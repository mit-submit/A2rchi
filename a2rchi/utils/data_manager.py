from a2rchi.utils.scraper import Scraper
from a2rchi.utils.ticket_manager import TicketManager
from a2rchi.utils.git_scraper import GitScraper
from a2rchi.utils.logging import get_logger
from a2rchi.utils.config_loader import load_config

from chromadb.config import Settings
from langchain_community.document_loaders.text import TextLoader
from langchain_community.document_loaders import PyPDFLoader
from langchain_community.document_loaders import BSHTMLLoader
from langchain_community.document_loaders import UnstructuredMarkdownLoader
from langchain_community.document_loaders import PythonLoader
from langchain_openai import OpenAIEmbeddings
from langchain_chroma.vectorstores import Chroma
from langchain_text_splitters.character import CharacterTextSplitter
from langchain_community.document_loaders import DirectoryLoader

import chromadb
import nltk
import hashlib
import os
import yaml
import time

logger = get_logger(__name__)

SUPPORTED_DISTANCE_METRICS = ['l2', 'cosine', 'ip']

class DataManager():

    def __init__(self):

        self.config = load_config(map=True)
        self.global_config = load_config(map=True)["global"]
        self.data_path = self.global_config["DATA_PATH"]
        self.chroma_config = self.config['services']['chromadb']
        self.stemmer = None
        
        # create data path if it doesn't exist
        os.makedirs(self.data_path, exist_ok=True)

        # scrape data onto the filesystem
        logger.info("Scraping documents onto filesystem")
        scraper = Scraper(dm_config=self.config['data_manager'])
        scraper.hard_scrape(verbose=True)

        # Fetch ticket data via APIs and copy onto the filesystem
        logger.info("Fetching ticket data onto filesystem")
        ticket_manager = TicketManager()
        ticket_manager.run()

        # scrape data onto the filesystem
        logger.info("Scraping git documentation onto filesystem")
        scraper = GitScraper()
        scraper.hard_scrape(verbose=True)

        # get the collection (reset it if it already exists and reset_collection = True)
        # the actual name of the collection is the name given by config with the embeddings specified
        embedding_name = self.config["data_manager"]["embedding_name"]
        self.collection_name = self.config["data_manager"]["collection_name"] + "_with_" + embedding_name
        logger.info(f"Using collection: {self.collection_name}")

        # distance metric to use for similarity search for RAG later on
        self.distance_metric = self.config["data_manager"]["distance_metric"]
        if self.distance_metric not in SUPPORTED_DISTANCE_METRICS:
            raise ValueError(f"The selected distance metrics, '{self.distance_metric}', is not supported. Must be one of {SUPPORTED_DISTANCE_METRICS}")

        # delete the existing collection if specified
        self.delete_existing_collection_if_reset()

        # get the embedding model
        embedding_class_map = self.config["data_manager"]["embedding_class_map"]
        embedding_name = self.config["data_manager"]["embedding_name"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])

        # create the text_splitter
        self.text_splitter = CharacterTextSplitter(
            chunk_size=self.config["data_manager"]["chunk_size"],
            chunk_overlap=self.config["data_manager"]["chunk_overlap"],
        )

        # makes sure nltk gets installed and initializes stemmer
        if self.config["data_manager"]["stemming"].get("enabled", False):
            nltk.download('punkt_tab')
            self.stemmer = nltk.stem.PorterStemmer()

    def delete_existing_collection_if_reset(self):
        """
        Connect to ChromaDB and delete collection.
        """
        # return early if not resetting
        if not self.config["data_manager"]["reset_collection"]:
            return

        # connect to chromadb server
        client = None
        if self.chroma_config["use_HTTP_chromadb_client"]:
            client = chromadb.HttpClient(
                host=self.chroma_config["chromadb_host"],
                port=self.chroma_config["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.chroma_config["local_vstore_path"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )

        if self.collection_name in [collection.name for collection in client.list_collections()]:
            client.delete_collection(self.collection_name)


    def fetch_collection(self):
        """
        Connect to ChromaDB and fetch the collection.
        """
        # connect to chromadb server
        client = None
        if self.chroma_config["use_HTTP_chromadb_client"]:
            client = chromadb.HttpClient(
                host=self.chroma_config["chromadb_host"],
                port=self.chroma_config["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.chroma_config["local_vstore_path"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        collection = client.get_or_create_collection(
            name=self.collection_name,
            metadata={
                "hnsw:space": self.distance_metric
            }
        )

        logger.info(f"N in collection: {collection.count()}")
        return collection


    def update_vectorstore(self):
        """
        Method which looks at the files in the data folder and syncs them to the vectors stored in the vectorstore
        """
        # fetch the collection
        collection = self.fetch_collection()

        # get current status of persistent vstore 
        files_in_vstore = [metadata["filename"] for metadata in collection.get(include=["metadatas"])["metadatas"]]

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
            logger.info("Vectorstore is up to date")
        else:
            logger.info("Vectorstore needs to be updated")

            # Creates a list of the file names to remove from vectorstore
            # Note: the full path of the files is not needed here.
            files_to_remove = list(set(files_in_vstore) - set(files_in_data.keys()))

            # removes files from the vectorstore
            logger.info(f"Files to remove: {files_to_remove}")
            collection = self._remove_from_vectorstore(collection, files_to_remove)

            # Create dictionary of the files to add, where the keys are the filenames and the values are the path of the file in data
            files_to_add = {filename: files_in_data[filename] for filename in list(set(files_in_data.keys()) - set(files_in_vstore))}

            # adds the files to the vectorstore
            logger.info(f"Files to add: {files_to_add}")
            collection = self._add_to_vectorstore(collection, files_to_add, sources)
            
            logger.info("Vectorstore update has been completed")

        logger.info(f"N Collection: {collection.count()}")

        # delete collection to release collection and client object as well for garbage collection
        del collection

        return


    def _remove_from_vectorstore(self, collection, files_to_remove):
        """
        Method which takes as input a list of filenames to remove from the vectorstore,
        then removes those filenames from the vectorstore.
        """
        for filename in files_to_remove:
            collection.delete(where={"filename": filename})

        return collection

    
    def _add_to_vectorstore(self, collection, files_to_add, sources={}):
        """
        Method which takes as input:

           collection:   a ChromaDB collection
           files_to_add: a dictionary with keys being the filenames and values being the file path
           sources:      a dictionary, usually loaded from a yaml file, which has keys being the 
                         file hash (everything in the file name except the file extension) and has
                         values of the url from which the source originated from. Not all files must
                         be in the source dictionary.

        and adds these files to the vectorstore.
        """
        for filename, file in files_to_add.items():

            logger.info(f"Processing file: {filename}")

            # create the chunks
            loader = None
            try:
                loader = self.loader(file)
            except Exception as e:
                logger.error(f"Failed to load file: {file}. Skipping. Exception: {e}")

            # treat case where file extension is not recognized or is broken
            if loader is None:
                continue 

             # initialize lists for file chunks and metadata
            chunks = []
            metadatas = []
            
            # load documents from current file and add to docs and metadata
            docs = loader.load()
            for doc in docs:
                
                new_chunks = [document.page_content for document in self.text_splitter.split_documents([doc])]
                
                for new_chunk in new_chunks:
                    if self.config["data_manager"]["stemming"].get("enabled", False):
                        words = nltk.tokenize.word_tokenize(new_chunk)
                        stemmed_words = [self.stemmer.stem(word) for word in words]
                        new_chunk = " ".join(stemmed_words)
                    chunks.append(new_chunk)
                    metadatas.append(doc.metadata)

            # explicitly get file metadata
            filehash = filename.split(".")[0]
            url = sources[filehash] if filehash in sources.keys() else ""

            logger.info(f"Corresponding: {filename} {filehash} -> {url}")

            # embeds each chunk
            embeddings = self.embedding_model.embed_documents(chunks)
            
            # add filename (better even corresponding url) as metadata for each chunk
            for metadata in metadatas:
                metadata["filename"] = filename
            
            # create unique id for each chunk
            # the first 12 bits of the id being the filename, 6 more based on the chunk itself, and the last 6 hashing the time
            ids = []
            for chunk in chunks:
                identifier = hashlib.md5()
                identifier.update(chunk.encode('utf-8'))
                chunk_hash = str(int(identifier.hexdigest(),16))[0:6]
                time_identifier = hashlib.md5()
                time_identifier.update(str(time.time()).encode('utf-8'))
                time_hash = str(int(identifier.hexdigest(),16))[0:6]
                while str(filehash) + str(chunk_hash) + str(time_hash) in ids:
                    logger.info("Found conflict with hash: " + str(filehash) + str(chunk_hash) + str(time_hash) + ". Trying again")
                    time_hash = str(int(time_hash) + 1)
                ids.append(str(filehash) + str(chunk_hash) + str(time_hash))

            logger.debug(f"Ids: {ids}")

            collection.add(embeddings=embeddings, ids=ids, documents=chunks, metadatas=metadatas)

            logger.info(f"Successfully added file {filename}")

            if url: logger.info(f"with URL: {url}")

        return collection


    def loader(self, file_path):
        """
        Return the document loader from a path, with the correct loader given the extension 
        """
        _, file_extension = os.path.splitext(file_path)
        if file_extension == ".txt":
            return TextLoader(file_path)
        elif file_extension == ".C":
            return TextLoader(file_path)
        elif file_extension == ".md":
            return UnstructuredMarkdownLoader(file_path)
        elif file_extension == ".py":
            return PythonLoader(file_path)
        elif file_extension == ".html":
            return BSHTMLLoader(file_path, bs_kwargs={"features": "html.parser"})
        elif file_extension == ".pdf":
            return PyPDFLoader(file_path)
        else: 
            logger.error(f"Format not supported -- {file_path}")
            return None
