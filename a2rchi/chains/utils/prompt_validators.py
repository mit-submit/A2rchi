from langchain_core.prompts import PromptTemplate
from typing import List, Optional, Callable


class ValidatedPromptTemplate(PromptTemplate):
    def __init__(self, template: str, input_variables: Optional[List[str]] = None, validator: Optional[Callable[[str], None]] = None):
        """
        A PromptTemplate that validates the template string.
        
        Args:
            template (str): The prompt template string.
            input_variables (Optional[List[str]]): List of input variable names.
            validator (Optional[Callable[[str], None]]): Function to validate the template.
        """
        if validator:
            validator(template)
        super().__init__(template=template, input_variables=input_variables)



def validate_main_prompt(prompt: str):
    if "{context}" not in prompt or "{question}" not in prompt:
        raise ValueError(f"MAIN_PROMPT must include {{context}} and {{question}}. Found:\n{prompt}")

def validate_condense_prompt(prompt: str):
    if "{chat_history}" not in prompt or "{question}" not in prompt:
        raise ValueError(f"CONDENSE_PROMPT must include {{chat_history}} and {{question}}. Found:\n{prompt}")

# TODO: update this when RAG implemented for image processing too (e.g. require {context})
def validate_image_prompt(prompt: str):
    pass

# TODO: update this when RAG implemented for any/all of these steps as well (e.g. require {context})
# should be optional, maybe easier to have user specify use of RAG or not in config and go based on that...
def validate_grading_summary_prompt(prompt: str):
    if "{final_student_solution}" not in prompt:
        raise ValueError(f"GRADING_SUMMARY_PROMPT must include {{final_student_solution}}. Found:\n{prompt}")

def validate_grading_analysis_prompt(prompt: str):
    if not all(k in prompt for k in ["{official_explanation}", "{final_student_solution}", "solution_summary"]):
        raise ValueError(f"GRADING_ANALYSIS_PROMPT missing one of required keys. Found:\n{prompt}")

def validate_grading_final_grade_prompt(prompt: str):
    if not all(k in prompt for k in ["{rubric_text}", "{submission_text}", "{analysis}"]):
        raise ValueError(f"GRADING_FINAL_GRADE_PROMPT missing one of required keys. Found:\n{prompt}")