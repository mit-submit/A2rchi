from a2rchi.chains.base import BaseQAChain, BaseGradingChain, BaseImageProcessingChain
from a2rchi.chains.prompts import read_prompt
from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger
from a2rchi.chains.prompts import PROMPTS
from a2rchi.chains.retrievers import SubMITRetriever, GradingRetriever

from chromadb.config import Settings
from langchain.prompts.prompt import PromptTemplate
from langchain_chroma.vectorstores import Chroma

from langchain.chains.conversation.memory import ConversationSummaryMemory
from langchain.chains import ConversationChain

import chromadb

logger = get_logger(__name__)

# at some point maybe won't make sense to keep one generic class...
class Chain() :

    def __init__(self, image_processing=False, grading=False):
        """
        Gets all the relavent files from the data directory and converts them
        into a format that the chain can use. Then, it creates the chain using 
        those documents.
        """
        self.kill = False
        self.image_processing = image_processing
        self.grading = grading

        self.update_config()


    # clean up this function a bit too much stuff, it's ugly
    def update_config(self):
        logger.info("Updating config")
        self.config = load_config(map=True)
        self.chain_config = self.config["chains"]["chain"]
        self.global_config = self.config["global"]
        self.utils_config = self.config["utils"]
        self.prompt_config = self.config["chains"]["prompts"]

        embedding_class_map = self.utils_config["embeddings"]["EMBEDDING_CLASS_MAP"]
        embedding_name = self.utils_config["embeddings"]["EMBEDDING_NAME"]
        self.embedding_model = embedding_class_map[embedding_name]["class"](**embedding_class_map[embedding_name]["kwargs"])
        self.collection_name = self.utils_config["data_manager"]["collection_name"] + "_with_" + embedding_name

        logger.info(f"Using collection: {self.collection_name}")

        model_class_map = self.chain_config["MODEL_CLASS_MAP"]
        model_name = self.chain_config.get("MODEL_NAME", None)
        condense_model_name = self.chain_config.get("CONDENSE_MODEL_NAME", model_name)

        # for grading service
        image_processing_model_name = self.chain_config.get("IMAGE_PROCESSING_MODEL_NAME", None)
        grading_final_grade_model_name = self.chain_config.get("GRADING_FINAL_GRADE_MODEL_NAME", None)
        grading_summary_model_name = self.chain_config.get("GRADING_SUMMARY_MODEL_NAME", grading_final_grade_model_name)
        grading_analysis_model_name = self.chain_config.get("GRADING_ANALYSIS_MODEL_NAME", grading_final_grade_model_name)

        if self.image_processing:
            self.image_processing_prompt = PROMPTS["IMAGE_PROCESSING"]
            self.image_processing_model = model_class_map[image_processing_model_name]["class"](**model_class_map[image_processing_model_name]["kwargs"])
            self._print_params("image processing", image_processing_model_name, model_class_map)

        elif self.grading:
            self.grading_final_grade_prompt = PROMPTS["GRADING_FINAL_GRADE"]
            self.grading_final_grade_llm = model_class_map[grading_final_grade_model_name]["class"](**model_class_map[grading_final_grade_model_name]["kwargs"])
            self._print_params("grading final grade", grading_final_grade_model_name, model_class_map)

            if grading_final_grade_model_name == model_name or "GRADING_ANALYSIS" not in PROMPTS:
                self.grading_analysis_prompt = None
                self.grading_analysis_llm = None
            else:
                self.grading_analysis_prompt = PROMPTS["GRADING_ANALYSIS"]
                self.grading_analysis_llm = model_class_map[grading_analysis_model_name]["class"](**model_class_map[grading_analysis_model_name]["kwargs"])
                self._print_params("grading analysis", grading_analysis_model_name, model_class_map)

            if grading_summary_model_name == model_name or "GRADING_SUMMARY" not in PROMPTS:
                self.grading_summary_prompt = None
                self.grading_summary_llm = None
            else:
                self.grading_summary_prompt = PROMPTS["GRADING_SUMMARY"]
                self.grading_summary_llm = model_class_map[grading_summary_model_name]["class"](**model_class_map[grading_summary_model_name]["kwargs"])
                self._print_params("grading summary", grading_summary_model_name, model_class_map)

        else:
            self.qa_prompt = PROMPTS["QA"]
            self.condense_question_prompt = PROMPTS["CONDENSE_QUESTION"]
            self.llm = model_class_map[model_name]["class"](**model_class_map[model_name]["kwargs"])
            
            if condense_model_name == model_name:
                self.condense_llm = None
            else:
                self.condense_llm = model_class_map[condense_model_name]["class"](**model_class_map[condense_model_name]["kwargs"])

            self._print_params("qa", model_name, model_class_map)
            self._print_params("condense", condense_model_name, model_class_map)

        # TODO: may want to add this back if we allow ConversationChain + ConversationSummaryMemory
        # self.memory_map = {}

    def update_vectorstore_and_create_chain(self):
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

        # construct chain
        vectorstore = Chroma(
            client=client,
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
        )

        if self.image_processing:
            
            # TODO: add retriever/RAG step for image processing if someone wants to do study on it

            chain = BaseImageProcessingChain.from_llm(
                llm=self.image_processing_model,
                prompt=self.image_processing_prompt,
                verbose=True,
            )


        # grading
        elif self.grading:
            retriever = GradingRetriever(
                vectorstore=vectorstore,
                search_kwargs={"k": self.utils_config["data_manager"]["num_documents_to_retrieve"]},
            )

            chain = BaseGradingChain.from_llm(
                llm=self.grading_final_grade_llm,
                summary_prompt=self.grading_summary_prompt,
                analysis_prompt=self.grading_analysis_prompt,
                final_grade_prompt=self.grading_final_grade_prompt,
                retriever=retriever,
                verbose=True,
            )

        # submit/general qa
        else:
            retriever = SubMITRetriever(
                vectorstore=vectorstore,
                search_kwargs={"k": self.utils_config["data_manager"]["num_documents_to_retrieve"]},
            )

            chain = BaseQAChain.from_llm(
                self.llm,
                retriever=retriever,
                qa_prompt=self.qa_prompt,
                condense_question_prompt=self.condense_question_prompt,
                condense_question_llm=self.condense_llm,
                return_source_documents=True,
            )

        logger.info(f"N entries: {client.get_collection(self.collection_name).count()}")
        logger.info("Updated chain with new vectorstore")

        # TODO: make it eas(ier) for users to use diff. LangChain chains 
        # # TODO: try to load/construct memory from past conversation if not in memory map (i.e. check postgres)
        # if conversation_id not in self.memory_map:
        #     self.memory_map[conversation_id] = ConversationSummaryMemory(
        #         llm=self.summary_llm,
        #         prompt=self.summary_prompt,
        #         max_token_limit=256,
        #     )

        # memory = self.memory_map[conversation_id]
        # chain = ConversationChain(llm=self.llm, memory=memory, prompt=self.qa_prompt, verbose=True)

        return chain


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
        similarity_result = vectorstore.similarity_search_with_score(input)
        score = (
            vectorstore.similarity_search_with_score(input)[0][1]
            if len(similarity_result) > 0
            else 1e10
        )

        # clean up vectorstore and client
        del vectorstore
        del client

        return score

    def __call__(self, *args, **kwargs):

        if self.image_processing:
            return self._call_image_processing(*args, **kwargs)

        elif self.grading:
            return self._call_grading(*args, **kwargs)

        else:
            return self._call_qa(*args, **kwargs)

    def _call_qa(self, history, conversation_id = None):
        """
        Call for the chain to answer a question

        Input: a history which is formatted as a list of 2-tuples, where the first element
        of the tuple is the author and the second element of the tuple is text that that 
        author wrote.

        Output: a dictionary containing the answer and some meta data. 
        """
        # create chain w/up-to-date vectorstore
        chain = self.update_vectorstore_and_create_chain()

        # seperate out the history into past interaction and current question input
        if len(history) > 0 and len(history[-1]) > 1:
            question = history[-1][1]
        else:
            logger.error("No question found")
            question = ""
        logger.info(f"Question: {question}")

        # get chat history if it exists
        chat_history = history[:-1] if history is not None else None

        # make the request to the chain
        inputs = {"question": question, "chat_history": chat_history}
        result = chain.invoke(inputs)

        # TODO: this was used with ConversationChain w/ConversationSummaryMemory
        # answer = chain(question)
        # answer['answer'] = answer['response']
        # answer['source_documents'] = []
        logger.info(f"Answer: {result['answer']}")
        logger.debug(f"Chat history: {result['chat_history']}")
        logger.debug(f"Sources: {result['source_documents']}")

        # delete chain object to release chain, vectorstore, and client for garbage collection
        del chain

        return result

    def _call_image_processing(self, images):
        """
        Call for image processing chain to take image to text.

        Input: a list of base64 encoded images
        Output: text extracted from the images
        """
        # create chain w/up-to-date vectorstore
        image_processing_chain = self.update_vectorstore_and_create_chain()

        logger.info("Converting solution images to text")
        text_from_images = image_processing_chain.run(images=images)
        logger.info("Images converted to text")

        # delete chain object to release chain, vectorstore, and client for garbage collection
        del image_processing_chain

        return text_from_images["text"]


    def _call_grading(self, submission_text, rubric_text, additional_comments=""):
        """
        Call for grading chain to grade a submission.

        Input: a submission text, rubric text, and additional comments
        Output: a dictionary containing the grade, feedback, and some metadata.
        """
        # create chain w/up-to-date vectorstore
        grading_chain = self.update_vectorstore_and_create_chain()

        logger.info("Grading submission")
        grading_result = grading_chain.run(
            submission_text=submission_text,
            rubric_text=rubric_text,
            additional_comments=additional_comments,
        )
        logger.info("Submission graded")

        # delete chain object to release chain, vectorstore, and client for garbage collection
        del grading_chain

        return grading_result

    
    def _print_params(self, name, model_name, model_class_map):
        """ Print the parameters of the model. """
        params_str = "\n".join([f"\t\t\t{param}: {value}" for param, value in model_class_map[model_name]["kwargs"].items()])
        logger.info(f"Using {name} model {model_name} with parameters:\n{params_str}")