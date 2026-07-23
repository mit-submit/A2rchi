"""Guarded stubs for heavy optional deps the unit suite must not require.

Several unit files import modules whose import chains reach langchain_core /
langchain_community / langchain_text_splitters / nltk. Those packages exist in
CI and in the service images but not necessarily in a bare ``pip install -e .``
dev env. Each stub below is installed ONLY when the real distribution is
genuinely absent (``importlib.util.find_spec``), so environments with the real
packages are untouched — this is stronger than the per-file ``sys.modules``
guards it replaces, which depended on collection order and on the langsmith
pytest plugin having pre-imported langchain_core.

Centralized here so the stub set cannot drift across test files and so every
test file behaves the same standalone as in a full-directory run.
"""
import importlib.util
import sys
import types


def _absent(dist_top_level: str) -> bool:
    try:
        return importlib.util.find_spec(dist_top_level) is None
    except (ImportError, ModuleNotFoundError, ValueError):
        return True


def _module(name: str) -> types.ModuleType:
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


if _absent("langchain_core"):
    _module("langchain_core")
    _module("langchain_core.documents").Document = object
    _module("langchain_core.embeddings").Embeddings = object
    _module("langchain_core.vectorstores").VectorStore = object

if _absent("nltk"):
    nltk_module = _module("nltk")
    nltk_module.tokenize = types.SimpleNamespace(word_tokenize=lambda text: text.split())
    nltk_module.stem = types.SimpleNamespace(
        PorterStemmer=lambda: types.SimpleNamespace(stem=lambda w: w)
    )
    nltk_module.download = lambda *_args, **_kwargs: None

if _absent("langchain_text_splitters"):
    _module("langchain_text_splitters")

    class _DummyCharacterTextSplitter:
        def __init__(self, *args, **kwargs):
            pass

        def split_documents(self, docs):
            return docs

    _module("langchain_text_splitters.character").CharacterTextSplitter = (
        _DummyCharacterTextSplitter
    )

if _absent("langchain_community"):
    _module("langchain_community")
    _loaders = _module("langchain_community.document_loaders")

    class _DummyLoader:
        def __init__(self, *_args, **_kwargs):
            pass

        def load(self):
            return []

    _loaders.BSHTMLLoader = _DummyLoader
    _loaders.PyPDFLoader = _DummyLoader
    _loaders.PythonLoader = _DummyLoader
    _loaders.TextLoader = _DummyLoader
    _module("langchain_community.document_loaders.text").TextLoader = _DummyLoader
