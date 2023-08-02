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
        from config_loader import Config_Loader
        config = Config_Loader().config["chains"]["chain"]
        self.global_config = Config_Loader().config["global"]
        self.data_path = self.global_config["DATA_PATH"]
        
        #Check if target folders exist 
        if not os.path.isdir(self.data_path):
                os.mkdir(self.data_path)
                #!Could think of scraping here 
    
        if not os.path.isdir(self.data_path+"vstore"):
                os.mkdir(self.data_path+"vstore")
                #!Could think of executing create_vectorstore (or update) here 
        return
    
    def update_vectorstore(self):
        #!Could add some verbose in the process

        #Get current status of persistent vstore 
        vstore = self.fetch_vectorstore()
        files_in_vstore = [f["source"] for f in vstore.get()["metadatas"]]
        ids_in_vstore = vstore.get()["ids"]

        #scan data folder and obtain list of files in data. Assumes max depth = 1
        dirs = [self.data_path + dir for dir in os.listdir(self.data_path) if os.path.isdir(self.data_path + dir) and dir!="vstore"]
        files_in_data = []
        for dir in dirs: 
            files = [dir+"/"+file for file in os.listdir(dir) if file != "info.txt"]
            for filename in files: 
                files_in_data.append(filename)

        # control if files in vectorstore == files in data
        if set(files_in_data)==set(files_in_vstore):
            print("Vectorstore is up to date")
        else:
            text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
            
            #remove obsolete files
            files_to_remove = list(set(files_in_vstore) - set(files_in_data))
            if files_to_remove:
                for file_to_remove in files_to_remove:
                    ids = []
                    for id,f in zip(ids_in_vstore,files_in_vstore):
                        if (f==file_to_remove): ids.append(id)
                    vstore._collection.delete(ids) #remove all ids liked to the one file in the loop
            
            #add new files to vectorstore
            files_to_add = list(set(files_in_data) - set(files_in_vstore))
            if files_to_add:
                loaders = [self.loader(f) for f in files_to_add]
                docs = []
                for loader in loaders:
                    docs.extend(loader.load())
                new_documents = text_splitter.split_documents(docs)
                if new_documents: vstore.add_documents(new_documents) #

        return
    
    def loader(self,file_path):
         #return the document loader from a path, with the correct loader given the extension 
         _, file_extension = os.path.splitext(file_path)
         if file_extension == ".txt" : return TextLoader(file_path)
         elif file_extension == ".html" : return BSHTMLLoader(file_path)
         elif file_extension == ".pdf" : return PyPDFLoader(file_path)
         else: print(file_path, " Error: format not supported")

    def create_vectorstore(self):
        #probably already obsolete, update does the job
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

        text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=0)
        documents = text_splitter.split_documents(docs)

        vectorstore = Chroma.from_documents(documents, OpenAIEmbeddings(), collection_name="OpenAI_Vstore", persist_directory=self.data_path+"vstore")
        return vectorstore
    

    def fetch_vectorstore(self):
        """
        create a vectorstore instance from the path ./data/vstore
        """ 
        vectorstore = Chroma(collection_name="OpenAI_Vstore", persist_directory=self.data_path+"vstore", embedding_function=OpenAIEmbeddings())
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
# d.create_vectorstore()
# print(d.fetch_vectorstore().get()["embeddings"])
# d.update_vectorstore()
# d.remove_file("Slurm_guide.html")
# print(d.fetch_vectorstore().get()) #does not update first shot