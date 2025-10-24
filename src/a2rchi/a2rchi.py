import chromadb
from chromadb.config import Settings
from langchain_chroma.vectorstores import Chroma

import src.a2rchi.pipelines as A2rchiPipelines
import src.a2rchi.agents as A2rchiAgents
from src.utils.config_loader import load_config
from src.utils.logging import get_logger

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
        self.update(pipeline, config_name=kwargs.get("config_name", None))
        self.pipeline_name = pipeline
        self._init_vectorstore_params()

    def update(self, pipeline=None, config_name = None):
        """
        Read relevant configuration settings.
        Initialize the Pipeline: either passed as argument or from config file.
        """
        logger.debug("Loading config")
        self.config = load_config(map=True, name=config_name)
        if pipeline:
            self.pipeline_name=pipeline
        self.pipeline = self._create_pipeline_instance(
            self.pipeline_name,
            config=self.config
        )

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
            # TODO this is an extremely ugly hack while we decide how we split these
            if "agent" in class_name.lower():
                cls = getattr(A2rchiAgents, class_name)
            else:
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
        # TODO I think this should be moved eslewhere, and can be called by the agents/pipelines as needed
        # TODO this function can probably be put as util somewhere else then
        
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

    def _prepare_call_kwargs(self, kwargs):
        """Attach a freshly initialised vectorstore to the call kwargs."""
        call_kwargs = dict(kwargs)
        call_kwargs["vectorstore"] = self._update_vectorstore()
        return call_kwargs

    def supports_stream(self) -> bool:
        """Return True when the active pipeline exposes a synchronous stream."""
        return callable(getattr(self.pipeline, "stream", None))

    def supports_astream(self) -> bool:
        """Return True when the active pipeline exposes an async stream."""
        return callable(getattr(self.pipeline, "astream", None))

    def invoke(self, *args, **kwargs):
        """
        Updates the vectorstore connection,
        passes it to the Pipeline's retriever,
        and then invokes the Pipeline.
        """
        call_kwargs = self._prepare_call_kwargs(kwargs)
        return self.pipeline.invoke(*args, **call_kwargs)

    def stream(self, *args, **kwargs):
        """
        Stream the pipeline output if the underlying pipeline supports it.
        """
        if not self.supports_stream():
            raise AttributeError(f"Pipeline '{self.pipeline_name}' does not expose a 'stream' method.")
        call_kwargs = self._prepare_call_kwargs(kwargs)
        return self.pipeline.stream(*args, **call_kwargs)

    async def astream(self, *args, **kwargs):
        """
        Asynchronously stream the pipeline output if supported.
        """
        if not self.supports_astream():
            raise AttributeError(f"Pipeline '{self.pipeline_name}' does not expose an 'astream' method.")
        call_kwargs = self._prepare_call_kwargs(kwargs)
        async for event in self.pipeline.astream(*args, **call_kwargs):
            yield event

    def __call__(self, *args, **kwargs):
        return self.invoke(*args, **kwargs)

    


    