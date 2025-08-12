from langchain_core.prompts import PromptTemplate
from typing import List, Optional
from a2rchi.utils.logging import get_logger

logger = get_logger(__name__)


class ValidatedPromptTemplate(PromptTemplate):
    def __init__(self, name: str, prompt_template: str, input_variables: Optional[List[str]] = None):
        """
        A PromptTemplate that validates the template string.
        
        Args:
            name (str): The name of the prompt.
            prompt_template (str): The prompt template string.
            input_variables (Optional[List[str]]): List of input variable names.
        """

        self._validate_prompt(name, prompt_template, input_variables)
        super().__init__(template=prompt_template, input_variables=input_variables)


    def _validate_prompt(self, name, prompt_template: str, input_variables: Optional[List[str]]):
        if input_variables:
            template_vars = [f"{{{var}}}" for var in input_variables]
            for template_var in template_vars:
                if template_var not in prompt_template:
                    raise ValueError(f"Input variable '{template_var}' not found in the main prompt template.")
        logger.info(f"Prompt '{name}' is valid with input variables: {input_variables}")