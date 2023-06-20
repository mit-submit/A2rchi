from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import CharacterTextSplitter
from langchain.llms import OpenAI
from langchain.chains import ConversationalRetrievalChain
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

        urls = ["https://submit.mit.edu",
            "https://submit.mit.edu/?page_id=6",
            "https://submit.mit.edu/?page_id=7",
            "https://submit.mit.edu/?page_id=8",
            "https://submit.mit.edu/?page_id=73"]

        #book_loaders= [PyPDFLoader("data/markus_books_pdf/" + file_name) for file_name in books]
        #transcript_loaders = [PyPDFLoader("data/markus_videos_pdf/" + file_name) for file_name in transcripts]
        html_loaders = [BSHTMLLoader("data/submit_website/" + file_name) for file_name in htmls]
        #web_loaders = [WebBaseLoader(url) for url in urls]

        github_docs = []
        for github_page in github_pages:
            with open('data/github/' +  github_page, 'r') as file:
                github_docs.append(Document(page_content=file.read()))
        
        loaders = html_loaders
        docs = []
        for github_doc in github_docs:
            docs.extend(github_doc)
        for loader in loaders:
            docs.extend(loader.load())


        text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        documents = text_splitter.split_documents(github_docs)

        embeddings = OpenAIEmbeddings()
        vectorstore = Chroma.from_documents(documents, embeddings)

        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)

        self.chain = ConversationalRetrievalChain.from_llm(OpenAI(temperature=.04), vectorstore.as_retriever(), return_source_documents=True)
