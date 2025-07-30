from langchain_core.documents import Document
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class TokenLimiter:
    def __init__(self, llm, max_tokens: int, reserved_tokens: int = 1000):
        """
        args:
            llm: The LLM object, must have get_num_tokens(text) method.
            max_tokens_limit: Max total token count allowed.
        """
        self.llm = llm
        self.effective_max_tokens = max_tokens - reserved_tokens
        
    def reduce_tokens_below_limit(self, docs: list[Document]) -> list[Document]:
        """
        Reduce the number of documents to fit within the max token limit.
        """
        if not docs:
            return []

        tokens = [
            self.llm.get_num_tokens(doc.page_content) for doc in docs
        ]

        token_count = sum(tokens)
        num_docs = len(docs)

        while token_count > self.effective_max_tokens and num_docs > 0:
            num_docs -= 1
            token_count -= tokens[num_docs] if num_docs >= 0 else 0

        reduced_docs = docs[:num_docs]
        
        logger.info(f"Reduced documents from {len(docs)}, {sum(tokens)} tokens to {len(reduced_docs)}, {token_count} tokens")

        return reduced_docs