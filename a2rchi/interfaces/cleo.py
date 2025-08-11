from a2rchi.utils.config_loader import load_config, CONFIG_PATH
from a2rchi.chains.chain import Chain
from a2rchi.utils import sender
from a2rchi.utils.data_manager import DataManager
from a2rchi.utils.env import read_secret
from a2rchi.utils.logging import get_logger

from redminelib import Redmine

import datetime
import psycopg2
import psycopg2.extras
import os
import re
import yaml

from a2rchi.utils.sql import SQL_INSERT_CONVO

logger = get_logger(__name__)

# DEFINITIONS
A2RCHI_PATTERN = '-- A2rchi --'


class CleoAIWrapper:
    """
    Wrapper which holds functionality for the cleobot. Way of interaction
    between cleo and A2rchi core.
    """

    def __init__(self):
        self.chain = Chain()

        # initialize data manager
        self.data_manager = DataManager()
        self.data_manager.update_vectorstore()

        # configs
        self.config = load_config()
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.data_path = self.global_config["DATA_PATH"]

        # postgres connection info
        self.pg_config = {
            "password": read_secret("POSTGRES_PASSWORD"),
            **self.utils_config["postgres"],
        }
        self.conn = None
        self.cursor = None

        self.config_id = 1 # TODO: make dynamic a la chat_app/app.py

    def prepare_context_for_storage(self, source_documents):
        
        # load the present list of sources
        try:
            with open(os.path.join(self.data_path, 'sources.yml'), 'r') as file:
                sources = yaml.load(file, Loader=yaml.FullLoader)
        except FileNotFoundError:
            sources = dict()

        num_retrieved_docs = len(source_documents)
        context = ""
        if num_retrieved_docs > 0:
            for k in range(num_retrieved_docs):
                document = source_documents[k]
                document_source_hash = document.metadata['source']
                if '/' in document_source_hash and '.' in document_source_hash:
                    document_source_hash = document_source_hash.split('/')[-1].split('.')[0]
                link_k = "link not available"
                if document_source_hash in sources:
                    link_k = sources[document_source_hash]
                if k == 0:
                    link = link_k
                multiple_newlines = r'\n{2,}'
                content = re.sub(multiple_newlines, '\n', document.page_content)
                context += f"Source {k+1}: {document.metadata.get('title', 'No Title')} ({link_k})\n\n{content}\n\n\n\n"

        return link, context

    def insert_conversation(self, issue_id, user_message, a2rchi_message, link, a2rchi_context, ts):
        logger.info("Storing interaction to postgres")

        service = "Cleo"

        insert_tups = (
            [
                # (service, issue_id, sender, content, context, ts) -- same ts for both just to have, not as interested in timing info for cleo service...
                (service, issue_id, "User", user_message, '', '', ts, self.config_id),
                (service, issue_id, "A2rchi", a2rchi_message, link, a2rchi_context, ts, self.config_id),
            ]
        )

        # create connection to database
        self.conn = psycopg2.connect(**self.pg_config)
        self.cursor = self.conn.cursor()
        psycopg2.extras.execute_values(self.cursor, SQL_INSERT_CONVO, insert_tups)
        self.conn.commit()

        # clean up database connection state
        self.cursor.close()
        self.conn.close()
        self.cursor, self.conn = None, None


    def __call__(self, history, issue_id):
        # create formatted history
        reformatted_history = []
        for entry in history:
            if "ISSUE_ID:" in entry[1]:
                role = "Expert"
            else:
                role = "A2rchi"
            message = CleoAIWrapper.get_substring_between(entry[1],"\n\nRe:","\r\nOn ")
            reformatted_history.append((role,message))
        reformatted_history[0] = ("Expert", reformatted_history[0][1])
        reformatted_history[-1] = ("User", reformatted_history[-1][1])

        # update vectorstore
        self.data_manager.update_vectorstore()

        # execute chain and get answer
        result = self.chain(reformatted_history)
        answer = result["answer"]

        # prepare other information for storage
        history = "Question: " + reformatted_history[-1][1] + "\n\n\n\nHistory:\n\n" + "\n\n".join(post[0] + ": " + post[1] for post in reversed(reformatted_history[:-1]))
        link, context = self.prepare_context_for_storage(result['source_documents'])
        ts = datetime.datetime.now()

        self.insert_conversation(issue_id, history, answer, link, context, ts)
        
        return answer

    @staticmethod
    def get_substring_between(text, start_word, end_word):
        """
        Small helper function. Return everything (not including) between the 
        start_word and the end_word if start_word and end_word exist. Otherwise
        it does nothing
        """
        start_index = text.find(start_word)
        end_index = text.find(end_word)

        if start_index != -1 and end_index != -1 and start_index < end_index:
            return text[start_index + len(start_word):end_index].strip()
        else:
            return text


class Cleo:
    'A class to describe the cleo redmine system.'

    def __init__(self, name):
        """
        Give it a name and generate a conncetion to the database (should be a singleton).
        """
        self.name = name             # to identify
        self.redmine = None
        self.smtp = sender.Sender()
        self.user = None
        self.project = None
        self.ai_wrapper = CleoAIWrapper()

        # read environment variables from secrets
        self.cleo_project = read_secret("CLEO_PROJECT")
        self.cleo_url = read_secret("CLEO_URL")
        self.cleo_user = read_secret("CLEO_USER")
        self.cleo_pw = read_secret("CLEO_PW")

        # make sure to open redmine access
        if self._verify():
            self.redmine = self._connect()
            self.user = self.redmine.user.get('current')
            self.load()

        # Load all the status, tracker, and priority ids
        statuses = self.redmine.issue_status.all()
        self.status_dict = dict()     # keys = status name, values = status_id
        for s in statuses:
            self.status_dict[s.name] = s.id

        trackers = self.redmine.tracker.all()
        self.tracker_dict = dict()
        for t in trackers:
            self.tracker_dict[t.name] = t.id

        priorities = self.redmine.enumeration.filter(resource="issue_priorities")
        self.priorities_dict = dict()
        for p in priorities:
            self.priorities_dict[p.name] = p.id


            
    def add_note_to_issue(self,issue_id,note):
        """
        Adding a note to an existing issue (and move to 'feedback' status)
        """
        self.redmine.issue.update(issue_id,status_id = self.status_dict['Feedback'],notes = note)
        return

    def reopen_issue(self, issue_id, note,attachments):
        """
        Move an issues status to `In Progress` and add a note
        """
        self.redmine.issue.update(issue_id,status_id = self.status_dict['In Progress'],
                                  notes = note,uploads = attachments)
        return
    
    def get_issue_history(self,issue_id):
        """
        Extract a tuple of author and notes for this ticket
        """
        issue = self.redmine.issue.get(issue_id)
        history = [("User:", "<b>" + issue.subject + "</b> \n" + issue.description )]
        for record in issue.journals:
            user = self.redmine.user.get(record.user.id)
            note = record.notes
            if note != '' and A2RCHI_PATTERN not in note:
                history.append((user.login,note))
        return history
    
    def load(self):
        """
        Load the project that is responsible to deal with email tickets.
        """
        self.project = self.redmine.project.get(self.cleo_project)
        return

    def new_issue(self,sender,cc,subject,description,attachments):
        """
        Create a brand new issue in the cleo system
        """
        if not subject.strip():
            subject = 'EMPTY subject'
        issue = self.redmine.issue.new()
        issue.project_id = self.project.id
        issue.subject = subject
        issue.description = description
        issue.tracker_id = self.tracker_dict["Support"]
        issue.status_id = self.status_dict['New']
        issue.priority_id = self.priorities_dict['Normal']
        issue.assigned_to_id = self.user.id
        issue.watcher_user_ids = []
        #issue.parent_issue_id =
        issue.start_date = datetime.date.today()
        issue.due_date = datetime.date.today()+datetime.timedelta(1)
        issue.estimated_hours = 1
        issue.done_ratio = 0
        issue.custom_fields = [{'id': 1, 'value': sender}, {'id': 2, 'value': cc}]
        #print(issue.custom_fields)
        #issue.custom_fields = []
        #issue.uploads = [{'path': '/abs/path/to/f1'}, {'path': '/abs/path/to/f2'}]
        #print(attachments)
        issue.uploads = attachments
        issue.save()
        return issue.id

    def process_new_issues(self):
        """
        Process all issues that are assigned to me and that are in 'New' or `In Progress` status.
        """
        issue_ids = []
        for issue in self.redmine.issue.filter(assigned_to_id=self.user.id,):
            if issue.status.id == self.status_dict['New'] or issue.status.id == self.status_dict['In Progress']:
                issue_ids.append(issue.id)
                subject = f"Re:{issue.subject}"
                history = self.remove_format(f"description: {issue.description}",'pre')
                for record in issue.journals:
                    if record.notes != "":
                        history += f"\n next entry: {record.notes}"                    
                logger.info("History input: ",history)
                try:
                    answer = self.ai_wrapper(self.get_issue_history(issue.id), issue.id)
                except Exception as e:
                    logger.error(str(e))
                    answer = "I am sorry, I am not able to process this request at the moment. Please continue with this ticket manually."
                self.add_note_to_issue(issue.id,answer)
                logger.info("A2rchi's response:\n",answer)
                self.feedback_issue(issue.id)
        logger.info("cleo.process_new_issues: %d"%(len(issue_ids)))
        return issue_ids

    def process_resolved_issues(self):
        """
        Process all issues that are in resolved mode.
        """
        issue_ids = []
        for issue in self.project.issues:
            if issue.status.id == self.status_dict['Resolved']:
                logger.info("Process_resolved_issues: {issue.id}")
                issue_ids.append(issue.id)
                subject = f"Re:{issue.subject}"
                to = issue.custom_fields[0]['value']
                cc = issue.custom_fields[1]['value']
                note = ''
                for record in issue.journals:
                    if record.notes and record.notes != "" and A2RCHI_PATTERN not in record.notes:
                        note = record.notes
                logger.info(f"\n TO:{to}\n CC:{cc}\n SUBJECT:{subject}\nISSUE_ID:{issue.id} (leave for reference)\n\n{note}\n\n> {issue.description}")
                note = f"\nISSUE_ID:{issue.id} (leave for reference)\n\n{note}"
                addon = issue.description.replace("\n","\n > ")
                self.smtp.send_message(to,cc,subject,f"{note}\n\nInitial request:\n > {addon}")
                self.close_issue(issue.id,note)
        logger.info("cleo.process_resolved_issues: %d"%(len(issue_ids)))
        return issue_ids
        
    def remove_format(self,string,tag):
        pattern = r"<%s>.*?</%s>"%(tag,tag)
        return re.sub(pattern,"",string,flags=re.DOTALL)
    
    def close_issue(self,issue_id,answer):
        """
        Moving the issue in the 'closed' status
        """
        self.redmine.issue.update(issue_id,status_id=self.status_dict['Closed'],
                                  notes=f'{A2RCHI_PATTERN} Resolving email was sent:\n{answer}')
        return
    
    def feedback_issue(self,issue_id):
        """
        Moving the issue in the 'feedback' status
        """
        self.redmine.issue.update(issue_id,status_id=self.status_dict['Feedback'],
                                  notes=f'{A2RCHI_PATTERN} Moved into feedback.')
        return

    def show_issue(self,issue_id):
        """
        Show issue with given id as presently in the cleo system
        """
        issue = self.project.issues.get(issue_id)
        logger.info(f"ID: {issue.id}")
        logger.info(f"Subject: {issue.subject}")
        logger.info(f"Description: {issue.description}")
        logger.info(f"Tracker: {issue.tracker} ({issue.tracker.id})")
        logger.info(f"Status: {issue.status} ({issue.status.id})")
        for record in issue.journals:
            logger.info(dir(record))
            user = self.redmine.user.get(record.user.id)
            logger.info(f" {record} ({user.login}):\n{record.notes}")
        return
            
    def show_issues(self):
        """
        Show all issues in the project
        """
        first = True
        for issue in self.project.issues:
            if first:
                first = False
                logger.info("ID status -- subject")
                logger.info("========================")
            #print(" %04d %s -- %s"%(issue.id,issue.status,issue.subject))
            self.show_issue(issue.id)
        return

    def _connect(self):
        """
        Open the redmine web site called cleo
        """
        logger.info(f"Open redmine (URL:{self.cleo_url} U:{self.cleo_user} P:*********)")
        rd = Redmine(self.cleo_url, username=self.cleo_user, password=self.cleo_pw)
        return rd
        
    def _verify(self):
        """
        Make sure the environment is setup
        """
        if self.cleo_url == None or self.cleo_user == None or self.cleo_pw == None:
            logger.info("Did not find all cleo configs: CLEO_URL, CLEO_USER, CLEO_PW (source ~/.cleo).")
            return False
        return True
