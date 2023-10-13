from A2rchi.chains.base import BaseSubMITChain as BaseChain

from chromadb.config import Settings
from langchain.vectorstores import Chroma
from threading import Lock, Thread

import chromadb
import time


class Chain() :

    def __init__(self):
        """
        Gets all the relavent files from the data directory and converts them
        into a format that the chain can use. Then, it creates the chain using 
        those documents.
        """
        self.lock = Lock()
        self.kill = False

        from A2rchi.utils.config_loader import Config_Loader
        self.config = Config_Loader().config["chains"]["chain"]
        self.global_config = Config_Loader().config["global"]
        self.utils_config = Config_Loader().config["utils"]

        embedding_class_map = self.utils_config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.utils_config["embeddings"]["EMBEDDING_NAME"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])
        self.collection_name = self.utils_config["data_manager"]["collection_name"] + "_with_" + embedding_name

        print("Using collection: ", self.collection_name)
        
        model_class_map = self.config["MODEL_CLASS_MAP"]
        model_name = self.config["MODEL_NAME"]
        self.llm = model_class_map[model_name]["class"](**model_class_map[model_name]["kwargs"])

        print("Using model ", model_name, " with parameters: ")
        for param_name in model_class_map[model_name]["kwargs"].keys():
            print("\t" , param_name , ": " , model_class_map[model_name]["kwargs"][param_name])


    def update_vectorstore_and_create_chain(self):
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

        # acquire lock and construct chain
        self.lock.acquire()
        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )
        chain = BaseChain.from_llm(self.llm, vectorstore.as_retriever(), return_source_documents=True)
        print(f"N entries: {client.get_collection(self.collection_name).count()}")
        print("Updated chain with new vectorstore")
        self.lock.release()

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


    def __call__(self, history):
        """
        Call for the chain to answer a question

        Input: a history which is formatted as a list of 2-tuples, where the first element
        of the tuple is the author and the second element of the tuple is text that that 
        author wrote.

        Output: a dictionary containing the answer and some meta data. 
        """
        # create chain w/up-to-date vectorstore
        chain = self.update_vectorstore_and_create_chain()

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
        self.lock.acquire()
        answer = chain({"question": question, "chat_history": chat_history})
        print(f" INFO - answer: {answer}")
        self.lock.release()

        # delete chain object to release chain, vectorstore, and client for garbage collection
        del chain

        return answer
