from typing import List, Tuple
from langchain_core.documents import Document
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)

class TokenLimiter:
    def __init__(
        self,
        llm,
        max_tokens: int,
        reserved_tokens: int = 1000,
        min_history_messages: int = 2,
        min_docs: int = 0,
        large_msg_fraction: float = 0.5, 
    ):
        """
        Args:
            llm: The LLM object, should have get_num_tokens(text) method,
                 else 4 chars per token is used.
            max_tokens: Max total token count allowed.
            reserved_tokens: Tokens reserved for prompt overhead.
            min_history_messages: Minimum number of history items to keep.
            min_docs: Minimum number of documents to keep.
            large_msg_fraction: Fraction of budget above which a single history
                                message is considered "very large".
        """
        self.llm = llm
        self.effective_max_tokens = max_tokens - reserved_tokens
        self.min_history_messages = min_history_messages
        self.min_docs = min_docs
        self.large_msg_threshold = int(self.effective_max_tokens * large_msg_fraction)
        self.QUESTION_SIZE_WARNING = "WARNING: your last message is too large for the model A2rchi is running on. Please reduce the size of your message, and try again."

    def safe_token_count(self, text: str) -> int:
        try:
            count = self.llm.get_num_tokens(text)
            if count is None or count < 0:
                count = max(len(text) // 4, 1)
                logger.warning(f"get_num_tokens returned invalid count, using fallback: {count}")
            return count
        except Exception as e:
            fallback = max(len(text) // 4, 1)
            logger.warning(f"get_num_tokens failed ({e}), using fallback: {fallback}")
            return fallback

    def reduce_tokens_below_limit(
        self,
        docs: List[Document] = None,
        history: List[Tuple[str, str]] = None,
    ) -> Tuple[List[Document], List[Tuple[str, str]]]:

        docs = docs or []
        history = history or []

        orig_docs = len(docs)
        orig_history = len(history)

        if not docs and not history:
            return [], []

        doc_tokens = [self.safe_token_count(d.page_content) for d in docs]
        history_tokens = [self.safe_token_count(h[1]) for h in history]

        def total_tokens():
            return sum(history_tokens) + sum(doc_tokens)

        # --- Step 1: Reduce history ---
        # 1a. Remove very large messages
        filtered_history = []
        filtered_history_tokens = []
        for msg, tcount in zip(history, history_tokens):
            if tcount <= self.large_msg_threshold:
                filtered_history.append(msg)
                filtered_history_tokens.append(tcount)
            else:
                logger.info(f"Removed very large message ({tcount} tokens) from history")

        history = filtered_history
        history_tokens = filtered_history_tokens

        # 1b. Remove oldest messages while over budget and above min_history_messages
        while total_tokens() > self.effective_max_tokens and len(history) > self.min_history_messages:
            removed_msg = history.pop(0)
            removed_tokens = history_tokens.pop(0)
            logger.info(f"Removed old message ({len(removed_tokens)} tokens) from history")

        # --- Step 2: Reduce documents ---
        num_docs = len(docs)
        while total_tokens() > self.effective_max_tokens and num_docs > self.min_docs:
            num_docs -= 1
            logger.info(f"Removed document ({doc_tokens[num_docs]} tokens)")

        reduced_docs = docs[:num_docs]

        logger.info(
            f"Reduced from {orig_docs} docs + {orig_history} history items "
            f"to {len(reduced_docs)} docs + {len(history)} history items, "
            f"{total_tokens()} tokens total "
            f"({self.effective_max_tokens} effective maximum allowed)"
        )

        return reduced_docs, history
    
    def check_question(
        self,
        question: str
    ):
        """
        Check if question itself is too large
        """
        if self.safe_token_count(question) > self.effective_max_tokens:
            return False
        return True
