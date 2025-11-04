import chromadb
from chromadb.config import Settings
from langchain_chroma.vectorstores import Chroma

from src.utils.logging import get_logger

logger = get_logger(__name__)


class VectorstoreConnector:
    """
    A class to manage the connection to the vectorstore (ChromaDB).
    This class initializes the vectorstore parameters from the config
    and provides a method to update the vectorstore connection.
    """

    def __init__(self, config):
        self.config = config
        self._init_vectorstore_params()

    def _init_vectorstore_params(self):
        """
        Initialize the vectorstore parameters from the config.
        """

        dm_config = self.config["data_manager"]
        chroma_config = self.config["services"]["chromadb"]

        embedding_class_map = dm_config["embedding_class_map"]
        embedding_name = dm_config["embedding_name"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](
            **embedding_class_map[embedding_name]["kwargs"]
        )
        self.collection_name = dm_config["collection_name"] + "_with_" + embedding_name
        self.use_HTTP_chromadb_client = chroma_config["use_HTTP_chromadb_client"]
        self.chromadb_host = chroma_config["chromadb_host"]
        self.chromadb_port = chroma_config["chromadb_port"]
        self.local_vstore_path = chroma_config["local_vstore_path"]

        logger.info(f"Vectorstore connection initialized with collection: {self.collection_name}")

    def _update_vectorstore_conn(self):
        """
        Function to update the vectorstore connection.
        """
        
        # connect to chromadb server
        client = None
        if self.use_HTTP_chromadb_client:
            client = chromadb.HttpClient(
                host=self.chromadb_host,
                port=self.chromadb_port,
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.local_vstore_path,
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )

        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )

        logger.debug(f"N entries: {client.get_collection(self.collection_name).count()}")
        logger.debug("Updated vectorstore connection")

        return vectorstore
    
    def get_vectorstore(self):
        """
        Public method to get the updated vectorstore connection.
        """
        return self._update_vectorstore_conn()