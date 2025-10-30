import re
from typing import Callable, Tuple

from src.a2rchi.pipelines.classic_pipelines.utils import history_utils
from src.a2rchi.pipelines.classic_pipelines.utils.prompt_validator import SUPPORTED_INPUT_VARIABLES
from src.utils.logging import get_logger

logger = get_logger(__name__)

class PromptFormatter:
    """
    Class to format a prompt template.
    The formatting includes:
        - adding supported 'context items': user question, chat history, documents, etc.
        - adding/removing special characters (to interplay with particular models) or formatting (e.g. html)
        - tokenizing the prompt
    This happens via the main function of the class, format_prompt().
    Each chain/model should have its own instance of this class.
    """

    def __init__(self,
            tokenizer,
            strip_html:bool = False
        ):
        self.tokenizer = tokenizer
        self.special_tokens = tokenizer.special_tokens_map
        self.apply_format = self._get_formatter()
        self.strip_html = strip_html
        self.tag_roles = {
            "question": "user",
            "documents": "assistant",
            "condensed_question": "user"
        }

    def format_prompt(self, prompt: str) -> Tuple[str, str]:
        """
        Main function of the class.
        Inputs:
        prompt (str): prompt template
        Outputs:
        (formatted_prompt, end_tag) (Tuple[str, str]): tuple of formatted prompt and tag as strings
        """

        # pre templating operations
        prompt = self._strip_tags(prompt)

        # optional prompt manipulations
        if self.strip_html:
            prompt = self._strip_html(prompt)

        # apply defined template
        prompt = self.apply_format(prompt)

        return prompt
    
    def _strip_tags(self, text: str) -> str:
        # Remove all <tags> and </tags> from the prompt
        if len(text) == 0: return text
        logger.debug("Stripping tags from prompt.")
        pattern = re.compile(rf"</?({'|'.join(map(re.escape, SUPPORTED_INPUT_VARIABLES))})>", re.IGNORECASE)
        return pattern.sub("", text)

    def _strip_html(self, text: str) -> str:
        # remove html from a string
        from html import unescape
        logger.debug("Stripping html from prompt.")
        text = unescape(text)
        return re.sub(r'<[^>]+>', '', text)
    
    def _find_tags_pattern(self):
        # Regex to capture <tag> ... </tag> blocks
        pattern = re.compile(
            rf"<({'|'.join(SUPPORTED_INPUT_VARIABLES)})>(.*?)</\1>",
            re.DOTALL | re.IGNORECASE
        )
        return pattern
    
    def _tuplize_tagged_prompt(self, text: str) -> Tuple[dict]:
        """
        Given a prompt divided by a given set of supported tags, split it up into a tuple.
        """
        
        pattern = self._find_tags_pattern()
        result = []
        pos = 0  # current scanning position

        for match in pattern.finditer(text):
            start, end = match.span()

            # If there's system text before this tag
            if start > pos:
                system_text = text[pos:start].strip()
                if system_text:
                    result.append({"role": "system", "content": system_text})

            tag_type = match.group(1).lower()
            tag_content = match.group(2).strip()
            if tag_type == 'history' and len(tag_content) > 0:
                # history is treated differently: we add each user/AI message as its own tuple
                for message in history_utils.tuplize_history(tag_content):
                    result.append({"role": message[0], "content": message[1]})
            else:
                result.append({"role": self.tag_roles.get(tag_type, "system"), "content": tag_content})

            pos = end  # advance position

        # Any leftover text after last tag is also system
        if pos < len(text):
            system_text = text[pos:].strip()
            if system_text:
                result.append({"role": "system", "content": system_text})

        return result
    
    def _get_formatter(self) -> Callable:
        # return the function that will be used to format the prompt

        if self._check_instructor_template():
            return self._apply_instructor_template
        elif self._check_chat_template():
            return self._apply_chat_template
        else:
            return self._apply_base_template

    def _check_instructor_template(self) -> bool:
        return "[INST]" in self.special_tokens.get("additional_special_tokens", [])
    
    def _check_chat_template(self) -> bool:
        return "<|im_start|>" in self.special_tokens.get("additional_special_tokens", [])
    
    def _apply_base_template(self, prompt: str) -> str:
        logger.debug("Using base template.")
        return prompt, prompt[len(prompt)-15:]
    
    def _apply_instructor_template(self, prompt: str) -> str:
        logger.debug("Using instructor template.")
        return f"[INST] {prompt} [/INST]", "[/INST]"
    
    def _apply_chat_template(self, prompt: str) -> Tuple:
        logger.debug("Using chat template.")
        message = self._tuplize_tagged_prompt(prompt)
        return self.tokenizer.apply_chat_template(message, tokenize=False, add_generation_prompt=True), "assistant"
