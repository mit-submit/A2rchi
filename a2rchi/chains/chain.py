import a2rchi.chains.workflows as A2rchiWorkflows
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger

import chromadb
from chromadb.config import Settings
from langchain_chroma.vectorstores import Chroma

logger = get_logger(__name__)

class Chain() :

    def __init__(self):
        """
        Gets all the relavent files from the data directory and converts them
        into a format that the workflow can use. Invokes the workflow with
        the updated documents.
        """
        self.update_config()

    def update_config(self):
        """
        Read relevant configuration settings.
        """
        logger.info("Updating config")
        self.config = load_config(map=True)
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.chains_config = self.config["chains"]

        embedding_class_map = self.utils_config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.utils_config["embeddings"]["EMBEDDING_NAME"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])
        self.collection_name = self.utils_config["data_manager"]["collection_name"] + "_with_" + embedding_name
        logger.info(f"Using collection: {self.collection_name}")

        self.workflow = self.create_workflow_instance(
            self.chains_config["chain"]["WORKFLOW"],
            self.chains_config["chain"]["WORKFLOW_KWARGS"]
        )

    def create_workflow_instance(self, class_name, *args, **kwargs):
        """
        Initialize the Workflow chosen by the config.
        """
        try:
            cls = getattr(A2rchiWorkflows, class_name)
            return cls(*args, **kwargs)
        except AttributeError:
            raise ValueError(f"Class '{class_name}' not found in module")
        except Exception as e:
            raise RuntimeError(f"Error creating instance of '{class_name}': {e}")

    def update_vectorstore(self):
        """
        Function to update the vectorstore with new files.
        Called each time you invoke your Workflow.
        """
        
        # connect to chromadb server
        client = None
        if self.utils_config["data_manager"]["use_HTTP_chromadb_client"]:
            client = chromadb.HttpClient(
                host=self.utils_config["data_manager"]["chromadb_host"],
                port=self.utils_config["data_manager"]["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.global_config["LOCAL_VSTORE_PATH"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )

        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )

        logger.info(f"N entries: {client.get_collection(self.collection_name).count()}")
        logger.info("Updated chain with new vectorstore")

        return vectorstore

    def similarity_search(self, input):
        """
        Perform similarity search with input against vectorstore.
        """
        # connect to chromadb server
        client = None
        if self.utils_config["data_manager"]["use_HTTP_chromadb_client"]:
            client = chromadb.HttpClient(
                host=self.utils_config["data_manager"]["chromadb_host"],
                port=self.utils_config["data_manager"]["chromadb_port"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        else:
            client = chromadb.PersistentClient(
                path=self.global_config["LOCAL_VSTORE_PATH"],
                settings=Settings(allow_reset=True, anonymized_telemetry=False),  # NOTE: anonymized_telemetry doesn't actually do anything; need to build Chroma on our own without it
            )
        
        # construct vectorstore
        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )

        # perform similarity search
        similarity_result = vectorstore.similarity_search_with_score(input, k=self.num_docs_to_retrieve)
        top_score = (
            vectorstore.similarity_search_with_score(input)[0][1]
            if len(similarity_result) > 0
            else 1e10
        )
        scores = [similarity_result[i][1] for i in range(len(similarity_result))]

        # clean up vectorstore and client
        del vectorstore
        del client

        return top_score, scores

    def __call__(self, *args, **kwargs):
        """
        Execute the Chain.
        Updates the vectorstore, passes it to the Workflow's retriever,
        and then invokes the Workflow.
        """

        vectorstore = self.update_vectorstore()
        self.workflow.update_retriever(vectorstore)
        return self.workflow.invoke(*args, **kwargs)

    # TODO write workflows for these too
    # def _call_image_processing(self, images):
    #     """
    #     Call for image processing chain to take image to text.

    #     Input: a list of base64 encoded images
    #     Output: text extracted from the images
    #     """
    #     # create chain w/up-to-date vectorstore
    #     image_processing_chain = self.update_vectorstore_and_create_chain()

    #     logger.info("Converting solution images to text")
    #     text_from_images = image_processing_chain.run(images=images)
    #     logger.info("Images converted to text")

    #     # delete chain object to release chain, vectorstore, and client for garbage collection
    #     del image_processing_chain

    #     return text_from_images["text"]


    # def _call_grading(self, submission_text, rubric_text, additional_comments=""):
    #     """
    #     Call for grading chain to grade a submission.

    #     Input: a submission text, rubric text, and additional comments
    #     Output: a dictionary containing the grade, feedback, and some metadata.
    #     """
    #     # create chain w/up-to-date vectorstore
    #     grading_chain = self.update_vectorstore_and_create_chain()

    #     logger.info("Grading submission")
    #     grading_result = grading_chain.run(
    #         submission_text=submission_text,
    #         rubric_text=rubric_text,
    #         additional_comments=additional_comments,
    #     )
    #     logger.info("Submission graded")

    #     # delete chain object to release chain, vectorstore, and client for garbage collection
    #     del grading_chain

    #     return grading_result

    
    
