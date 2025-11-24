import json
import os
import re
import time

from datetime import datetime
from threading import Lock
from typing import List
from urllib.parse import urlparse

import chromadb
import mistune as mt
import numpy as np
import psycopg2
import psycopg2.extras
import yaml
from chromadb.config import Settings
from flask import jsonify, render_template, request, session, flash, redirect, url_for
from flask_cors import CORS
from langchain_chroma.vectorstores import Chroma
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import (BashLexer, CLexer, CppLexer, FortranLexer,
                             HtmlLexer, JavaLexer, JavascriptLexer, JuliaLexer,
                             MathematicaLexer, MatlabLexer, PythonLexer,
                             TypeScriptLexer)

from src.a2rchi.a2rchi import A2rchi
from src.data_manager.data_manager import DataManager
from src.utils.config_loader import CONFIGS_PATH, get_config_names, load_config
from src.utils.env import read_secret
from src.utils.logging import get_logger
from src.utils.sql import SQL_INSERT_CONVO, SQL_INSERT_FEEDBACK, SQL_INSERT_TIMING, SQL_QUERY_CONVO, SQL_INSERT_CONFIG, SQL_CREATE_CONVERSATION, SQL_UPDATE_CONVERSATION_TIMESTAMP, SQL_LIST_CONVERSATIONS, SQL_GET_CONVERSATION_METADATA, SQL_DELETE_CONVERSATION
from src.data_manager.collectors.scrapers.scraper_manager import ScraperManager
from src.data_manager.collectors.persistence import PersistenceService
from src.data_manager.collectors.utils.index_utils import CatalogService
from src.interfaces.chat_app.document_utils import *


logger = get_logger(__name__)

# DEFINITIONS
QUERY_LIMIT = 10000 # max queries per conversation
MAIN_PROMPT_FILE = "/root/A2rchi/main.prompt"
CONDENSE_PROMPT_FILE = "/root/A2rchi/condense.prompt"
SUMMARY_PROMPT_FILE = "/root/A2rchi/summary.prompt"


class AnswerRenderer(mt.HTMLRenderer):
    """
    Class for custom rendering of A2rchi output. Child of mistune's HTMLRenderer, with custom overrides.
    Code blocks are structured and colored according to pygment lexers
    """
    RENDERING_LEXER_MAPPING = {
            "python": PythonLexer,
            "java": JavaLexer,
            "javascript": JavascriptLexer,
            "bash": BashLexer,
            "c++": CppLexer,
            "cpp": CppLexer,
            "c": CLexer,
            "typescript": TypeScriptLexer,
            "html": HtmlLexer,
            "fortran" : FortranLexer,
            "julia" : JuliaLexer,
            "mathematica" : MathematicaLexer,
            "matlab": MatlabLexer
        }

    def __init__(self):
        self.config = load_config()
        super().__init__()

    def block_code(self, code, info=None):
        # Handle code blocks (triple backticks)
        if info not in self.RENDERING_LEXER_MAPPING.keys(): info = 'bash' #defaults in bash
        code_block_highlighted = highlight(code.strip(), self.RENDERING_LEXER_MAPPING[info](stripall=True), HtmlFormatter())

        if self.config["services"]["chat_app"]["include_copy_button"]:
            button = """<button class="copy-code-btn" onclick="copyCode(this)"> Copy Code </button>"""
        else: button = ""

        return f"""<div class="code-box">
                <div class="code-box-header">
                <span>{info}</span>{button}
                </div>
                <div class="code-box-body">{code_block_highlighted}
                </div>
                </div>"""

    def codespan(self, text):
        # Handle inline code snippets (single backticks)
        return f"""<code class="code-snippet">{text}</code>"""


class ConversationAccessError(Exception):
    """Raised when a client attempts to access a conversation it does not own."""
    pass


class ChatWrapper:
    """
    Wrapper which holds functionality for the chatbot
    """
    def __init__(self):
        # load configs
        self.config = load_config()
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.services_config = self.config["services"]
        self.data_path = self.global_config["DATA_PATH"]

        # initialize data manager
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()
        embedding_name = self.config["data_manager"]["embedding_name"]
        self.similarity_score_reference = self.config["data_manager"]["embedding_class_map"][embedding_name]["similarity_score_reference"]
        self.sources_config = self.config["data_manager"]["sources"]

        # store postgres connection info
        self.pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **self.services_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        # initialize lock and chain
        self.lock = Lock()
        self.a2rchi = A2rchi(pipeline=self.config["services"]["chat_app"]["pipeline"])
        self.number_of_queries = 0

        # initialize config_id to be None
        self.config_id = None

    def update_config(self, config_id, config_name=None):
        self.config_id = config_id
        self.a2rchi.update(pipeline=self.config["services"]["chat_app"]["pipeline"], config_name=config_name)

    @staticmethod
    def convert_to_app_history(history):
        """
        Input: the history in the form of a list of tuples, where the first entry of each tuple is
        the author of the text and the second entry is the text itself (native A2rchi history format)

        Output: the history in the form of a list of lists, where the first entry of each tuple is
        the author of the text and the second entry is the text itself
        """
        return [list(entry) for entry in history]


    @staticmethod
    def format_code_in_text(text):
        """
        Takes in input plain text (the output from A2rchi);
        Recognizes structures in canonical Markdown format, and processes according to the custom renderer;
        Returns it formatted in HTML
        """

        enabled_plugins = ['table']
        markdown = mt.create_markdown(renderer=AnswerRenderer(), plugins=enabled_plugins)
        try:
            return markdown(text)
        except:
             logger.info("Rendering error: markdown formatting failed")
             return text

    def get_top_sources(self, documents, scores):
        """
        Build a list of top reference entries (link or ticket id).
        """
        if scores:
            sorted_indices = np.argsort(scores)
            scores = [scores[i] for i in sorted_indices]
            documents = [documents[i] for i in sorted_indices]

        top_sources = []
        seen_refs = set()
        pairs = zip(scores, documents) if scores else ((None, doc) for doc in documents)

        for score, document in pairs:
            # Skip threshold filtering for placeholder scores (-1)
            # Otherwise, filter out documents with score > threshold
            if score is not None and score != -1.0 and score > self.similarity_score_reference:
                logger.debug(f"Skipping document with score {score} above threshold {self.similarity_score_reference}")
                break

            metadata = document.metadata or {}

            display_name = self._get_display_name(metadata)
            if not display_name:
                continue

            if not self._get_doc_visibility(self, metadata):
                logger.debug(f"Document {display_name} marked as not visible; skipping.")
                continue

            link = self._extract_link(metadata)

            if display_name in seen_refs:
                continue
            seen_refs.add(display_name)

            top_sources.append(
                {
                    "link": link,
                    "display": display_name,
                    "score": score if score is not None else "N/A",
                }
            )

            if len(top_sources) >= 5:
                break

        logger.debug(f"Top sources: {top_sources}")
        return top_sources

    @staticmethod
    def format_links(top_sources):
        _output = ""

        if top_sources:
            _output += '''
            <div style="
                margin-top: 1.5em;
                padding-top: 0.5em;
                border-top: 1px solid rgba(255, 255, 255, 0.1);
                font-size: 0.75em;
                color: #adb5bd;
                line-height: 1.3;
            ">
                <div style="margin-bottom: 0.3em; font-weight: 500;">Sources:</div>
            '''

            for entry in top_sources:
                score = entry["score"]
                link = entry["link"]
                display_name = entry["display"]
                
                # Format score: show nothing for -1 (placeholder), otherwise show numeric value
                if score == -1.0 or score == "N/A":
                    score_str = ""
                else:
                    score_str = f"({score:.2f})"

                if link:
                    reference_html = f"<a href=\"{link}\" target=\"_blank\" rel=\"noopener noreferrer\" style=\"color: #66b3ff; text-decoration: none;\" onmouseover=\"this.style.textDecoration='underline'\" onmouseout=\"this.style.textDecoration='none'\">{display_name}</a>"
                else:
                    reference_html = f"<span style=\"color: #66b3ff;\">{display_name}</span>"

                _output += f'''
                    <div style="margin: 0.15em 0; display: flex; align-items: center; gap: 0.4em;">
                        <span>•</span>
                        {reference_html}
                        <span style="color: #6c757d; font-size: 0.9em;">{score_str}</span>
                    </div>
                '''

            _output += '</div>'

        return _output

    @staticmethod
    def _looks_like_url(value: str | None) -> bool:
        return isinstance(value, str) and value.startswith(("http://", "https://"))

    @staticmethod
    def _get_display_name(metadata: dict) -> str | None:
        display_name = metadata.get("display_name")
        if isinstance(display_name, str) and display_name.strip():
            return display_name.strip()
        else:
            logger.error("display_name is not a valid non-empty string in metadata")
            logger.error(f"Metadata content: {metadata}")
            return None

    @staticmethod
    def _get_title(metadata: dict) -> str | None:
        title = metadata.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
        else:
            logger.error("title is not a valid non-empty string in metadata")
            logger.error(f"Metadata content: {metadata}")
            return None

    @staticmethod
    def _get_doc_visibility(self, metadata: dict) -> bool:
        """
        From the metadata, check the source type.
        From the config, check if the source type is visible or not.
        """
        source_type = metadata.get("source_type")
        if not source_type:
            return True  # default to True if not specified

        if source_type not in self.sources_config:
            logger.error(f"Source type {source_type} not found in config, defaulting to visible")
            return True
        return bool(self.sources_config[source_type].get("visible", True))

    @staticmethod
    def _extract_link(metadata: dict) -> str | None:
        for key in ("url", "link", "href"):
            candidate = metadata.get(key)
            if ChatWrapper._looks_like_url(candidate):
                return candidate
        return None

    def insert_feedback(self, feedback):
        """
        Insert feedback from user for specific message into feedback table.
        """
        # construct insert_tup (mid, feedback_ts, feedback, feedback_msg, incorrect, unhelpful, inappropriate)
        insert_tup = (
            feedback['message_id'],
            feedback['feedback_ts'],
            feedback['feedback'],
            feedback['feedback_msg'],
            feedback['incorrect'],
            feedback['unhelpful'],
            feedback['inappropriate'],
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        self.cursor.execute(SQL_INSERT_FEEDBACK, insert_tup)
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None


    def query_conversation_history(self, conversation_id, client_id):
        """
        Return the conversation history as an ordered list of tuples. The order
        is determined by ascending message_id. Each tuple contains the sender and
        the message content
        """
        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()

        # ensure conversation belongs to client before querying
        self.cursor.execute(SQL_GET_CONVERSATION_METADATA, (conversation_id, client_id))
        metadata = self.cursor.fetchone()
        if metadata is None:
            self.cursor.close()
            self.conn.close()
            self.cursor, self.conn = None, None
            raise ConversationAccessError("Conversation does not exist for this client")

        # query conversation history
        self.cursor.execute(SQL_QUERY_CONVO, (conversation_id,))
        history = self.cursor.fetchall()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        return history

    def create_conversation(self, first_message: str, client_id: str) -> int:
        """
        Gets first message (activates a new conversation), and generates a title w/ first msg.
        (TODO: commercial ones use one-sentence summarizer to make the title)

        Returns: Conversation ID.

        """
        service = "Chatbot"
        title = first_message[:20] + ("..." if len(first_message) > 20 else "")
        now = datetime.now()
        
        version = os.getenv("APP_VERSION", "unknown")

        # title, created_at, last_message_at, version
        insert_tup = (title, now, now, client_id, version)

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        self.cursor.execute(SQL_CREATE_CONVERSATION, insert_tup)
        conversation_id = self.cursor.fetchone()[0]
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        logger.info(f"Created new conversation with ID: {conversation_id}")
        return conversation_id

    def update_conversation_timestamp(self, conversation_id: int, client_id: str):
        """
        Update the last_message_at timestamp for a conversation.
        last_message_at is used to reorder conversations in the UI (on vertical sidebar).
        """
        now = datetime.now()

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()

        # update timestamp
        self.cursor.execute(SQL_UPDATE_CONVERSATION_TIMESTAMP, (now, conversation_id, client_id))
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

    def prepare_context_for_storage(self, source_documents, scores):
        scores = scores or []
        num_retrieved_docs = len(source_documents)
        context = ""
        if num_retrieved_docs > 0:
            for k in range(num_retrieved_docs):
                document = source_documents[k]
                metadata = document.metadata or {}
                link_k = self._extract_link(metadata)
                if not link_k:
                    link_k = (
                        self._get_display_name(metadata)
                        or self._get_title(metadata)
                        or "link not available"
                    )
                multiple_newlines = r'\n{2,}'
                content = re.sub(multiple_newlines, '\n', document.page_content)
                # Safely get the score, use "N/A" if index is out of range
                score_display = scores[k] if k < len(scores) else "N/A"
                context += f"SOURCE {k+1}: {metadata.get('title', 'No Title')} ({link_k})\nSIMILARITY SCORE: {score_display}\n\n{content}\n\n\n\n"

        return context

    def insert_conversation(self, conversation_id, user_message, a2rchi_message, link, a2rchi_context, is_refresh=False) -> List[int]:
        """
        """
        logger.debug("Entered insert_conversation.")

        service = "Chatbot"
        # parse user message / a2rchi message
        user_sender, user_content, user_msg_ts = user_message
        a2rchi_sender, a2rchi_content, a2rchi_msg_ts = a2rchi_message

        # construct insert_tups
        insert_tups = (
            [
                # (service, conversation_id, sender, content, context, ts)
                (service, conversation_id, user_sender, user_content, '', '', user_msg_ts, self.config_id),
                (service, conversation_id, a2rchi_sender, a2rchi_content, link, a2rchi_context, a2rchi_msg_ts, self.config_id),
            ]
            if not is_refresh
            else [
                (service, conversation_id, a2rchi_sender, a2rchi_content, link, a2rchi_context, a2rchi_msg_ts, self.config_id),
            ]
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        psycopg2.extras.execute_values(self.cursor, SQL_INSERT_CONVO, insert_tups)
        self.conn.commit()
        message_ids = list(map(lambda tup: tup[0], self.cursor.fetchall()))

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        return message_ids

    def insert_timing(self, message_id, timestamps):
        """
        Store timing info to understand response profile.
        """
        logger.debug("Entered insert_timing.")

        # construct insert_tup
        insert_tup = (
            message_id,
            timestamps['client_sent_msg_ts'],
            timestamps['server_received_msg_ts'],
            timestamps['lock_acquisition_ts'],
            timestamps['vectorstore_update_ts'],
            timestamps['query_convo_history_ts'],
            timestamps['chain_finished_ts'],
            timestamps['a2rchi_message_ts'],
            timestamps['insert_convo_ts'],
            timestamps['finish_call_ts'],
            timestamps['server_response_msg_ts'],
            timestamps['server_response_msg_ts'] - timestamps['server_received_msg_ts']
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        self.cursor.execute(SQL_INSERT_TIMING, insert_tup)
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

    def __call__(self, message: List[str], conversation_id: int|None, client_id: str, is_refresh: bool, server_received_msg_ts: datetime,  client_sent_msg_ts: float, client_timeout: float, config_name: str):
        """
        Execute the chat functionality.
        """
        # store timestamps for code profiling information
        start_time = time.time()

        timestamps = {}

        self.lock.acquire()
        timestamps['lock_acquisition_ts'] = datetime.now()
        try:
            # update vector store through data manager; will only do something if newwhere files have been added
            logger.info("Acquired lock file update vectorstore")

            self.data_manager.update_vectorstore()
            timestamps['vectorstore_update_ts'] = datetime.now()

        except Exception as e:
            # NOTE: we log the error message but do not return here, as a failure
            # to update the data manager does not necessarily mean A2rchi cannot
            # process and respond to the message
            logger.error(f"Failed to update vectorstore - {str(e)}")

        finally:
            self.lock.release()
            logger.info("Released lock file update vectorstore")

        try:
            # convert the message to native A2rchi form (because javascript does not have tuples)
            sender, content = tuple(message[0])

            if not client_id:
                raise ValueError("client_id is required to process chat messages")

            # new conversation if conversation_id is None, otherwise use existing
            if conversation_id is None:
                conversation_id = self.create_conversation(content, client_id)
                history = []
            else:
                history = self.query_conversation_history(conversation_id, client_id)
                self.update_conversation_timestamp(conversation_id, client_id)

            timestamps['query_convo_history_ts'] = datetime.now()

            # if this is a chat refresh / message regeneration; remove previous contiuous non-A2rchi message(s)
            if is_refresh:
                while history[-1][0] == "A2rchi":
                    _ = history.pop(-1)

            # guard call to LLM; if timestamp from message is more than timeout secs in the past;
            # return error=True and do not generate response as the client will have timed out
            if server_received_msg_ts.timestamp() - client_sent_msg_ts > client_timeout:
                return None, None, None, timestamps, 408

            # run chain to get result; limit users to 1000 queries per conversation; refreshing browser starts new conversation
            if len(history) < QUERY_LIMIT:
                history = history + [(sender, content)] if not is_refresh else history
                self.update_config(config_id=self.config_id,config_name=config_name)
                result = self.a2rchi(history=history, conversation_id=conversation_id)
                timestamps['chain_finished_ts'] = datetime.now()
            else:
                # for now let's return a timeout error, as returning a different
                # error message would require handling new message_ids param. properly
                return None, None, None, timestamps, 500

            # keep track of total number of queries and log this amount
            self.number_of_queries += 1
            logger.info(f"Number of queries is: {self.number_of_queries}")

            # display answer
            output = self.format_code_in_text(result["answer"])


            # display sources (links or ticket references)
            documents = result.get("source_documents", [])
            scores = result.get("metadata", {}).get("retriever_scores", [])
            top_sources = self.get_top_sources(documents, scores)
            output += self.format_links(top_sources)

            # message is constructed!
            timestamps['a2rchi_message_ts'] = datetime.now()

            # formatting context
            context = self.prepare_context_for_storage(documents, scores)

            best_reference = "Link unavailable"
            if top_sources:
                primary_source = top_sources[0]
                best_reference = primary_source["link"] or primary_source["display"]

            # and now finally insert the conversation
            user_message = (sender, content, server_received_msg_ts)
            a2rchi_message = ("A2rchi", output, timestamps['a2rchi_message_ts'])
            message_ids = self.insert_conversation(
                conversation_id,
                user_message,
                a2rchi_message,
                best_reference,
                context,
                is_refresh
            )
            timestamps['insert_convo_ts'] = datetime.now()

        except ConversationAccessError as e:
            logger.warning(f"Unauthorized conversation access attempt: {e}")
            return None, None, None, timestamps, 403
        except Exception as e:
            # NOTE: we log the error message and return here
            logger.error(f"Failed to produce response: {e}", exc_info=True)
            return None, None, None, timestamps, 500

        finally:
            if self.cursor is not None:
                self.cursor.close()
            if self.conn is not None:
                self.conn.close()

        timestamps['finish_call_ts'] = datetime.now()

        return output, conversation_id, message_ids, timestamps, None


class FlaskAppWrapper(object):

    def __init__(self, app, **configs):
        logger.info("Entering FlaskAppWrapper")
        self.app = app
        self.configs(**configs)
        self.config = load_config()
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.services_config = self.config["services"]
        self.chat_app_config = self.config["services"]["chat_app"]
        self.data_path = self.global_config["DATA_PATH"]
        self.persistence = PersistenceService(self.data_path)
        self.catalog = CatalogService(self.data_path)

        self.salt = read_secret("UPLOADER_SALT")
        self.app.secret_key = read_secret("FLASK_UPLOADER_APP_SECRET_KEY")
        self.app.config['UPLOAD_FOLDER'] = os.path.join(self.data_path, "manual_uploads")
        self.app.config['WEBSITE_FOLDER'] = os.path.join(self.data_path, "manual_websites")
        self.app.config['ACCOUNTS_FOLDER'] = self.global_config["ACCOUNTS_PATH"]
        self.app.config['WEBLISTS_FOLDER'] = os.path.join(self.data_path, "websites")

        # create upload and accounts folders if they don't already exist
        os.makedirs(self.app.config['UPLOAD_FOLDER'], exist_ok=True)
        os.makedirs(self.app.config['WEBSITE_FOLDER'], exist_ok=True)
        os.makedirs(self.app.config['ACCOUNTS_FOLDER'], exist_ok=True)

        # create path specifying URL sources for scraping
        self.sources_path = os.path.join(self.data_path, 'index.yaml')
        self.scraper_manager = ScraperManager()

        # store postgres connection info
        self.pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **self.services_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        # insert config
        self.config_id = self.insert_config(self.config)

        # create the chat from the wrapper
        self.chat = ChatWrapper()
        self.chat.update_config(self.config_id)

        # enable CORS:
        CORS(self.app)

        # add endpoints for flask app
        self.add_endpoint('/api/get_chat_response', 'get_chat_response', self.get_chat_response, methods=["POST"])
        self.add_endpoint('/', '', self.index)
        self.add_endpoint('/terms', 'terms', self.terms)
        self.add_endpoint('/api/like', 'like', self.like,  methods=["POST"])
        self.add_endpoint('/api/dislike', 'dislike', self.dislike,  methods=["POST"])
        self.add_endpoint('/api/update_config', 'update_config', self.update_config, methods=["POST"])
        self.add_endpoint('/api/health', 'health', self.health, methods=["GET"])
        self.add_endpoint('/api/get_configs', 'get_configs', self.get_configs, methods=["GET"])

        # conditionally add ChromaDB endpoints based on config
        if self.chat_app_config.get('enable_debug_chroma_endpoints', False):
            logger.info("Adding ChromaDB API endpoints (list_docs, search_docs)")
            self.add_endpoint('/api/list_docs', 'list_docs', self.list_docs, methods=["GET"])
            self.add_endpoint('/api/search_docs', 'search_docs', self.search_docs, methods=["POST"])
        else:
            logger.info("ChromaDB API endpoints disabled by config")

        # endpoints for conversations managing
        logger.info("Adding conversations management API endpoints")
        self.add_endpoint('/api/list_conversations', 'list_conversations', self.list_conversations, methods=["GET"])
        self.add_endpoint('/api/load_conversation', 'load_conversation', self.load_conversation, methods=["POST"])
        self.add_endpoint('/api/new_conversation', 'new_conversation', self.new_conversation, methods=["POST"])
        self.add_endpoint('/api/delete_conversation', 'delete_conversation', self.delete_conversation, methods=["POST"])

        # add endpoints for document_index
        self.add_endpoint('/document_index/', 'document_index', self.document_index)
        self.add_endpoint('/document_index/index', 'index', self.document_index)
        self.add_endpoint('/document_index/login', 'login', self.login, methods=['GET', 'POST'])
        self.add_endpoint('/document_index/logout', 'logout', self.logout)
        self.add_endpoint('/document_index/upload', 'upload', self.upload, methods=['POST'])
        self.add_endpoint('/document_index/delete/<file_hash>', 'delete', self.delete)
        self.add_endpoint('/document_index/delete_source/<source_type>', 'delete_source', self.delete_source)
        self.add_endpoint('/document_index/upload_url', 'upload_url', self.upload_url, methods=['POST'])
        self.add_endpoint('/document_index/load_document/<path:file_hash>', 'load_document', self.load_document)

    def health(self):
        return jsonify({"status": "OK"}, 200)

    def configs(self, **configs):
        for config, value in configs:
            self.app.config[config.upper()] = value

    def add_endpoint(self, endpoint = None, endpoint_name = None, handler = None, methods = ['GET'], *args, **kwargs):
        self.app.add_url_rule(endpoint, endpoint_name, handler, methods = methods, *args, **kwargs)

    def run(self, **kwargs):
        self.app.run(**kwargs)

    def insert_config(self, config):
        # TODO: use config_name (and then hash of config string) to determine
        #       if config already exists; if so, don't push new config

        # parse config and config_name
        config_name = self.config["name"]
        config = yaml.dump(self.config)

        # construct insert_tup
        insert_tup = [
            (config, config_name),
        ]

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        psycopg2.extras.execute_values(self.cursor, SQL_INSERT_CONFIG, insert_tup)
        self.conn.commit()
        config_id = list(map(lambda tup: tup[0], self.cursor.fetchall()))[0]

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None

        return config_id

    def update_config(self):
        """
        Updates the config used by A2rchi for responding to messages. The config
        is parsed and inserted into the `configs` table. Finally, the chat wrapper's
        config_id is updated.
        """
        # parse config and write it out to CONFIGS_PATH
        config_str = request.json.get('config')
        config_name = config_str['name']
        with open(CONFIGS_PATH+f'{config_name}.yaml', 'w') as f:
            f.write(config_str)

        # parse prompts and write them to their respective locations
        # TODO fix
        main_prompt = request.json.get('main_prompt')
        with open(MAIN_PROMPT_FILE, 'w') as f:
            f.write(main_prompt)

        condense_prompt = request.json.get('condense_prompt')
        with open(CONDENSE_PROMPT_FILE, 'w') as f:
            f.write(condense_prompt)

        summary_prompt = request.json.get('summary_prompt')
        with open(SUMMARY_PROMPT_FILE, 'w') as f:
            f.write(summary_prompt)

        # re-read config using load_config and update dependent variables
        self.config = load_config()
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.services_config = self.config["services"]
        self.chat_app_config = self.config["services"]["chat_app"]
        self.data_path = self.global_config["DATA_PATH"]

        # store postgres connection info
        self.pg_config = {
            "password": read_secret("PG_PASSWORD"),
            **self.services_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        # insert config
        self.config_id = self.insert_config(self.config)

        # create the chat from the wrapper
        self.chat = ChatWrapper()
        self.chat.update_config(self.config_id)

        return jsonify({'response': f'config updated successfully w/config_id: {self.config_id}'}), 200

    def get_configs(self):
        """
        Gets the names of configs loaded in A2rchi.


        Returns:
            A json with a response list of the configs names
        """

        config_names = get_config_names()
        return jsonify({'options':config_names}), 200


    def get_chat_response(self):
        """
        Gets a response when prompted. Asks as an API to the main app, who's
        functionality is carried through by javascript and html. Input is a
        requestion with

            conversation_id: Either None or an integer
            last_message:    list of length 2, where the first element is "User"
                             and the second element contains their message.

        Returns:
            A json with a response (html formatted plain text string) and a
            discussion ID (either None or an integer)
        """
        # compute timestamp at which message was received by server
        start_time = time.time()
        server_received_msg_ts = datetime.now()

        # get user input and conversation_id from the request
        message = request.json.get('last_message')
        conversation_id = request.json.get('conversation_id')
        config_name = request.json.get('config_name')
        is_refresh = request.json.get('is_refresh')
        client_sent_msg_ts = request.json.get('client_sent_msg_ts') / 1000
        client_timeout = request.json.get('client_timeout') / 1000
        client_id = request.json.get('client_id')

        if not client_id:
            return jsonify({'error': 'client_id missing'}), 400

        # query the chat and return the results.
        logger.debug("Calling the ChatWrapper()")
        response, conversation_id, message_ids, timestamps, error_code = self.chat(message, conversation_id, client_id, is_refresh, server_received_msg_ts, client_sent_msg_ts, client_timeout,config_name)

        # handle errors
        if error_code is not None:
            if error_code == 408:
                output = jsonify({'error': 'client timeout'})
            elif error_code == 403:
                output = jsonify({'error': 'conversation not found'})
            else:
                output = jsonify({'error': 'server error; see chat logs for message'})
            return output, error_code

        # compute timestamp at which message was returned to client
        timestamps['server_response_msg_ts'] = datetime.now()

        # store timing info for this message
        timestamps['server_received_msg_ts'] = server_received_msg_ts
        timestamps['client_sent_msg_ts'] = datetime.fromtimestamp(client_sent_msg_ts)
        self.chat.insert_timing(message_ids[-1], timestamps)

        # otherwise return A2rchi's response to client
        try:
            response_size = len(response) if isinstance(response, str) else 0
            logger.info(f"Generated Response Length: {response_size} characters")
            json.dumps({'response': response})  # Validate JSON formatting
        except Exception as e:
            logger.error(f"JSON Encoding Error: {e}")
            response = "Error processing response"

        response_data = {
            'response': response,
            'conversation_id': conversation_id,
            'a2rchi_msg_id': message_ids[-1],
            'server_response_msg_ts': timestamps['server_response_msg_ts'].timestamp(),
            'final_response_msg_ts': datetime.now().timestamp(),
        }

        end_time = time.time()
        logger.info(f"API Response Time: {end_time - start_time:.2f} seconds")

        return jsonify(response_data)

    def index(self):
        return render_template('index.html')

    def terms(self):
        return render_template('terms.html')

    def like(self):
        self.chat.lock.acquire()
        logger.info("Acquired lock file")
        try:
            # Get the JSON data from the request body
            data = request.json

            # Extract the HTML content and any other data you need
            message_id = data.get('message_id')

            feedback = {
                "message_id"   : message_id,
                "feedback"     : "like",
                "feedback_ts"  : datetime.now(),
                "feedback_msg" : None,
                "incorrect"    : None,
                "unhelpful"    : None,
                "inappropriate": None,
            }
            self.chat.insert_feedback(feedback)

            response = {'message': 'Liked'}
            return jsonify(response), 200

        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            return jsonify({'error': str(e)}), 500

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
            logger.info("Released lock file")

            if self.chat.cursor is not None:
                self.chat.cursor.close()
            if self.chat.conn is not None:
                self.chat.conn.close()

    def dislike(self):
        self.chat.lock.acquire()
        logger.info("Acquired lock file")
        try:
            # Get the JSON data from the request body
            data = request.json

            # Extract the HTML content and any other data you need
            message_id = data.get('message_id')
            feedback_msg = data.get('feedback_msg')
            incorrect = data.get('incorrect')
            unhelpful = data.get('unhelpful')
            inappropriate = data.get('inappropriate')

            feedback = {
                "message_id"   : message_id,
                "feedback"     : "dislike",
                "feedback_ts"  : datetime.now(),
                "feedback_msg" : feedback_msg,
                "incorrect"    : incorrect,
                "unhelpful"    : unhelpful,
                "inappropriate": inappropriate,
            }
            self.chat.insert_feedback(feedback)

            response = {'message': 'Disliked'}
            return jsonify(response), 200

        except Exception as e:
            logger.error(f"Request failed: {str(e)}")
            return jsonify({'error': str(e)}), 500

        # According to the Python documentation: https://docs.python.org/3/tutorial/errors.html#defining-clean-up-actions
        # this will still execute, before the function returns in the try or except block.
        finally:
            self.chat.lock.release()
            logger.info("Released lock file")

            if self.chat.cursor is not None:
                self.chat.cursor.close()
            if self.chat.conn is not None:
                self.chat.conn.close()

    def list_docs(self):
        """
        API endpoint to list all documents indexed in ChromaDB with pagination.
        Query parameters:
        - page: Page number (1-based, default: 1)
        - per_page: Documents per page (default: 50, max: 500)
        - content_length: Max content preview length (default: -1 for full content)
        Returns a JSON with paginated list of documents and their metadata.
        """
        # Check if ChromaDB endpoints are enabled
        if not self.chat_app_config.get('enable_debug_chroma_endpoints', False):
            return jsonify({'error': 'ChromaDB endpoints are disabled in configuration'}), 404

        try:
            # Get pagination parameters from query string
            page = int(request.args.get('page', 1))
            per_page = min(int(request.args.get('per_page', 50)), 500)  # Cap at 500
            content_length = int(request.args.get('content_length', -1))  # Default -1 for full content

            # Validate parameters
            if page < 1:
                return jsonify({'error': 'Page must be >= 1'}), 400
            if per_page < 1:
                return jsonify({'error': 'per_page must be >= 1'}), 400
            if content_length < -1 or content_length == 0:
                return jsonify({'error': 'content_length must be -1 (full content) or > 0'}), 400

            # Get the collection from ChromaDB
            collection = self.chat.data_manager.fetch_collection()

            # Get total count first
            total_documents = collection.count()

            # Calculate pagination
            offset = (page - 1) * per_page
            total_pages = (total_documents + per_page - 1) // per_page  # Ceiling division

            # Check if page is valid
            if page > total_pages and total_documents > 0:
                return jsonify({'error': f'Page {page} does not exist. Total pages: {total_pages}'}), 400

            # Get paginated documents from the collection
            result = collection.get(
                include=['documents', 'metadatas'],
                limit=per_page,
                offset=offset
            )

            # Format the response
            documents = []
            for i, doc in enumerate(result['documents']):
                # Truncate content based on content_length parameter (-1 means full content)
                if content_length == -1:
                    content = doc  # Return full content
                else:
                    content = doc[:content_length] + '...' if len(doc) > content_length else doc

                doc_info = {
                    'id': result['ids'][i],
                    'content': content,
                    'content_length': len(doc),  # Original content length
                    'metadata': result['metadatas'][i] if i < len(result['metadatas']) else {}
                }
                documents.append(doc_info)

            response_data = {
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total_documents': total_documents,
                    'total_pages': total_pages,
                    'has_next': page < total_pages,
                    'has_prev': page > 1,
                    'next_page': page + 1 if page < total_pages else None,
                    'prev_page': page - 1 if page > 1 else None
                },
                'documents': documents
            }

            return jsonify(response_data), 200

        except ValueError as e:
            return jsonify({'error': f'Invalid parameter: {str(e)}'}), 400
        except Exception as e:
            print(f"ERROR in list_docs: {str(e)}")
            return jsonify({'error': str(e)}), 500

    # TODO should this call a2rchi rather than connect to db directly?
    # in any case, code-duplication should be elminated here
    def search_docs(self):
        """
        API endpoint to search for the nearest documents to a given query with pagination.
        Expects JSON input with:
        - query (required): Search query string
        - n_results (optional): Number of results to return (default: 5, max: 100)
        - content_length (optional): Max content length in response (default: -1 for full content, max: 5000)
        - include_full_content (optional): Whether to include full content (default: false)
        Returns the most similar documents with their similarity scores.
        """
        # Check if ChromaDB endpoints are enabled
        if not self.chat_app_config.get('enable_debug_chroma_endpoints', False):
            return jsonify({'error': 'ChromaDB endpoints are disabled in configuration'}), 404

        try:
            # Get the query from request
            data = request.json
            query = data.get('query')
            n_results = min(int(data.get('n_results', 5)), 100)  # Cap at 100
            content_length = min(int(data.get('content_length', -1)), 5000) if data.get('content_length', -1) != -1 else -1  # Default -1 for full content
            include_full_content = data.get('include_full_content', False)

            if not query:
                return jsonify({'error': 'Query parameter is required'}), 400

            if n_results < 1:
                return jsonify({'error': 'n_results must be >= 1'}), 400

            if content_length < -1 or content_length == 0:
                return jsonify({'error': 'content_length must be -1 (full content) or > 0'}), 400

            # Connect to ChromaDB and create vectorstore
            client = None
            if self.services_config["chromadb"]["use_HTTP_chromadb_client"]:
                client = chromadb.HttpClient(
                    host=self.services_config["chromadb"]["chromadb_host"],
                    port=self.services_config["chromadb"]["chromadb_port"],
                    settings=Settings(allow_reset=True, anonymized_telemetry=False),
                )
            else:
                client = chromadb.PersistentClient(
                    path=self.global_config["LOCAL_VSTORE_PATH"],
                    settings=Settings(allow_reset=True, anonymized_telemetry=False),
                )

            # Get the collection name and embedding model from chat
            collection_name = self.chat.chain.collection_name
            embedding_model = self.chat.chain.embedding_model

            # Create vectorstore
            vectorstore = Chroma(
                client=client,
                collection_name=collection_name,
                embedding_function=embedding_model,
            )

            # Perform similarity search with scores
            results = vectorstore.similarity_search_with_score(query, k=n_results)

            # Format the response
            documents = []
            for doc, score in results:
                # Handle content length based on parameters
                if include_full_content or content_length == -1:
                    content = doc.page_content
                else:
                    content = (doc.page_content[:content_length] + '...'
                             if len(doc.page_content) > content_length
                             else doc.page_content)

                doc_info = {
                    'content': content,
                    'content_length': len(doc.page_content),  # Original content length
                    'metadata': doc.metadata,
                    'similarity_score': float(score)
                }
                documents.append(doc_info)

            response_data = {
                'query': query,
                'search_params': {
                    'n_results_requested': n_results,
                    'n_results_returned': len(documents),
                    'content_length': content_length,
                    'include_full_content': include_full_content
                },
                'documents': documents
            }

            # Clean up
            del vectorstore
            del client

            return jsonify(response_data), 200

        except ValueError as e:
            return jsonify({'error': f'Invalid parameter: {str(e)}'}), 400
        except Exception as e:
            print(f"ERROR in search_docs: {str(e)}")
            return jsonify({'error': str(e)}), 500

    def list_conversations(self):
        """
        List all conversations, ordered by most recent first.

        Query parameters:
        - limit (optional): Number of conversations to return (default: 50, max: 500)

        Returns:
            JSON with list of conversations with fields: (conversation_id, title, created_at, last_message_at).
        """
        try:
            client_id = request.args.get('client_id')
            if not client_id:
                return jsonify({'error': 'client_id missing'}), 400
            limit = min(int(request.args.get('limit', 50)), 500)

            # create connection to database
            conn = psycopg2.connect(**self.pg_config)
            cursor = conn.cursor()
            cursor.execute(SQL_LIST_CONVERSATIONS, (client_id, limit))
            rows = cursor.fetchall()

            conversations = []
            for row in rows:
                conversations.append({
                    'conversation_id': row[0],
                    'title': row[1] or "New Chat",
                    'created_at': row[2].isoformat() if row[2] else None,
                    'last_message_at': row[3].isoformat() if row[3] else None,
                })

            # clean up database connection state
            cursor.close()
            conn.close()

            return jsonify({'conversations': conversations}), 200

        except ValueError as e:
            return jsonify({'error': f'Invalid parameter: {str(e)}'}), 400
        except Exception as e:
            print(f"ERROR in list_conversations: {str(e)}")
            return jsonify({'error': str(e)}), 500

    def load_conversation(self):
        """
        Load a specific conversation's full history.

        POST body:
        - conversation_id: The ID of the conversation to load

        Returns:
            JSON with conversation metadata and full message history
        """
        try:
            data = request.json
            conversation_id = data.get('conversation_id')
            client_id = data.get('client_id')

            if not conversation_id:
                return jsonify({'error': 'conversation_id missing'}), 400
            if not client_id:
                return jsonify({'error': 'client_id missing'}), 400

            # create connection to database
            conn = psycopg2.connect(**self.pg_config)
            cursor = conn.cursor()

            # get conversation metadata
            cursor.execute(SQL_GET_CONVERSATION_METADATA, (conversation_id, client_id))
            meta_row = cursor.fetchone()

            # if no metadata found, return error
            if not meta_row:
                cursor.close()
                conn.close()
                return jsonify({'error': 'conversation not found'}), 404

            # get history of the conversation
            cursor.execute(SQL_QUERY_CONVO, (conversation_id, ))
            history_rows = cursor.fetchall()

            conversation = {
                'conversation_id': meta_row[0],
                'title': meta_row[1] or "New Conversation",
                'created_at': meta_row[2].isoformat() if meta_row[2] else None,
                'last_message_at': meta_row[3].isoformat() if meta_row[3] else None,
                'messages': [
                    {'sender': row[0], 'content': row[1]}
                    for row in history_rows
                ]
            }

            # clean up database connection state
            cursor.close()
            conn.close()

            return jsonify(conversation), 200

        except Exception as e:
            logger.error(f"Error in load_conversation: {str(e)}")
            return jsonify({'error': str(e)}), 500

    def new_conversation(self):
        """
        Start a new conversation without sending a message yet.
        This simply returns null(Conversation ID == None) to indicate that the frontend should
        reset its conversation_id, and a new one will be created on first message.

        Returns:
            JSON with conversation_id == None
        """
        try:
            # return null to indicate a new conversation
            # actual conversation will be created when the first message is sent
            return jsonify({'conversation_id': None}), 200

        except Exception as e:
            logger.error(f"Error in new_conversation: {str(e)}")
            return jsonify({'error': str(e)}), 500

    def delete_conversation(self):
        """
        Delete a conversation and all its messages. (Using SQL CASCADE)

        POST body:
        - conversation_id: The ID of the conversation to delete

        Returns:
            JSON with success status
        """
        try:
            data = request.json
            conversation_id = data.get('conversation_id')
            client_id = data.get('client_id')

            if not conversation_id:
                return jsonify({'error': 'conversation_id missing when deleting.'}), 400
            if not client_id:
                return jsonify({'error': 'client_id missing when deleting.'}), 400

            # create connection to database
            conn = psycopg2.connect(**self.pg_config)
            cursor = conn.cursor()

            # Delete conversation metadata (SQL CASCADE will delete all child messages)
            cursor.execute(SQL_DELETE_CONVERSATION, (conversation_id, client_id))
            deleted_count = cursor.rowcount
            conn.commit()

            # clean up database connection state
            cursor.close()
            conn.close()

            if deleted_count == 0:
                return jsonify({'error': 'Conversation not found'}), 404

            logger.info(f"Deleted conversation {conversation_id}")
            return jsonify({'success': True, 'deleted_conversation_id': conversation_id}), 200

        except ValueError as e:
            return jsonify({'error': f'Invalid parameter: {str(e)}'}), 400
        except Exception as e:
            print(f"ERROR in delete_conversation: {str(e)}")
            return jsonify({'error': str(e)}), 500

    def is_authenticated(self):
        """
        Keeps the state of the authentication.

        Returns true if there has been a correct login authentication and false otherwise.
        """
        return 'logged_in' in session and session['logged_in']

    #@app.route('/document_index/login', methods=['GET', 'POST'])
    def login(self):
        """
        Method which governs the logging into the system. Relies on check_credentials function
        """
        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']

            if check_credentials(username, password, self.salt, self.app.config['ACCOUNTS_FOLDER']):
                session['logged_in'] = True
                return redirect(url_for('index'))
            else:
                flash('Invalid credentials')

        return render_template('login.html')


    #@app.route('/document_index/logout')
    def logout(self):
        """
        Method which is responsible for logout

        This method is never explictly called, login sessions
        are stored in the cookies.
        """
        session.pop('logged_in', None)

        return redirect(url_for('login'))


    #@app.route('/document_index/')
    def document_index(self):
        """
        Methods which gets all the filenames in the UPLOAD_FOLDER and lists them
        in the UI.

        Note, this method must convert the file hashes (which is the name the files)
        are stored under in the filesystem) to file names. It uses get_filename_from_hash
        for this.
        """
        if not self.is_authenticated():
            return redirect(url_for('login'))

        sources_index = {}

        for source_hash in self.catalog.metadata_index.keys():
            metadata_source = self.catalog.get_metadata_for_hash(source_hash)
            if not isinstance(metadata_source, dict):
                logger.info("Metadata for hash %s missing or invalid; skipping", source_hash)
                continue

            source_type = metadata_source.get("source_type")
            if not source_type:
                logger.info("Metadata for hash %s missing source_type; skipping", source_hash)
                continue

            title = metadata_source.get("ticket_id") or metadata_source.get("url")
            if not title:
                title = metadata_source.get("display_name") or source_hash

            sources_index.setdefault(source_type, []).append((source_hash, title))


        return render_template('document_index.html', sources_index=sources_index.items())


    #@app.route('/document_index/upload', methods=['POST'])
    def upload(self):
        """
        Methods which governs uploading.

        Does not allow uploading if the file is not of a valid file type or if the file
        already exists in the filesystem.
        """
        if not self.is_authenticated():
            return redirect(url_for('login'))

        # check that there is a file selected and that the name is not null
        if 'file' not in request.files:
            flash('No file part')
            return redirect(url_for('index'))

        file = request.files['file']
        if file.filename == '':
            flash('No selected file')
            return redirect(url_for('index'))

        # check it is a valid file
        file_extension = os.path.splitext(file.filename)[1]
        if file and file_extension in self.global_config["ACCEPTED_FILES"]:

            try:
                resource = add_uploaded_file(target_dir=self.app.config['UPLOAD_FOLDER'],file=file, file_extension=file_extension)
                self.scraper_manager.register_resource(target_dir=Path(self.app.config['UPLOAD_FOLDER']),resource=resource)
                flash('File uploaded successfully')
            except Exception:
                flash(f'File under this name already exists. If you would like to upload a new file, please delete the old one.')

        return redirect(url_for('index'))


    #@app.route('/document_index/delete/<file_hash>')
    def delete(self, file_hash):
        """
        Method which governs deleting

        Technically can handle edge case where the file which is trying to be deleted
        is not in the filesystem.
        """
        self.persistence.delete_resource(file_hash)
        return redirect(url_for('index'))

    #@app.route('/document_index/delete_source/<source_type>')
    def delete_source(self, source_type):
        """
        Method to delete all documents of a specific source type
        """

        self.persistence.delete_by_metadata_filter("source_type", source_type)
        return redirect(url_for('index'))

    #@app.route('/document_index/upload_url', methods=['POST'])
    def upload_url(self):
        if not self.is_authenticated():
            return redirect(url_for('login'))

        url = request.form.get('url')
        if url:
            logger.info(f"Uploading the following URL: {url}")
            try:
                target_dir = Path(self.app.config['WEBSITE_FOLDER'])
                resources = self.scraper_manager.web_scraper.scrape(url)
                for resource in resources:
                    self.scraper_manager.register_resource(target_dir, resource)
                self.scraper_manager.persist_sources()
                added_to_urls = True

            except Exception as e:
                logger.error(f"Failed to upload URL: {str(e)}")
                added_to_urls = False

            if added_to_urls:
                flash('URL uploaded successfully')
            else:
                flash('Failed to add URL')
        else:
            flash('No URL provided')

        return redirect(url_for('index'))


    #@app.route('/document_index/load_document/<path:file_hash>')
    def load_document(self, file_hash):

        index = self.catalog.file_index
        if file_hash in index.keys():
            document = self.catalog.get_document_for_hash(file_hash)
            metadata = self.catalog.get_metadata_for_hash(file_hash)

            title = metadata['title'] if 'title' in metadata.keys() else metadata['display_name']
            return jsonify({'document':document,
                            'display_name':metadata['display_name'],
                            'source_type':metadata['source_type'],
                            'original_url':metadata['url'],
                            'title':title})

        else:
            return jsonify({'document':"Document not found",
                            'display_name':"Error",
                            'source_type':'null',
                            'original_url':"no_url",
                            'title':'Not found'})
