import asyncio
import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def _load_module():
    if "langchain_core.tools" not in sys.modules:
        tools_mod = ModuleType("langchain_core.tools")

        class StructuredTool:
            def __init__(self, *, func, coroutine):
                self._func = func
                self._coroutine = coroutine

            @classmethod
            def from_function(cls, *, func, coroutine, **kwargs):
                return cls(func=func, coroutine=coroutine)

            def invoke(self, args):
                return self._func(**args)

            async def ainvoke(self, args):
                return await self._coroutine(**args)

        tools_mod.StructuredTool = StructuredTool
        sys.modules["langchain_core"] = ModuleType("langchain_core")
        sys.modules["langchain_core.tools"] = tools_mod

    if "pydantic" not in sys.modules:
        pydantic_mod = ModuleType("pydantic")

        class BaseModel:
            pass

        def Field(default, **kwargs):
            return default

        pydantic_mod.BaseModel = BaseModel
        pydantic_mod.Field = Field
        sys.modules["pydantic"] = pydantic_mod

    repo = Path(__file__).resolve().parents[2]
    path = repo / ".scratch" / "build_vectorstore_tool.py"
    spec = importlib.util.spec_from_file_location("benchmark_vectorstore_tool", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Response:
    def __init__(self, payload=None):
        self._payload = payload or {"hits": []}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_async_hybrid_tool_is_cancellable(monkeypatch):
    module = _load_module()
    catalog = SimpleNamespace(base_url="http://catalog", _headers={}, timeout=20.0)
    tool = module.make_search_vectorstore_hybrid(catalog)

    def blocking_requests_get(*args, **kwargs):
        time.sleep(0.2)
        return _Response()

    class SlowAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            await asyncio.sleep(1.0)
            return _Response()

    monkeypatch.setattr(module.requests, "get", blocking_requests_get)
    monkeypatch.setattr(
        module,
        "httpx",
        SimpleNamespace(
            AsyncClient=SlowAsyncClient,
            Timeout=lambda *args, **kwargs: SimpleNamespace(args=args, kwargs=kwargs),
        ),
    )

    start = time.monotonic()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(tool.ainvoke({"query": "slow", "limit": 1}), timeout=0.05)
    assert time.monotonic() - start < 0.15


def test_sync_hybrid_tool_closes_catalog_connection(monkeypatch):
    module = _load_module()
    catalog = SimpleNamespace(base_url="http://catalog", _headers={"Authorization": "Bearer token"}, timeout=20.0)
    captured = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return _Response({"hits": [{"metadata": {"display_name": "doc"}, "snippet": "body", "score": 1.0}]})

    monkeypatch.setattr(module.requests, "get", fake_get)
    tool = module.make_search_vectorstore_hybrid(catalog)

    result = tool.invoke({"query": "query", "limit": 1})

    assert "doc" in result
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["headers"]["Connection"] == "close"
    assert captured["timeout"] == 20.0
