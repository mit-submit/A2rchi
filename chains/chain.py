from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import CharacterTextSplitter
from langchain.chat_models import ChatOpenAI

from langchain.chains import ConversationalRetrievalChain

from chains.base import BaseSubMITChain as BaseChain
from chains.models import OpenAILLM, DumbLLM, LlamaLLM

from langchain.document_loaders import TextLoader
from langchain.document_loaders import PyPDFLoader
from langchain.memory import ConversationBufferMemory
from langchain.document_loaders import BSHTMLLoader
from langchain.document_loaders import WebBaseLoader
from langchain.docstore.document import Document

from utils.data_manager import DataManager

import os
from threading import Lock, Thread
import time

from utils.config_loader import Config_Loader
config = Config_Loader().config["chains"]["chain"]
global_config = Config_Loader().config["global"]


class Chain() :

    def __init__(self):
        """
        Gets all the relavent files from the data directory and converts them
        into a format that the chain can use. Then, it creates the chain using 
        those documents.
        """
        self.lock = Lock()
        self.kill = False

        self.dataManager = DataManager()
        self.vectorstore = self.dataManager.fetch_vectorstore()

        model_class_map = config["MODEL_CLASS_MAP"]
        model_name = config["MODEL_NAME"]
        self.llm = model_class_map[model_name]["class"](**model_class_map[model_name]["kwargs"])

        print("Using model ", model_name, " with parameters: ")
        for param_name in model_class_map[model_name]["kwargs"].keys():
            print("\t" , param_name , ": " , model_class_map[model_name]["kwargs"][param_name])

        self.chain = BaseChain.from_llm(self.llm, self.vectorstore.as_retriever(), return_source_documents=True)

        update_vectorstore_thread = Thread(target=self.update_vectorstore)
        update_vectorstore_thread.start()

    def update_vectorstore(self):
        while not self.kill:
            time.sleep(1000)
            self.lock.acquire()
            self.vectorstore = self.dataManager.fetch_vectorstore()
            self.chain = BaseChain.from_llm(self.llm, self.vectorstore.as_retriever(), return_source_documents=True)
            print("Updated chain with new vectorstore")
            self.lock.release()
        return None
            



    def __call__(self, history):
        """
        Call for the chain to answer a question

        Input: a history which is formatted as a list of 2-tuples, where the first element
        of the tuple is the author and the second element of the tuple is text that that 
        author wrote.

        Output: a dictionary containing the answer and some meta data. 
        """

        #seperate out the history into past interaction and current question input
        question = history[-1][1]
        if history is not None:
            prev_history = history[:-1]
        else:
            prev_history = None

        #make the request to the chain 
        self.lock.acquire()
        answer = self.chain({"question": question, "chat_history": prev_history})
        self.lock.release()
        return answer


