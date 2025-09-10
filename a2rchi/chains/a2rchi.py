import a2rchi.chains.pipelines as A2rchiPipelines
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger

import chromadb
from chromadb.config import Settings
from langchain_chroma.vectorstores import Chroma

logger = get_logger(__name__)

class A2rchi():
    """
    Central class of the A2rchi framework.
    Connects your database with the Pipeline, 
    creates and executes your Pipeline.
    """

    def __init__(
            self,
            pipeline,
            *args,
            **kwargs
        ):
        self.update(pipeline)
        self.pipeline = pipeline
        self._init_vectorstore_params()

    def update(self, pipeline=None):
        """
        Read relevant configuration settings.
        Initialize the Pipeline: either passed as argument or from config file.
        """
        logger.debug("Loading config")
        self.config = load_config(map=True)
        if pipeline:
            self.pipeline=pipeline
        self.pipeline = self._create_pipeline_instance(
            self.pipeline,
            config=self.config
        )

    def _init_vectorstore_params(self):
        """
        Initialize the vectorstore parameters from the config.
        """

        dm_config = self.config["data_manager"]

        embedding_class_map = dm_config["embedding_class_map"]
        embedding_name = dm_config["embedding_name"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](
            **embedding_class_map[embedding_name]["kwargs"]
        )
        self.collection_name = dm_config["collection_name"] + "_with_" + embedding_name
        self.use_HTTP_chromadb_client = dm_config["use_HTTP_chromadb_client"]
        self.chromadb_host = dm_config["chromadb_host"]
        self.chromadb_port = dm_config["chromadb_port"]
        self.local_vstore_path = dm_config["local_vstore_path"]

        logger.info(f"Using collection: {self.collection_name}")

    def _create_pipeline_instance(self, class_name, *args, **kwargs):
        """
        Initialize the Pipeline chosen by the config.
        """
        logger.debug(f"Initializing Pipeline: {class_name}.")
        logger.debug("With args:")
        logger.debug(f"{args}")
        logger.debug("and kwargs:")
        logger.debug(f"{kwargs}")
        try:
            cls = getattr(A2rchiPipelines, class_name)
            return cls(*args, **kwargs)
        except AttributeError:
            raise ValueError(f"Class '{class_name}' not found in module")
        except Exception as e:
            raise RuntimeError(f"Error creating instance of '{class_name}': {e}")

    def _update_vectorstore(self):
        """
        Function to update the vectorstore connection.
        Called each time you invoke your Pipeline.
        """
        
        # connect to chromadb server
        client = None
        if self.use_HTTP_chromadb_client:
            client = chromadb.HttpClient(
                host=self.chromadb_host,
                port=self.chromadb_port,
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else: # TODO what is this?
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
        logger.debug("Updated chain with new vectorstore")

        return vectorstore

    def __call__(self, *args, **kwargs):
        """
        Updates the vectorstore connection,
        passes it to the Pipeline's retriever,
        and then invokes the Pipeline.
        """
        vectorstore = self._update_vectorstore()
        result = self.pipeline.invoke(vectorstore=vectorstore, *args, **kwargs)
        return result

    
    