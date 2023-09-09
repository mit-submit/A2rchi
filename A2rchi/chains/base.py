"""Chain for chatting with a vector database."""
from __future__ import annotations

from A2rchi.chains.prompts import CONDENSE_QUESTION_PROMPT, QA_PROMPT
from A2rchi.utils.config_loader import Config_Loader

from langchain.base_language import BaseLanguageModel
from langchain.chains.combine_documents.stuff import StuffDocumentsChain
from langchain.chains.conversational_retrieval.base import BaseConversationalRetrievalChain
from langchain.chains.llm import LLMChain
from langchain.schema import BaseRetriever, Document
from langchain.schema.prompt_template import BasePromptTemplate
from typing import Any, Dict, List, Optional, Tuple


# DEFINITIONS
config = Config_Loader().config["chains"]["base"]


def _get_chat_history(chat_history: List[Tuple[str, str]]) -> str:
    buffer = ""
    for dialogue in chat_history:
        if isinstance(dialogue, tuple) and dialogue[0] in config["ROLES"]:
            identity = dialogue[0]
            message = dialogue[1]
            buffer += identity + ": " + message + "\n"
        else:
            raise ValueError(
                "Error loading the chat history. Possible causes: " + 
                f"Unsupported chat history format: {type(dialogue)}."
                f"Unsupported role: {dialogue[0]}."

                f" Full chat history: {chat_history} "
            )

    return buffer


class BaseSubMITChain(BaseConversationalRetrievalChain):
    """
    Chain for chatting with an index, specific for submit
    """
    retriever: BaseRetriever # Index to connect to
    max_tokens_limit: Optional[int] = None # restrict doc length to return from store, enforced only for StuffDocumentChain
    get_chat_history: Optional[function] = _get_chat_history

    def _reduce_tokens_below_limit(self, docs: List[Document]) -> List[Document]:
        num_docs = len(docs)

        if self.max_tokens_limit and isinstance(
            self.combine_docs_chain, StuffDocumentsChain
        ):
            tokens = [
                self.combine_docs_chain.llm_chain.llm.get_num_tokens(doc.page_content)
                for doc in docs
            ]
            token_count = sum(tokens[:num_docs])
            while token_count > self.max_tokens_limit:
                num_docs -= 1
                token_count -= tokens[num_docs]

        return docs[:num_docs]


    def _get_docs(self, question: str, inputs: Dict[str, Any]) -> List[Document]:
        docs = self.retriever.get_relevant_documents(question)
        return self._reduce_tokens_below_limit(docs)


    async def _aget_docs(self, question: str, inputs: Dict[str, Any]) -> List[Document]:
        docs = await self.retriever.aget_relevant_documents(question)
        return self._reduce_tokens_below_limit(docs)


    @classmethod
    def from_llm(
        cls,
        llm: BaseLanguageModel,
        retriever: BaseRetriever,
        condense_question_prompt: BasePromptTemplate = CONDENSE_QUESTION_PROMPT,
        chain_type: str = "stuff",
        verbose: bool = False,
        condense_question_llm: Optional[BaseLanguageModel] = None,
        combine_docs_chain_kwargs: Optional[Dict] = None,
        **kwargs: Any,
    ) -> BaseConversationalRetrievalChain:
        # Load chain from LLM
        combine_docs_chain_kwargs = combine_docs_chain_kwargs or {}
        _prompt = QA_PROMPT
        document_variable_name = "context"
        llm_chain = LLMChain(
            llm=llm,
            prompt=_prompt,
            verbose=verbose,
        )
        doc_chain = StuffDocumentsChain(
            llm_chain=llm_chain,
            document_variable_name=document_variable_name,
            verbose=verbose)

        _llm = condense_question_llm or llm
        condense_question_chain = LLMChain(
            llm=_llm, prompt=condense_question_prompt, verbose=verbose
        )

        return cls(
            retriever=retriever,
            combine_docs_chain=doc_chain,
            question_generator=condense_question_chain,
            **kwargs,
        )
