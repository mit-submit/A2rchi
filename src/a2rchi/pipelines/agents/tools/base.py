"""Abstract base class for all tools."""

from typing import Callable
from langchain.tools import tool


def create_abstract_tool(
    *,
    name: str = "abstract_tool",
    description: str = "Abstract base tool.",
) -> Callable:

    @tool(name, description=description)
    def _abstract_tool(query: str) -> str:
        """An abstract tool that does nothing."""
        ...
        return "This is an abstract tool. Please implement specific functionality."
    
    return _abstract_tool
