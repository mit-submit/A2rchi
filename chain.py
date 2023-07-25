from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import CharacterTextSplitter
from langchain.llms import OpenAI
from langchain.chat_models import ChatOpenAI

from langchain.chains import ConversationalRetrievalChain
from conversational_retrieval_and_subMIT_help.base import ConversationalRetrievalAndSubMITChain

from langchain.document_loaders import TextLoader
from langchain.document_loaders import PyPDFLoader
from langchain.memory import ConversationBufferMemory
from langchain.document_loaders import BSHTMLLoader
from langchain.document_loaders import WebBaseLoader
from langchain.docstore.document import Document

import os

class Chain() :

    def __init__(self):
        """
        Gets all the relavent files from the data directory and converts them
        into a format that the chain can use. Then, it creates the chain using 
        those documents.
        """

        htmls = os.listdir('data/submit_website')
        github_pages = os.listdir('data/github')

        html_loaders = [BSHTMLLoader("data/submit_website/" + file_name) for file_name in htmls]
        github_loaders = [TextLoader("data/github/" + file_name) for file_name in github_pages]
        
        loaders = html_loaders + github_loaders
        docs = []
        for loader in loaders:
            docs.extend(loader.load())

        text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        documents = text_splitter.split_documents(docs)

        embeddings = OpenAIEmbeddings()
        self.vectorstore = Chroma.from_documents(documents, embeddings)

        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

        self.chain = ConversationalRetrievalAndSubMITChain.from_llm(ChatOpenAI(model_name="gpt-4", temperature= 1), self.vectorstore.as_retriever(), return_source_documents=True)

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
        return self.chain({"question": question, "chat_history": prev_history})


