from langchain_core.documents import Document
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class TokenLimiter:
    def __init__(self, llm, max_tokens: int, reserved_tokens: int = 1000):
        """
        args:
            llm: The LLM object, should have the get_num_tokens(text) method, else 4 chars per token is used 
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

        tokens = []
        for i, doc in enumerate(docs):
            try:
                doc_tokens = self.llm.get_num_tokens(doc.page_content)
                if doc_tokens is None or doc_tokens < 0:
                    doc_tokens = max(len(doc.page_content) // 4, 1)
                    logger.warning(f"Doc {i}: get_num_tokens returned {doc_tokens}, using fallback estimation")
                tokens.append(doc_tokens)
            except Exception as e:
                fallback_tokens = max(len(doc.page_content) // 4, 1)
                tokens.append(fallback_tokens)
                logger.warning(f"Doc {i}: get_num_tokens failed ({e}), using fallback: {fallback_tokens}")

        token_count = sum(tokens)
        num_docs = len(docs)

        while token_count > self.effective_max_tokens and num_docs > 0:
            num_docs -= 1
            token_count -= tokens[num_docs] if num_docs >= 0 else 0

        reduced_docs = docs[:num_docs]
        
        if token_count < sum(tokens):
            logger.info(f"Reduced documents from {len(docs)}, {sum(tokens)} tokens to {len(reduced_docs)}, {token_count} tokens")

        return reduced_docs