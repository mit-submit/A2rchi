"""Pipeline package exposing the available pipeline classes."""

from importlib import import_module


_PIPELINE_EXPORTS = {
    "BasePipeline": (".classic_pipelines.base", "BasePipeline"),
    "GradingPipeline": (".classic_pipelines.grading", "GradingPipeline"),
    "ImageProcessingPipeline": (".classic_pipelines.image_processing", "ImageProcessingPipeline"),
    "QAPipeline": (".classic_pipelines.qa", "QAPipeline"),
    "BaseReActAgent": (".agents.base_react", "BaseReActAgent"),
    "CMSCompOpsAgent": (".agents.cms_comp_ops_agent", "CMSCompOpsAgent"),
}

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
