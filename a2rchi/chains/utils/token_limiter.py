from typing import List, Tuple, Any
from langchain_core.documents import Document
from langchain_core.language_models.base import BaseLanguageModel
from langchain_core.prompts.base import BasePromptTemplate

from a2rchi.utils.config_loader import load_config
from a2rchi.utils.logging import get_logger
from a2rchi.chains.utils import history_utils

logger = get_logger(__name__)
config = load_config(map=False)

class TokenLimiter:
    def __init__(
        self,
        llm : BaseLanguageModel,
        max_tokens: int = 1e10,
        prompt: BasePromptTemplate = None,
        reserved_tokens: int = 0,
        min_history_messages: int = 2,
        min_docs: int = 0,
        large_msg_fraction: float = 0.5, 
    ):
        """
        Args:
            llm: The LLM object, should have get_num_tokens(text) method,
                 else 4 chars per token is used.
            max_tokens: Max total token count allowed, used if no limit is set via config or the LLM model.
            prompt: if passed, used to reserve tokens from the maximum allowed.
            reserved_tokens: additional tokens reserved (default: 0).
            min_history_messages: Minimum number of history items to keep.
            min_docs: Minimum number of documents to keep.
            large_msg_fraction: Fraction of budget above which a single history
                                message is considered "very large".
        """
        self.llm = llm
        self.reserved_tokens = reserved_tokens
        self.prompt_tokens = self.safe_token_count(prompt.format(**{v: "" for v in prompt.input_variables})) # TODO fix
        self.max_tokens = self.get_max_tokens(max_tokens)
        self.effective_max_tokens = self.calculate_effective_max_tokens()

        self.min_history_messages = min_history_messages
        self.min_docs = min_docs
        self.large_msg_threshold = int(self.effective_max_tokens * large_msg_fraction)
        self.INPUT_SIZE_WARNING = "WARNING: your last message is too large for the model A2rchi is running on. Please reduce the size of your message, and try again. The variable {var} was found to be too large."

    def calculate_effective_max_tokens(self) -> int:
        """
        Returns the effective allowed max tokens, which will be used to cut down history and docs.
        This is calculated as the total allowed max tokens by a model/config/class
        with the reserved and prompt tokens subtracted.
        """
        eff_max = self.max_tokens - self.reserved_tokens - self.prompt_tokens
        if eff_max < 100:
            logger.warning("The effective max tokens allowed is very low!")
            logger.warning(f"Effective max ({eff_max}) = max. tokens ({self.max_tokens}) - reserved tokens ({self.reserved_tokens}) - prompt tokens ({self.prompt_tokens})") 
        if eff_max <= 0:
            logger.error("The effective max tokens is below 0! Setting it to default value of 1000 and hoping everything goes well..")
            logger.error(f"Effective max ({eff_max}) = max. tokens ({self.max_tokens}) - reserved tokens ({self.reserved_tokens}) - prompt tokens ({self.prompt_tokens})") 
            return 1000
        logger.info(f"Setting effective max tokens to {eff_max}")
        return eff_max

    def get_max_tokens(self, max_tokens: int = 1e10) -> int:
        """
        Return the maximum tokens as the minimum of:
        1. upper bound set by user via config
        2. upper bound set by TokenLimiter class
        3. upper bounf set by LLM model (if we can find it)
        """
        config_max_tokens = self.get_max_tokens_from_config()
        llm_max_tokens = self.get_max_tokens_from_llm()
        max_tokens = self.safe_token_value(max_tokens)
        return min(max_tokens, config_max_tokens, llm_max_tokens)
            
    def get_max_tokens_from_config(self) -> int:
        config_max_tokens = config.get('chains', {}).get('chain', {}).get("MAX_MODEL_TOKENS", 1e10)
        return self.safe_token_value(config_max_tokens)

    def get_max_tokens_from_llm(self) -> int:
        llm_max_tokens = getattr(self.llm, 'max_tokens', 1e10)
        return self.safe_token_value(llm_max_tokens)
        
    def safe_token_value(self, count) -> int:
        try:
            count = int(count)
            if count <= 0:
                raise Exception(f"Value of count is too low ({count}).")
            return count
        except Exception as e:
            logger.warning(e)
            return 1e10

    def safe_token_count(self, text: str) -> int:
        # Validate and sanitize input
        if text is None:
            logger.warning("Received None for text, using empty string")
            text = ""
        elif not isinstance(text, str):
            logger.warning(f"Expected string, got {type(text).__name__}. Attempting conversion.")
            try:
                text = str(text)
            except Exception as e:
                logger.warning(f"Could not convert to string ({e}), using empty string")
                text = ""

        try:
            count = self.llm.get_num_tokens(text)
            if count is None or count < 0:
                raise Exception(f"Token count is {count}")
            return count
        except Exception as e:
            fallback = max(len(text) // 4, 1)
            logger.warning(f"get_num_tokens failed ({e}), using fallback: {fallback}")
            return fallback

    def prune_inputs_to_token_limit(
        self,
        docs: List[Document] = None,
        history: List[Tuple[str, str]] | str = None,
        **kwargs: Any
    ) -> Tuple[List[Document], List[Tuple[str, str]]]:
        """
        Reduce input variable tokens below limit.
        Everything else in the prompt is already accounted for via the effective max tokens.
        History and documents are dealt with according to priority as below,
        other input variables (extras) are removed last, since we don't know what they are.

        Priority:
        1a. Remove large history messages
        1b. Remove old history messages
        2. Remove last documents
        3. Remove extras
        Never remove user question! If user question is larger than effective max tokens
        """
        
        extras = {}
        docs = docs or []
        history = history or []

        # Validate and collect extras
        for k, v in kwargs.items():
            if not isinstance(v, str):
                raise ValueError(f"Extra variable '{k}' must be a string, got {type(v)}")
            extras[k] = v

        if not docs and not history:
            return [], []
        
        # if history is passed as a string, make a tuple so we can easily remove old messages
        # but remember at the end to return it as a string
        orig_history_str = False
        if type(history) is str:
            history = history_utils.tuplize_history(history)
            orig_history_str = True
        
        # store original length to compare at the end
        orig_docs = len(docs)
        orig_history = len(history)
                
        extra_tokens = [self.safe_token_count(v) for v in extras.values()]
        doc_tokens = [self.safe_token_count(d.page_content) for d in docs]
        history_tokens = [self.safe_token_count(h[1]) for h in history]

        def total_tokens():
            return sum(history_tokens) + sum(doc_tokens) + sum(extra_tokens)

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
            _ = history.pop(0)
            removed_tokens = history_tokens.pop(0)
            logger.info(f"Removed old message ({removed_tokens} tokens) from history")

        # --- Step 2: Reduce documents ---
        while total_tokens() > self.effective_max_tokens and len(docs) > self.min_docs:
            _ = docs.pop(0)
            removed_tokens = doc_tokens.pop(0)
            logger.info(f"Removed document ({doc_tokens} tokens)")

        # --- Step 3: Remove extras (last resort) ---
        if total_tokens() > self.effective_max_tokens and extras:
            sorted_extras = sorted(extras.items(), key=lambda kv: extra_tokens[kv[0]], reverse=True)
            for key, tcount in sorted_extras:
                if total_tokens() <= self.effective_max_tokens:
                    break
                logger.info(f"Removed extra '{key}' ({tcount} tokens)")
                del extras[key]
                del extra_tokens[key]

        logger.info(
            f"Reduced from {orig_docs} docs + {orig_history} history items "
            f"to {len(docs)} docs + {len(history)} history items + "
            f"{len(extras)} extras, {total_tokens()} tokens total "
            f"({self.effective_max_tokens} effective maximum allowed)"
        )

        if orig_history_str:
            history = history_utils.stringify_history(history)

        return {'docuents': docs, 'history': history, **extras}
    
    def check_input_size(
        self,
        input: str
    ):
        """
        Check if input is too large
        """
        if self.safe_token_count(input) > self.effective_max_tokens:
            return False
        return True
