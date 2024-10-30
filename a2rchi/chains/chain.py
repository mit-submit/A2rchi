from a2rchi.chains.base import BaseSubMITChain as BaseChain
from a2rchi.chains.prompts import read_prompt
from a2rchi.utils.config_loader import Config_Loader

from chromadb.config import Settings
from langchain.prompts.prompt import PromptTemplate
from langchain_chroma.vectorstores import Chroma

from langchain.chains.conversation.memory import ConversationSummaryMemory
from langchain.chains import ConversationChain

import chromadb


class Chain() :

    def __init__(self):
        """
        Gets all the relavent files from the data directory and converts them
        into a format that the chain can use. Then, it creates the chain using 
        those documents.
        """
        self.kill = False
        self.update_config()


    def update_config(self):
        print("Chain Updating Config")
        self.config = Config_Loader().config
        self.chain_config = self.config["chains"]["chain"]
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.prompt_config = self.config["chains"]["prompts"]

        embedding_class_map = self.utils_config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.utils_config["embeddings"]["EMBEDDING_NAME"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])
        self.collection_name = self.utils_config["data_manager"]["collection_name"] + "_with_" + embedding_name

        print("Using collection: ", self.collection_name)

        model_class_map = self.chain_config["MODEL_CLASS_MAP"]
        model_name = self.chain_config["MODEL_NAME"]
        condense_model_name = self.chain_config.get("CONDENSE_MODEL_NAME", model_name)
        summary_model_name = self.chain_config.get("SUMMARY_MODEL_NAME", model_name)
        self.llm = model_class_map[model_name]["class"](**model_class_map[model_name]["kwargs"])
        self.condense_llm = model_class_map[condense_model_name]["class"](**model_class_map[condense_model_name]["kwargs"])
        self.summary_llm = model_class_map[summary_model_name]["class"](**model_class_map[summary_model_name]["kwargs"])

        print("Using model ", model_name, " with parameters: ")
        for param_name in model_class_map[model_name]["kwargs"].keys():
            print("\t" , param_name , ": " , model_class_map[model_name]["kwargs"][param_name])

        print("Using condense model ", condense_model_name, " with parameters: ")
        for param_name in model_class_map[condense_model_name]["kwargs"].keys():
            print("\t" , param_name , ": " , model_class_map[condense_model_name]["kwargs"][param_name])

        # self.qa_prompt = PromptTemplate(
        #     template=read_prompt(self.prompt_config["MAIN_PROMPT"], is_main_prompt=True), input_variables=["history", "input"]
        # )
        self.qa_prompt = PromptTemplate(
            template=read_prompt(self.prompt_config["MAIN_PROMPT"], is_main_prompt=True), input_variables=["context", "question"]
        )

        self.condense_question_prompt = PromptTemplate(
            template=read_prompt(self.prompt_config["CONDENSING_PROMPT"], is_condense_prompt=True), input_variables=["chat_history", "question"]
        )

        self.summary_prompt = PromptTemplate(
            template=read_prompt(self.prompt_config["SUMMARY_PROMPT"]), input_variables=["summary", "new_lines"]
        )

        # TODO: may want to add this back if we allow ConversationChain + ConversationSummaryMemory
        # self.memory_map = {}

    def update_vectorstore_and_create_chain(self, conversation_id):
        # connect to chromadb server
        client = None
        if self.utils_config["data_manager"]["use_HTTP_chromadb_client"]:
            client = chromadb.HttpClient(
                host=self.utils_config["data_manager"]["chromadb_host"],
                port=self.utils_config["data_manager"]["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.global_config["LOCAL_VSTORE_PATH"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )

        # construct chain
        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )
        chain = BaseChain.from_llm(
            self.llm,
            vectorstore.as_retriever(),
            qa_prompt=self.qa_prompt,
            condense_question_prompt=self.condense_question_prompt,
            summary_prompt=self.summary_prompt,
            condense_question_llm=self.condense_llm,
            summary_llm=self.summary_llm,
            return_source_documents=True,
        )
        print(f"N entries: {client.get_collection(self.collection_name).count()}")
        print("Updated chain with new vectorstore")

        # TODO: make it eas(ier) for users to use diff. LangChain chains 
        # # TODO: try to load/construct memory from past conversation if not in memory map (i.e. check postgres)
        # if conversation_id not in self.memory_map:
        #     self.memory_map[conversation_id] = ConversationSummaryMemory(
        #         llm=self.summary_llm,
        #         prompt=self.summary_prompt,
        #         max_token_limit=256,
        #     )

        # memory = self.memory_map[conversation_id]
        # chain = ConversationChain(llm=self.llm, memory=memory, prompt=self.qa_prompt, verbose=True)

        return chain


    def similarity_search(self, input):
        """
        Perform similarity search with input against vectorstore.
        """
        # connect to chromadb server
        client = None
        if self.utils_config["data_manager"]["use_HTTP_chromadb_client"]:
            client = chromadb.HttpClient(
                host=self.utils_config["data_manager"]["chromadb_host"],
                port=self.utils_config["data_manager"]["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.global_config["LOCAL_VSTORE_PATH"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        
        # construct vectorstore
        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )

        # perform similarity search
        similarity_result = vectorstore.similarity_search_with_score(input)
        score = (
            vectorstore.similarity_search_with_score(input)[0][1]
            if len(similarity_result) > 0
            else 1e10
        )

        # clean up vectorstore and client
        del vectorstore
        del client

        return score


    def __call__(self, history, conversation_id):
        """
        Call for the chain to answer a question

        Input: a history which is formatted as a list of 2-tuples, where the first element
        of the tuple is the author and the second element of the tuple is text that that 
        author wrote.

        Output: a dictionary containing the answer and some meta data. 
        """
        # create chain w/up-to-date vectorstore
        chain = self.update_vectorstore_and_create_chain(conversation_id)

        # seperate out the history into past interaction and current question input
        if len(history) > 0 and len(history[-1]) > 1:
            question = history[-1][1]
        else:
            print(" ERROR - no question found")
            question = ""
        print(f" INFO - question: {question}")

        # get chat history if it exists
        chat_history = history[:-1] if history is not None else None

        # make the request to the chain 
        answer = chain({"question": question, "chat_history": chat_history})

        # TODO: this was used with ConversationChain w/ConversationSummaryMemory
        # answer = chain(question)
        # answer['answer'] = answer['response']
        # answer['source_documents'] = []
        print(f" INFO - answer: {answer}")

        # delete chain object to release chain, vectorstore, and client for garbage collection
        del chain

        return answer
