import io
from markitdown import MarkItDown
from src.utils.logging import get_logger
# from src.interfaces.llm.llm_client import LLMClient

logger = get_logger(__name__)

class MarkitdownConvertor:

    def __init__(self):
        self.markitdown = MarkItDown(
            enable_plugins=True,
            # llm_client=llm_client,
            # llm_model=llm_model,
        )

    def convert(self, content: str, file_extension: str = ".html") -> str:
        """
        Convert the content to markdown using MarkItDown.
        Args:
            content: The content to convert.
            file_extension: The file extension of the content.
        Returns:
            The converted content.
        """
        logger.info(f"Converting content to markdown: {content}")
        result = self.markitdown.convert_stream(
            io.BytesIO(content.encode("utf-8")),
            file_extension=file_extension,   
        )
        logger.info(f"Markitdown result: {result.text_content if hasattr(result, 'text_content') else str(result)}")
        return result.text_content if hasattr(result, 'text_content') else str(result)