import re
from typing import List, Optional

from langchain_core.prompts import PromptTemplate

from src.utils.logging import get_logger

logger = get_logger(__name__)

SUPPORTED_INPUT_VARIABLES = [
    "full_history", # full history between user and agent
    "history", # history trimmed of the last user message
    "question", # last user message
    "retriever_output", # output of retriever from the vectorstore
    "condensed_output" # output of condensing step
    # TODO should support any given chain's output
]

class ValidatedPromptTemplate(PromptTemplate):
    """
    A PromptTemplate that validates the template string.
    
    Args:
        name (str): The name of the prompt.
        prompt_template (str): The prompt template string.
        input_variables (Optional[List[str]]): List of input variable names.
    """

    def __init__(self, name: str, prompt_template: str, input_variables: Optional[List[str]] = None):

        # if input_variables is passed, we check that they exist as {placeholders} in the prompt
        # else, we automatically find what the input variables are by reading the prompt {placeholders}
        if input_variables is None:
            input_variables = self._find_input_variables(prompt_template)
        else:
            self._check_input_variables(prompt_template, input_variables)
        prompt_template = self._add_tags(prompt_template)
        logger.info(f"Prompt {name} has been validated.")
        super().__init__(template=prompt_template, input_variables=input_variables)

    def _check_input_variables(self, prompt_template: str, input_variables: List[str]):
        """
        For all input variables, checks that they are supported,
        and a corresponding {placeholder} exists in the prompt string.
        """
        for var in input_variables:
            if var not in SUPPORTED_INPUT_VARIABLES:
                raise ValueError(f"Input variable: {var} is not supported.")
        template_vars = [f"{{{var}}}" for var in input_variables]
        for template_var in template_vars:
            if template_var not in prompt_template:
                raise ValueError(f"Input variable '{template_var}' not found in the main prompt template.")
        logger.info(f"Prompt validated to use input variables: {input_variables}")

    def _find_input_variables(self, prompt_template: str) -> list[str]:
        """
        Finds {input variables} placeholders in the prompt template string,
        distinguished by being in curly brackets, and being one of
        SUPPORTED_INPUT_VARIABLES.
        Returns the input variables it found as a list.
        """
        pattern = re.compile(r"\{([^}]+)\}")  # matches {tagname}
        found = set()
        for match in pattern.finditer(prompt_template):
            var = match.group(1).strip()
            if var in SUPPORTED_INPUT_VARIABLES:
                found.add(var)
            else:
                logger.warning(f"Found variable {var}, but it is not supported.")
        logger.debug(f"Found input variables {list(found)}")
        return list(found)

    def _add_tags(self, prompt_template: str) -> str:
        """
        Add <tags> around the context items we support.
        This is used to splice up the prompt in PromptFormatter.
        """
        pattern = re.compile(r"\{([^}]+)\}")  # matches {tagname}

        def replacer(match):
            tag = match.group(1).strip().lower()
            if tag in SUPPORTED_INPUT_VARIABLES:
                return f"<{tag}> {{{tag}}} </{tag}>"
            return match.group(0)  # leave unchanged if not supported

        prompt_template = pattern.sub(replacer, prompt_template)
        logger.debug("Added tags to prompt.")
        return prompt_template
