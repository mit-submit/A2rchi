from typing import Any, Dict, List, Optional, Tuple
from a2rchi.utils.config_loader import load_global_config


def stringify_history(chat_history: List[Tuple[str, str]]) -> str:
    """
    Format chat history from a list of tuples
    [ ("User": "message"), ("AI": "response"), ... ]
    to a single string
    "User: message\nAI: response..."
    """
    if chat_history is None or type(chat_history) is not list:
        return chat_history
    
    buffer = ""
    for dialogue in chat_history:
        if isinstance(dialogue, tuple):
            identity = dialogue[0]
            message = dialogue[1]
            buffer += identity + ": " + message + "\n"
        else:
            raise ValueError(
                "Error loading the chat history. Possible causes: " + 
                f"Unsupported chat history format: {type(dialogue)}."
                f" Full chat history: {chat_history} "
            )

    return buffer

def tuplize_history(chat_history: str) -> List[Tuple[str, str]]:
    """
    Reverse the operaiton of get_chat_history.
    From a string, make a list of (identity, message).
    """
    if chat_history is None or type(chat_history) is not str or len(chat_history) == 0:
        return chat_history
    
    history = []
    for line in chat_history.strip().splitlines():
        if ": " not in line:
            raise ValueError(f"Line does not contain valid format 'role: message': {line}")
        role, message = line.split(": ", 1)
        history.append((role, message))
    return history
