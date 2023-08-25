import os

from langchain.document_loaders import TextLoader
from langchain.document_loaders import PyPDFLoader
from langchain.memory import ConversationBufferMemory
from langchain.document_loaders import BSHTMLLoader
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import CharacterTextSplitter


class DataManager():

    def __init__(self):
        from A2rchi.utils.config_loader import Config_Loader
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]
        
        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True) #!Could think of scraping here

        # create vector store if it doesn't exist
        self.vector_store_path = os.path.join(self.data_path, "vstore")
        os.makedirs(self.vector_store_path, exist_ok=True) #!Could think of executing create_vectorstore (or update) here

        return
    
    def update_vectorstore(self):
        #!Could add some verbose in the process

        # Get current status of persistent vstore 
        vstore = self.fetch_vectorstore()
        files_in_vstore = [f["source"] for f in vstore.get()["metadatas"]]
        ids_in_vstore = vstore.get()["ids"]

        # scan data folder and obtain list of files in data. Assumes max depth = 1
        dirs = [
            os.path.join(self.data_path, dir)
            for dir in os.listdir(self.data_path)
            if os.path.isdir(os.path.join(self.data_path, dir)) and dir != "vstore"
        ]
        files_in_data = [
            os.path.join(dir, file)
            for dir in dirs
            for file in os.listdir(dir)
            if file != "info.txt"
        ]

        # control if files in vectorstore == files in data
        if set(files_in_data) == set(files_in_vstore):
            print("Vectorstore is up to date")
        else:
            text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
            
            # remove obsolete files
            files_to_remove = list(set(files_in_vstore) - set(files_in_data))
            ids_to_remove = [id for id, file in zip(ids_in_vstore, files_in_vstore) if file in files_to_remove]
            vstore._collection.delete(ids_to_remove)

            # add new files to vectorstore; will do nothing if files_to_add is empty
            files_to_add = list(set(files_in_data) - set(files_in_vstore))
            docs = [doc for f in files_to_add for doc in self.loader(f).load()]
            new_documents = text_splitter.split_documents(docs)
            if new_documents:
                vstore.add_documents(new_documents)

        return

    def loader(self, file_path):
         # return the document loader from a path, with the correct loader given the extension 
         _, file_extension = os.path.splitext(file_path)
         if file_extension == ".txt" : return TextLoader(file_path)
         elif file_extension == ".html" : return BSHTMLLoader(file_path)
         elif file_extension == ".pdf" : return PyPDFLoader(file_path)
         else: print(file_path, " Error: format not supported")

    def create_vectorstore(self):
        # fetch docs from directors in self.data_path
        dirs = [
            os.path.join(self.data_path, dir)
            for dir in os.listdir(self.data_path)
            if os.path.isdir(os.path.join(self.data_path, dir)) and dir != "vstore"
        ]
        files_in_data = [
            os.path.join(dir, file)
            for dir in dirs
            for file in os.listdir(dir)
            if file != "info.txt"
        ]
        docs = [doc for f in files_in_data for doc in self.loader(f).load()]

        # split documents
        text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        documents = text_splitter.split_documents(docs)

        # create and return vectorstore
        vectorstore = Chroma.from_documents(
            documents,
            embedding=OpenAIEmbeddings(),
            collection_name="OpenAI_Vstore",
            persist_directory=self.vector_store_path,
        )

        return vectorstore


    def fetch_vectorstore(self):
        """
        create a vectorstore instance from the path `self.data_path`/vstore
        """ 
        vectorstore = Chroma(
            collection_name="OpenAI_Vstore",
            embedding_function=OpenAIEmbeddings(),
            persist_directory=self.vector_store_path,
        )

        return vectorstore


    def delete_vectorstore(self):
        vectorstore = self.fetch_vectorstore()
        vectorstore._collection.delete()

        return


    def remove_file(self, file_to_remove):
        i = 0
        for root, _, files in os.walk(self.data_path):
            for file in files:
                # Check if the file name matches the given name
                if file == file_to_remove:
                    # Get the full path of the file
                    file_path = os.path.join(root, file)
                    # Delete the file
                    os.remove(file_path)
                    i += 1
                    print(f"File {file_path} has been removed")

        print(f"Removed {i} files")
        self.update_vectorstore()

        return


    def add_file(self, file):
        """
        Add a file in the `self.data_path`/manual directory. Create the directory if it's not there.
        """
        self.manual_dir = os.path.join(self.global_config["DATA_PATH"], "manual")
        os.makedirs(self.manual_dir, exist_ok=True)
                
        with open(file, 'r') as infile:
            content = infile.read()
            
        outfilename = os.path.join(self.manual_dir, os.path.basename(file))
        with open(outfilename, 'w') as outfile:
            outfile.write(content)
            print("File added successfully")

        self.update_vectorstore() #becomes aware that there is new data, will require adding it to the vectorstore
        return
