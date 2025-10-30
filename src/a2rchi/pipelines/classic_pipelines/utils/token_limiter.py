from typing import Any, Dict, List, Tuple

from langchain_core.documents import Document
from langchain_core.language_models.base import BaseLanguageModel
from langchain_core.prompts.base import BasePromptTemplate

from src.a2rchi.pipelines.classic_pipelines.utils import history_utils
from src.utils.logging import get_logger

logger = get_logger(__name__)

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
        unprunable_input_variables: List[str] = ["question"]
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
        self.unprunable_input_variables = unprunable_input_variables
    
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
        llm_max_tokens = self.get_max_tokens_from_llm()
        max_tokens = self.safe_token_value(max_tokens)
        return min(max_tokens, llm_max_tokens)

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
        question: str = "",
        history: List[Tuple[str, str]] | str = [],
        **kwargs: Any
    ) -> Dict[List[Document], List[Tuple[str, str]]]:
        """
        Reduce input variable tokens below limit.
        Everything else in the prompt is already accounted for via the effective max tokens.
        History and documents are dealt with according to priority as below,
        other input variables (extras) are removed last, since we don't know what they are.
        Document lists are inferred by the variable type.
        If multiple document lists are passed, we alternate removing the last from each.
        Never remove unprunables or the user question.

        Priority:
        1a. Remove large history messages
        1b. Remove old history messages
        2. Remove last documents
        3. Remove extras
        """

        # this will be the output of this function
        pruned_inputs = {}

        # count tokens of the question
        question_tokens = self.safe_token_count(question)
                
        # Validate and collect docs, extras
        docs_lists, docs_vars = [], []
        extras = {}
        extra_tokens = {}
        for k, v in kwargs.items():
            # check if the variable is a list/tuple of Documents
            if (isinstance(v, tuple) or isinstance(v, list)) and len(v) > 0:
                if hasattr(v[0], 'page_content'):
                    docs_lists.append(v)
                    docs_vars.append(k)
                    continue
            elif not isinstance(v, str):
                raise ValueError(f"Extra variable '{k}' must be a string, got {type(v)}")
            extras[k] = v
            extra_tokens[k] = self.safe_token_count(v)
        
        # if history is passed as a string, make a tuple so we can easily remove old messages
        # but remember at the end to return it as a string
        orig_history = 0
        orig_history_str = False
        history_tokens = []
        if history:
            orig_history_str = False
            if type(history) is str:
                history = history_utils.tuplize_history(history)
                orig_history_str = True
            orig_history = len(history)
            history_tokens = [self.safe_token_count(h[1]) for h in history]
        
        # separate documents lists we can prune from those we can't
        orig_docs_counts, doc_tokens = [], []
        prunable_docs_lists, prunable_indices = [], []
        if docs_lists:
            for docs_list in docs_lists:
                orig_docs_counts.append(len(docs_list))
                doc_tokens.append([self.safe_token_count(d.page_content) for d in docs_list])
            # Calculate indices before converting tuples to lists
            prunable_indices = [i for i, (docs_list, docs_var) in enumerate(zip(docs_lists, docs_vars)) if docs_var not in self.unprunable_input_variables]
            prunable_docs_lists = [list(docs_lists[i]) if isinstance(docs_lists[i], tuple) else docs_lists[i] for i in prunable_indices]
            for k, v in zip(docs_vars, docs_lists):
                if k in self.unprunable_input_variables:
                    pruned_inputs[k] = v

        def total_tokens():
            return question_tokens + sum(history_tokens) + sum(sum(x) for x in doc_tokens) + sum(extra_tokens.values())
        
        # --- Step 0: Leave question ---
        pruned_inputs['question'] = question

        # --- Step 1: Reduce history ---
        # 1a. Remove very large messages
        if history and 'history' not in self.unprunable_input_variables:
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

            pruned_inputs['history'] = history

        # --- Step 2: Reduce documents ---
        if prunable_docs_lists:
            # Remove one document at a time from each prunable docs list in round-robin fashion
            # Map prunable_docs_lists to their indices in docs_lists to update the correct doc_tokens
            
            while total_tokens() > self.effective_max_tokens and any(len(docs) > self.min_docs for docs in prunable_docs_lists):
                for idx, prunable_docs_list in enumerate(prunable_docs_lists):
                    if len(prunable_docs_list) > self.min_docs:
                        prunable_index = prunable_indices[idx]
                        prunable_docs_list.pop()
                        removed_tokens = doc_tokens[prunable_index].pop()
                        logger.info(f"Removed document ({removed_tokens} tokens) from docs list {prunable_index}")
                        if total_tokens() <= self.effective_max_tokens or not any(len(docs) > self.min_docs for docs in prunable_docs_lists):
                            break
            # Add back pruned docs lists to pruned_inputs
            for idx, prunable_docs_list in zip(prunable_indices, prunable_docs_lists):
                pruned_inputs[docs_vars[idx]] = prunable_docs_list

        # --- Step 3: Remove extras (last resort) ---
        extras_removed = []
        if total_tokens() > self.effective_max_tokens and extras:
            sorted_extras = sorted(extras.items(), key=lambda kv: extra_tokens[kv[0]], reverse=True)
            for key, tcount in sorted_extras:
                if total_tokens() <= self.effective_max_tokens:
                    break
                if key not in self.unprunable_input_variables:
                    logger.info(f"Removed extra '{key}' ({tcount} tokens)")
                    extras_removed.append(key)
                    del extras[key]
                    del extra_tokens[key]
        pruned_inputs.update(**extras)

        logger.info(
            f"Reduced from "
            f"{ sum(orig_docs_counts) } docs "
            f"+ {orig_history} history items "
            f"{ ' + '.join(extras.keys()) }"
            f" to { sum(len(pruned_inputs[docs]) for docs in docs_vars) } docs + {len(history)} history items "
            f"{ ' + '.join(extras_removed)}: "
            f"{total_tokens()} tokens total "
            f"({self.effective_max_tokens} effective maximum allowed)"
        )

        if orig_history_str:
            pruned_inputs['history'] = history_utils.stringify_history(pruned_inputs['history'])

        return pruned_inputs
    
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
