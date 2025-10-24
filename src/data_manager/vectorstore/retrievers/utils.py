from typing import Dict, Tuple

from src.utils.logging import get_logger

logger = get_logger(__name__)

INSTRUCTION_AWARE_MODELS = [
    "Qwen/Qwen3-Embedding-0.6B",
    "Qwen/Qwen3-Embedding-4B",
    "Qwen/Qwen3-Embedding-8B",
]
        
def supports_instructions(embedding_name: str, dm_config: Dict[str, any]) -> Tuple[str, bool]:
    embedding_kwargs = dm_config["embedding_class_map"][embedding_name]["kwargs"]
    embedding_model = embedding_kwargs.get("model") or embedding_kwargs.get("model_name")
    return embedding_model, embedding_model in INSTRUCTION_AWARE_MODELS

def make_instruction_query(instructions: str, query: str) -> str:
    return f"Instruct: {instructions}\nQuery:{query}"

