from A2rchi.chains.base import BaseSubMITChain as BaseChain

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

        if self.utils_config["data_manager"]["use_HTTP_chromadb_client"]:
            self.client = chromadb.HttpClient(
                host=self.utils_config["data_manager"]["chromadb_host"],
                port=self.utils_config["data_manager"]["chromadb_port"],
            )
        else:
            self.client = chromadb.PersistentClient(path = self.global_config["LOCAL_VSTORE_PATH"])

        self.collection_name = self.utils_config["data_manager"]["collection_name"] + "_with_" + embedding_name
        self.vectorstore = Chroma(client = self.client, collection_name = self.collection_name,
                                  embedding_function = self.embedding_model)

        print("Collection used: ", self.client.get_collection(self.collection_name))
        
        model_class_map = self.config["MODEL_CLASS_MAP"]
        model_name = self.config["MODEL_NAME"]
        self.llm = model_class_map[model_name]["class"](**model_class_map[model_name]["kwargs"])

        print("Using model ", model_name, " with parameters: ")
        for param_name in model_class_map[model_name]["kwargs"].keys():
            print("\t" , param_name , ": " , model_class_map[model_name]["kwargs"][param_name])

        self.chain = BaseChain.from_llm(self.llm, self.vectorstore.as_retriever(), return_source_documents = True)

        # only run update vectorstore thread if dynamic updating of vectorstore allowed with ssh client
        if self.utils_config["data_manager"]["use_HTTP_chromadb_client"]:
            update_vectorstore_thread = Thread(target=self.update_vectorstore)
            update_vectorstore_thread.start()

    def update_vectorstore(self):
        while not self.kill:
            time.sleep(int(self.config["chain_update_time"]))
            self.lock.acquire()
            self.vectorstore = Chroma(
                client=self.client,
                collection_name=self.collection_name,
                embedding_function=self.embedding_model,
            )
            self.chain = BaseChain.from_llm(self.llm, self.vectorstore.as_retriever(), return_source_documents=True)
            print(f"N entries: {self.client.get_collection(self.collection_name).count()}")
            print("Updated chain with new vectorstore")
            self.lock.release()

        return


    def __call__(self, history):
        """
        Call for the chain to answer a question

        Input: a history which is formatted as a list of 2-tuples, where the first element
        of the tuple is the author and the second element of the tuple is text that that 
        author wrote.

        Output: a dictionary containing the answer and some meta data. 
        """
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
        answer = self.chain({"question": question, "chat_history": chat_history})
        print(f" INFO - answer: {answer}")
        self.lock.release()

        return answer
