from typing import Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)


UNSAFE_PROMPT_WARNING = """
It looks as if your question may be unsafe. 
                
This may be due to issues relating to toxicity, hate, identity, violence, physical tones, sexual tones, profanity, or biased questions.

Please try to reformat your question.
"""

UNSAFE_OUTPUT_WARNING = """
The response to your question may be unsafe.

This may be due to issues relating to toxicity, hate, identity, violence, physical tones, sexual tones, profanity, or biased questions.

There are two ways to solve this:
    - generate the response
    - reformat your question so that it does not prompt an unsafe response.
"""

def check_safety(text: str, safety_checkers:list, text_type:str) -> Tuple[bool, str]:
    """
    Inputs:
    text: text to check the safety of
    safety_checkers: list of safety checks to use (e.g. SalesforceSafetyChecker)
    text_type: whether it's the input, output, etc. 

    Returns:
    Tuple of 
    flag: is given text safe or not
    str: warning message, if any
    """

    safety_results = [check(text) for check in safety_checkers]
    are_safe = all([r[1] for r in safety_results])

    if not are_safe:
        logger.warning(f"Given text of type {text_type} deemed unsafe.")
        for method, is_safe, report in safety_results:
            if not is_safe:
                logger.warning(method)
                logger.warning(report)

        if text_type == 'prompt':
            return False, UNSAFE_PROMPT_WARNING
        elif text_type == 'output':
            return False, UNSAFE_OUTPUT_WARNING
        else:
            return False, "Given text deemed unsafe."
    else:
        return True, ""