from typing import Any, Dict, List, Optional, Tuple
from a2rchi.utils.config_loader import load_config

config = load_config()["chains"]["base"]

def stringify_history(chat_history: List[Tuple[str, str]]) -> str:
    """
    Format chat history from a list of tuples
    [ ("User": "message"), ("AI": "response"), ... ]
    to a single string
    "User: message\nAI: response..."
    """
    buffer = ""
    for dialogue in chat_history:
        if isinstance(dialogue, tuple) and dialogue[0] in config["ROLES"]:
            identity = dialogue[0]
            message = dialogue[1]
            buffer += identity + ": " + message + "\n"
        else:
            raise ValueError(
                "Error loading the chat history. Possible causes: " + 
                f"Unsupported chat history format: {type(dialogue)}."
                f"Unsupported role: {dialogue[0]}."

                f" Full chat history: {chat_history} "
            )

    return buffer

def tuplize_history(chat_history: str) -> List[Tuple[str, str]]:
    """
    Reverse the operaiton of get_chat_history.
    From a string, make a list of (identity, message).
    """
    history = []
    for line in chat_history.strip().splitlines():
        if ": " not in line:
            raise ValueError(f"Line does not contain valid format 'role: message': {line}")
        role, message = line.split(": ", 1)
        if role not in config["ROLES"]:
            raise ValueError(f"Unsupported role: {role}. Full line: {line}")
        history.append((role, message))
    return history