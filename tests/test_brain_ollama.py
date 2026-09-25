import asyncio
import json

import httpx
import pytest
from pydantic import ValidationError

from rahasya.brain.contracts import ToolChoice
from rahasya.brain.ollama import BrainError, OllamaClient
from rahasya.brain.settings import AgentRole, BrainSettings


@pytest.mark.asyncio
async def test_ollama_uses_local_schema_and_hardware_limits():
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3:4b"}]})
        payload = json.loads(request.content)
        assert payload["options"]["num_ctx"] == 4096
        assert payload["options"]["num_predict"] == 512
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["format"]["additionalProperties"] is False
        assert "tools" not in payload  # Decisions are schema constrained; Python executes validated modules.
        return httpx.Response(200, json={
            "done": True, "message": {"content": '{"module":null,"reason":"Nothing eligible"}'},
            "prompt_eval_count": 20, "eval_count": 10,
        })

    client = OllamaClient(BrainSettings(), transport=httpx.MockTransport(handle))
    try:
        assert await client.check() == ["qwen3:4b"]
        decision = await client.decide(AgentRole.COORDINATOR, "Choose", {}, ToolChoice)
        assert decision.module is None
        assert (client.input_tokens, client.output_tokens) == (20, 10)
        assert all(request.url.host == "127.0.0.1" for request in calls)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_missing_models_does_not_download_or_fallback():
    requests = []

    def handle(request):
        requests.append(request.method)
        return httpx.Response(200, json={"models": []})

    client = OllamaClient(BrainSettings(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(BrainError, match="Download"):
            await client.check()
        assert requests == ["GET"]
    finally:
        await client.close()


@pytest.mark.parametrize("body", [
    {"done": True, "message": {"content": "not json"}},
    {"done": True, "message": {"content": '{"module":"x","reason":"x","shell":"rm"}'}},
    {"done": False, "message": {"content": '{"module":null,"reason":"x"}'}},
    {"done": True, "done_reason": "length", "message": {"content": '{"module":null,"reason":"x"}'}},
])
@pytest.mark.asyncio
async def test_malformed_or_incomplete_output_is_rejected(body):
    client = OllamaClient(BrainSettings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=body)))
    try:
        with pytest.raises(BrainError):
            await client.decide(AgentRole.SOCIAL, "Choose", {}, ToolChoice)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_cancellation_releases_shared_inference_gate():
    entered = asyncio.Event()

    async def blocked(request):
        entered.set()
        await asyncio.Event().wait()

    slow = OllamaClient(BrainSettings(), transport=httpx.MockTransport(blocked))
    fast = OllamaClient(BrainSettings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "done": True, "message": {"content": '{"module":null,"reason":"done"}'},
    })))
    try:
        task = asyncio.create_task(slow.decide(AgentRole.SOCIAL, "Choose", {}, ToolChoice))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await asyncio.wait_for(fast.decide(AgentRole.SOCIAL, "Choose", {}, ToolChoice), 1)).module is None
    finally:
        await slow.close()
        await fast.close()


@pytest.mark.parametrize("url", ["https://ollama.com", "http://localhost:11434/api/chat", "http://user:secret@localhost:11434"])
def test_remote_or_credentialed_endpoint_is_rejected(url):
    with pytest.raises(ValidationError):
        BrainSettings(base_url=url)


@pytest.mark.asyncio
async def test_redirect_is_not_followed():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(302, headers={"Location": "https://remote.example/api/tags"})

    client = OllamaClient(BrainSettings(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(BrainError, match="HTTP 302"):
            await client.check()
        assert len(calls) == 1
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_request_deadline_cancels_simulated_inference():
    async def handle(request):
        await asyncio.sleep(1)

    client = OllamaClient(BrainSettings(request_timeout_seconds=0.01), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(BrainError, match="deadline"):
            await client.decide(AgentRole.SOCIAL, "Choose", {}, ToolChoice)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_failure_is_actionable_and_has_no_fallback():
    calls = []

    def handle(request):
        calls.append(request.url.host)
        raise httpx.ConnectError("connection refused")

    client = OllamaClient(BrainSettings(), transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(BrainError, match="Cannot reach local Ollama"):
            await client.check()
        assert calls == ["127.0.0.1"]
    finally:
        await client.close()
