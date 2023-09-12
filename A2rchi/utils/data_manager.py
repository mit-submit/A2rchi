from chromadb.config import Settings
from langchain.document_loaders import TextLoader
from langchain.document_loaders import PyPDFLoader
from langchain.memory import ConversationBufferMemory
from langchain.document_loaders import BSHTMLLoader
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.vectorstores import Chroma
from langchain.text_splitter import CharacterTextSplitter

import chromadb
import hashlib
import os
import yaml


class DataManager():

    def __init__(self):
        from A2rchi.utils.config_loader import Config_Loader
        self.config = Config_Loader().config["utils"]
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]

        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # connect to chromadb server
        if self.config["data_manager"]["use_HTTP_chromadb_client"]:
            self.client = chromadb.HttpClient(
                host=self.config["data_manager"]["chromadb_host"],
                port=self.config["data_manager"]["chromadb_port"],
                settings=Settings(allow_reset=True),
            )
        else:
            self.client = chromadb.PersistentClient(path=self.global_config["LOCAL_VSTORE_PATH"])

        # get the collection (reset it if it already exists and reset_collection = True)
        # the actual name of the collection is the name given by config with the embeddings specified
        embedding_name = self.config["embeddings"]["EMBEDDING_NAME"]
        self.collection_name = self.config["data_manager"]["collection_name"] + "_with_" + embedding_name
        print("Using collection: ", self.collection_name)

        if self.config["data_manager"]["reset_collection"] and self.collection_name in [collection.name for collection in self.client.list_collections()]:
            self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(self.collection_name)
        print(f" n in collection: {self.collection.count()}")

        # get the embedding model
        embedding_class_map = self.config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.config["embeddings"]["EMBEDDING_NAME"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])

        # create the text_splitter
        self.text_splitter = CharacterTextSplitter(
            chunk_size=self.config["data_manager"]["CHUNK_SIZE"],
            chunk_overlap=self.config["data_manager"]["CHUNK_OVERLAP"],
        )


    def update_vectorstore(self):
        """
        Method which looks at the files in the data folder and syncs them to the vectors stored in the vectorstore
        """
        # get current status of persistent vstore 
        files_in_vstore = [metadata["filename"] for metadata in self.collection.get(include=["metadatas"])["metadatas"]]

        # scan data folder and obtain list of files in data. Assumes max depth = 1
        dirs = [
            os.path.join(self.data_path, dir)
            for dir in os.listdir(self.data_path)
            if os.path.isdir(os.path.join(self.data_path, dir)) and dir != "vstore"
        ]
        files_in_data_fullpath = [
            os.path.join(dir, file)
            for dir in dirs
            for file in os.listdir(dir)
        ]

        # files in data is a dictionary, with keys of the names of files and values with their full path.
        files_in_data = {os.path.basename(file_fullpath): file_fullpath for file_fullpath in files_in_data_fullpath}

        # get map between sources and filename hashes
        with open(os.path.join(self.data_path, 'sources.yml'), 'r') as file:
            sources = yaml.load(file, Loader=yaml.FullLoader)

        # control if files in vectorstore == files in data
        if set(files_in_data.keys()) == set(files_in_vstore):
            print("Vectorstore is up to date")
        else:
            print("Vectorstore needs to be updated")

            # creates a list of the file names to remove from vectorstore
            # Note: the full path of the files is not needed here.
            files_to_remove = list(set(files_in_vstore) - set(files_in_data.keys()))

            # removes files from the vectorstore
            print(f"Files to remove: {files_to_remove}")
            self._remove_from_vectorstore(files_to_remove)

            # create dictionary of the files to add, where the keys are the filenames and the values are the path of the file in data
            files_to_add = {filename: files_in_data[filename] for filename in list(set(files_in_data.keys()) - set(files_in_vstore))}

            # adds the files to the vectorstore
            print(f"Files to add: {files_to_add}")
            self._add_to_vectorstore(files_to_add, sources)
            print("Vectorstore update has been completed")

        print(f" N Collection: {self.collection.count()}")

        return


    def _remove_from_vectorstore(self, files_to_remove):
        """
        Method which takes as input a list of filenames to remove from the vectorstore,
        then removes those filenames from the vectorstore.
        """
        for filename in files_to_remove:
            self.collection.delete(where={"filename": filename})


    def _add_to_vectorstore(self, files_to_add, sources={}):
        """
        Method which takes as input:
        
           files_to_add: a dictionary with keys being the filenames and values being the file path
           sources:      a dictionary, usually loaded from a yaml file, which has keys being the 
                         file hash (everything in the file name except the file extension) and  has
                         values of the url from which the source originated from. Not all files must
                         be in the source dictionary.

        and adds these files to the vectorstore.
        """
        for filename, file in files_to_add.items():

            # create the chunks
            loader = None
            try:
                loader = self.loader(file)
            except:
                print(f" ERROR - loading: {file} skip and move on.")

            # treat case where file extension is not recognized or is broken
            if loader is None:
                continue 

            doc = loader.load()[0]
            chunks = [document.page_content for document in self.text_splitter.split_documents([doc])]

            # explicitly get file metadata
            filehash = filename.split(".")[0]
            url = sources[filehash] if filehash in sources.keys() else ""

            # embed each chunk
            embeddings = self.embedding_model.embed_documents(chunks)

            # create the metadata for each chunk
            metadatas = [doc.metadata for chunk in chunks]
            for metadata in metadatas:
                metadata["filename"] = filename
            
            # create unique id for each chunk
            # the first 12 bits of the id being the filename and the other 6 based on the chunk itself
            ids = []
            for chunk in chunks:
                identifier = hashlib.md5()
                identifier.update(chunk.encode('utf-8'))
                chunk_hash = str(int(identifier.hexdigest(),16))[0:6]
                ids.append(str(filehash) + str(chunk_hash))

            print("Ids: ",ids)
            self.collection.add(embeddings=embeddings, ids=ids, documents=chunks, metadatas=metadatas)


    def loader(self, file_path):
        """
        Return the document loader from a path, with the correct loader given the extension 
        """
        _, file_extension = os.path.splitext(file_path)
        if file_extension == ".txt":
            return TextLoader(file_path)
        elif file_extension == ".html":
            return BSHTMLLoader(file_path, bs_kwargs={"features": "html.parser"})
        elif file_extension == ".pdf":
            return PyPDFLoader(file_path)
        else: 
            print(f" Error: format not supported -- {file_path}")
            return None 
