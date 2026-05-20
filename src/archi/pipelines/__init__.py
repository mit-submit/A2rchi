"""Pipeline package exposing the available pipeline classes."""

import inspect
import sys
from importlib import import_module
from pathlib import Path

from src.utils.logging import get_logger

logger = get_logger(__name__)


_PIPELINE_EXPORTS = {
    "BasePipeline": (".classic_pipelines.base", "BasePipeline"),
    "GradingPipeline": (".classic_pipelines.grading", "GradingPipeline"),
    "ImageProcessingPipeline": (".classic_pipelines.image_processing", "ImageProcessingPipeline"),
    "QAPipeline": (".classic_pipelines.qa", "QAPipeline"),
    "BaseReActAgent": (".agents.base_react", "BaseReActAgent"),
    "CMSCompOpsAgent": (".agents.cms_comp_ops_agent", "CMSCompOpsAgent"),
}

# Locate the pipelines/agents/ directory
agents_dir = Path("/root/archi/src/archi/pipelines/extra_agents")

if agents_dir.exists() and agents_dir.is_dir():
    # Add the exact folder to sys.path
    extra_agents_path = str(agents_dir.resolve())
    if extra_agents_path not in sys.path:
        sys.path.insert(0, extra_agents_path)

    for file_path in agents_dir.glob("*.py"):
        if file_path.name == "__init__.py":
            continue

        # Because extra_agents is in sys.path, the module name is just the filename stem!
        # e.g., "wisdqm_agent" instead of "src.archi.pipelines.extra_agents.wisdqm_agent"
        module_path = file_path.stem

        try:
            # Import it absolutely as a top-level module (drop the package= argument)
            module = import_module(module_path)
            logger.info(f"Successfully imported module {module_path}")

            for name, obj in inspect.getmembers(module, inspect.isclass):
                if obj.__module__ == module.__name__:
                    if name in _PIPELINE_EXPORTS:
                        logger.error(
                            f"Pipeline export collision: '{name}' from {module_path} "
                            f"was skipped because it is already registered."
                        )
                        continue

                    # Register for regular lazy loading
                    _PIPELINE_EXPORTS[name] = (module_path, name)

        except Exception as e:
            logger.error(
                f"Failed to scan extra agent module {module_path}: {e}"
            )
else:
    logger.warning(
        f"Extra agents directory not found at {agents_dir}. Skipping dynamic load."
    )

__all__ = list(_PIPELINE_EXPORTS)




def __getattr__(name):
    target = _PIPELINE_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = target
    module = import_module(module_name, __name__)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + __all__)
