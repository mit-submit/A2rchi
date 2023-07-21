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

        htmls = os.listdir('data/submit_website')
        github_pages = os.listdir('data/github')

        html_loaders = [BSHTMLLoader("data/submit_website/" + file_name) for file_name in htmls]
        github_loaders = [TextLoader("data/github/" + file_name) for file_name in github_pages]

        #github_docs = []
        #for github_page in github_pages:
        #    with open('data/github/' +  github_page, 'r') as file:
        #        github_docs.append(Document(page_content=file.read(), metadata =  {"source": "user guide chapter: " + github_page[:-3]}))
        
        loaders = html_loaders + github_loaders
        docs = []
        #for github_doc in github_docs:
        #    docs.extend(github_doc)
        for loader in loaders:
            docs.extend(loader.load())

        #print("docs are: ", docs)


        text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        documents = text_splitter.split_documents(docs)

        embeddings = OpenAIEmbeddings()
        self.vectorstore = Chroma.from_documents(documents, embeddings)

        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

        self.chain = ConversationalRetrievalAndSubMITChain.from_llm(ChatOpenAI(temperature=.04, model_name="gpt-4"), self.vectorstore.as_retriever(), return_source_documents=True)
