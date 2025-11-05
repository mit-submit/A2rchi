"""Image processing pipeline."""

from __future__ import annotations

from typing import Any, Dict, List

from src.a2rchi.pipelines.classic_pipelines.utils.chain_wrappers import ChainWrapper
from src.a2rchi.pipelines.classic_pipelines.chains import ImageLLMChain
from src.a2rchi.pipelines.classic_pipelines.base import BasePipeline
from src.a2rchi.utils.output_dataclass import PipelineOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ImageProcessingPipeline(BasePipeline):
    """Pipeline dedicated to processing and analysing images."""

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        super().__init__(config, *args, **kwargs)

        self._image_llm_chain = ImageLLMChain(
            llm=self.llms['image_processing_model'],
            prompt=self.prompts['image_processing_prompt'],
        )
        self.image_processing_chain = ChainWrapper(
            chain=self._image_llm_chain,
            llm=self.llms['image_processing_model'],
            prompt=self.prompts['image_processing_prompt'],
            required_input_variables=[],
            **kwargs,
        )

    def invoke(
        self,
        images: List[Any],
        **kwargs,
    ) -> PipelineOutput:
        logger.info("Processing %s images.", len(images))
        text_from_image = self.image_processing_chain.invoke(inputs={"images": images})
        answer = text_from_image.get("answer") if isinstance(text_from_image, dict) else text_from_image
        metadata = {} if not isinstance(text_from_image, dict) else {k: v for k, v in text_from_image.items() if k != "answer"}
        return PipelineOutput(
            answer=answer or "",
            source_documents=[],
            intermediate_steps=[],
            metadata=metadata,
        )
