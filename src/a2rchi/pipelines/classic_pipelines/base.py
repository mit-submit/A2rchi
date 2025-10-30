"""Base pipeline definition used by all concrete pipelines."""

from __future__ import annotations

from typing import Any, Dict

from src.a2rchi.pipelines.classic_pipelines.utils.prompt_utils import read_prompt
from src.a2rchi.pipelines.classic_pipelines.utils.prompt_validator import ValidatedPromptTemplate
from src.a2rchi.utils.output_dataclass import PipelineOutput
from src.utils.logging import get_logger

logger = get_logger(__name__)


class BasePipeline:
    """
    BasePipeline provides a foundational structure for building pipeline classes that
    process user queries using configurable language models and prompts.
    """

    def __init__(
        self,
        config: Dict[str, Any],
        *args,
        **kwargs,
    ) -> None:
        self.config = config
        self.a2rchi_config = self.config["a2rchi"]
        self.dm_config = self.config["data_manager"]
        self.pipeline_config = self.a2rchi_config["pipeline_map"][self.__class__.__name__]
        self._init_llms()
        self._init_prompts()

    def update_retriever(self, vectorstore):
        self.retriever = None

    def invoke(self, *args, **kwargs) -> PipelineOutput:
        return PipelineOutput(
            answer="Stat rosa pristina nomine, nomina nuda tenemus.",
            source_documents=[],
            intermediate_steps=[],
        )

    def _init_llms(self) -> None:
        """Initialise language models declared for the pipeline."""

        model_class_map = self.a2rchi_config["model_class_map"]
        models_config = self.pipeline_config.get("models", {})
        self.llms: Dict[str, Any] = {}

        all_models = dict(models_config.get("required", {}), **models_config.get("optional", {}))
        initialised_models: Dict[str, Any] = {}

        for model_name, model_class_name in all_models.items():
            if model_class_name in initialised_models:
                self.llms[model_name] = initialised_models[model_class_name]
                logger.debug(
                    "Reusing initialised model '%s' of class '%s'",
                    model_name,
                    model_class_name,
                )
                continue

            model_entry = model_class_map[model_class_name]
            model_class = model_entry["class"]
            model_kwargs = model_entry["kwargs"]
            instance = model_class(**model_kwargs)
            self.llms[model_name] = instance
            initialised_models[model_class_name] = instance

    def _init_prompts(self) -> None:
        """Initialise prompts defined in pipeline configuration."""

        prompts_config = self.pipeline_config.get("prompts", {})
        required = prompts_config.get("required", {})
        optional = prompts_config.get("optional", {})
        all_prompts = {**optional, **required}

        self.prompts: Dict[str, ValidatedPromptTemplate] = {}
        for name, path in all_prompts.items():
            if not path:
                continue
            try:
                prompt_template = read_prompt(path)
            except FileNotFoundError as exc:
                if name in required:
                    raise FileNotFoundError(
                        f"Required prompt file '{path}' for '{name}' not found: {exc}"
                    ) from exc
                logger.warning(
                    "Optional prompt file '%s' for '%s' not found or unreadable: %s",
                    path,
                    name,
                    exc,
                )
                continue
            self.prompts[name] = ValidatedPromptTemplate(
                name=name,
                prompt_template=prompt_template,
            )
