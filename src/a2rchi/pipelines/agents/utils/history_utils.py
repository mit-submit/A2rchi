from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from src.utils.logging import get_logger

logger = get_logger(__name__)

def infer_speaker(speaker: str) -> type[BaseMessage]:
    """Infer the speaker type and return the appropriate message class."""
    if speaker.lower() in ["user", "human"]:
        return HumanMessage
    if speaker.lower() in ["agent", "ai", "assistant", "a2rchi"]:
        return AIMessage
    logger.warning("Unknown speaker type: %s. Defaulting to HumanMessage.", speaker)
    return HumanMessage