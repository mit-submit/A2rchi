import os

from langchain.document_loaders import TextLoader
from langchain.document_loaders import PyPDFLoader
from langchain.memory import ConversationBufferMemory
from langchain.document_loaders import BSHTMLLoader
from langchain.vectorstores import Chroma
from langchain.text_splitter import CharacterTextSplitter

from langchain.embeddings.openai import OpenAIEmbeddings

import chromadb
from chromadb.config import Settings
import yaml

import hashlib


class DataManager():

    def __init__(self):
        from config_loader import Config_Loader
        self.config = Config_Loader().config["utils"]
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]
        
        #Check if target folders exist 
        if not os.path.isdir(self.data_path):
                os.mkdir(self.data_path)
    
        #if not os.path.isdir(self.data_path+"vstore"):
        #        os.mkdir(self.data_path+"vstore")
        #
        
        #Connect to chromadb server
        if self.config["data_manager"]["use_HTTP_chromadb_client"]:
            self.client = chromadb.HttpClient(host=self.config["data_manager"]["chromadb_host"], port=self.config["data_manager"]["chromadb_port"], settings=Settings(allow_reset=True))
        else:
            self.client = chromadb.PersistentClient(path = self.global_config["DATA_PATH"])

        #get the collection (reset it if it already exists and reset_collection=True)
        #the actial name of the collection is the name given by config with the embeddings specified
        embedding_name = self.config["embeddings"]["EMBEDDING_NAME"]
        self.collection_name = self.config["data_manager"]["collection_name"] + "_with_" + embedding_name
        print("Using collection: ", self.collection_name)
        if self.config["data_manager"]["reset_collection"] and self.collection_name in [collection.name for collection in self.client.list_collections()]:
             self.client.delete_collection(self.collection_name)
        self.collection = self.client.get_or_create_collection(self.collection_name)

        #Get the embedding model
        embedding_class_map = self.config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.config["embeddings"]["EMBEDDING_NAME"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])


    
    def update_vectorstore(self):
        #TODO: check if we need to recreate the collection here

        #Get current status of persistent vstore 
        files_in_vstore = [met["filename"] for met in self.collection.get(include=["metadatas"])["metadatas"]]

        #scan data folder and obtain list of files in data. Assumes max depth = 1
        #TODO: improve this scan
        dirs = [self.data_path + dir for dir in os.listdir(self.data_path) if os.path.isdir(self.data_path + dir) and dir!="vstore"]
        files_in_data_fullpath = []
        for dir in dirs: 
            files = [dir+"/"+file for file in os.listdir(dir) if file != "info.txt"]
            for filename in files: 
                files_in_data_fullpath.append(filename)

        #files in data is a dictionary, with keys of the names of files and values with their full path.
        files_in_data = {file_fullpath.split("/")[-1]: file_fullpath for file_fullpath in files_in_data_fullpath}

        #get map between sources and filename hashes
        with open(self.global_config["DATA_PATH"]+'sources.yml', 'r') as file:
                sources = yaml.load(file, Loader=yaml.FullLoader)

        # control if files in vectorstore == files in data
        print("Files in data are: ", set(files_in_data.keys()))
        print("Files in vstore are: ", set(files_in_vstore))
        if set(files_in_data.keys())==set(files_in_vstore):
            print("Vectorstore is up to date")
        else:
            text_splitter = CharacterTextSplitter(chunk_size=self.config["data_manager"]["CHUNK_SIZE"], chunk_overlap=self.config["data_manager"]["CHUNK_OVERLAP"])
            
            #remove obsolete files
            files_to_remove = list(set(files_in_vstore) - set(files_in_data.keys()))
            for file in files_to_remove:
                filename = file.split("/")[-1]
                self.collection.delete(where={"filename": filename})
            
            #add new files to vectorstore
            files_to_add = list(set(files_in_data.keys()) - set(files_in_vstore))
            for filename in files_to_add:

                #create the chunks
                file = files_in_data[filename]
                loader = self.loader(file)
                doc = loader.load()[0]
                chunks = [document.page_content for document in text_splitter.split_documents([doc])]

                #explicityly get file metadata
                filehash = filename.split(".")[0]
                if filehash in sources.keys():
                     url = sources[filehash]
                else:
                     url = ""

                #Embed each chunk
                embeddings = self.embedding_model.embed_documents(chunks)

                #create the metadata for each chunk
                metadatas = [{"filename":filename, "url": url, "chunk_size":len(chunk)} for chunk in chunks]
                metadatas = [doc.metadata for chunk in chunks]
                for metadata in metadatas:
                     metadata["filename"] = filename
                
                #Create unique id for each chunk
                #The first 12 bits of the id being the filename and the other 6 based on the chunk itself
                ids = []
                for chunk in chunks:
                    identifier = hashlib.md5()
                    identifier.update(chunk.encode('utf-8'))
                    chunk_hash = str(int(identifier.hexdigest(),16))[0:6]
                    ids.append(str(filehash) + str(chunk_hash))
                
                self.collection.add(embeddings = embeddings, ids = ids, documents = chunks, metadatas = metadatas)

        return
    
    def loader(self,file_path):
         #return the document loader from a path, with the correct loader given the extension 
         _, file_extension = os.path.splitext(file_path)
         if file_extension == ".txt" : return TextLoader(file_path)
         elif file_extension == ".html" : return BSHTMLLoader(file_path)
         elif file_extension == ".pdf" : return PyPDFLoader(file_path)
         else: print(file_path, " Error: format not supported")

    def create_vectorstore(self):
        ###DEPRECATED METHOD
        # Can just use update_vectorstore

        #Check if target folders exist 
        if not os.path.isdir(self.data_path+"vstore"):
                os.mkdir(self.data_path+"vstore")

        dirs = [self.data_path + dir for dir in os.listdir(self.data_path) if os.path.isdir(self.data_path + dir) and dir!="vstore"]
        files_in_data = []
        for dir in dirs: 
            files = [dir+"/"+file for file in os.listdir(dir) if file != "info.txt"]
            for filename in files: 
                files_in_data.append(filename)

        loaders = [self.loader(f) for f in files_in_data]
        docs = []
        for loader in loaders:
            docs.extend(loader.load())

        text_splitter = CharacterTextSplitter(chunk_size=self.config["CHUNK_SIZE"], chunk_overlap=self.config["CHUNK_OVERLAP"])
        documents = text_splitter.split_documents(docs)

        embedding_class_map = self.config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.config["embeddings"]["EMBEDDING_NAME"]
        self.embedding = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])
        vectorstore = Chroma.from_documents(documents, self.embedding, collection_name="OpenAI_Vstore", persist_directory=self.data_path+"vstore")

        return vectorstore
    

    def fetch_vectorstore(self):
        """
        create a vectorstore instance from the path global_config["DATA_PATH"]
        """ 
        embedding_class_map = self.config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.config["embeddings"]["EMBEDDING_NAME"]
        self.embedding = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])

        vectorstore = Chroma(collection_name=embedding_name, persist_directory=self.data_path+"vstore", embedding_function=self.embedding)
        return vectorstore

    def delete_vectorstore(self):
        vstore=self.fetch_vectorstore()
        vstore._collection.delete()
        return
    
    def remove_file(self,file_to_remove):
        i = 0
        for root, dirs, files in os.walk(self.data_path):
            for file in files:
                # Check if the file name matches the given name
                if file == file_to_remove:
                    # Get the full path of the file
                    file_path = os.path.join(root, file)
                    # Delete the file
                    os.remove(file_path)
                    i += 1
                    print("File",file_path,"has been removed")
        print("Removed ",i," files")
        self.update_vectorstore()
        return
    

    def add_file(self,file):
        """
        Add a file in the $DATAS_PATH/manual directory. Create the directory if it's not there.
        
        """
        self.manual_dir = self.global_config["DATA_PATH"]+"manual/"
        if not os.path.isdir(self.manual_dir):
                os.mkdir(self.manual_dir)
                
        with open(file, 'r') as infile:
            content = infile.read()
            
        outfilename=self.manual_dir+os.path.basename(file)
        with open(outfilename, 'w') as outfile:
                outfile.write(content)
                print("File added successfully")

        self.update_vectorstore() #becomes aware that there is new data, will require adding it to the vectorstore
        return

# d = DataManager()
# d.delete_vectorstore()
# print(d.fetch_vectorstore().get())
# d.add_file("/home/submit/mori25/Slurm_guide.html")
# d.update_vectorstore()