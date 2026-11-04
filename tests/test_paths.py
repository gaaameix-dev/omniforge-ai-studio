"""Paths the main suite does not reach: live adapters, SMTP, LLM critique, handlers."""
from __future__ import annotations

import json

import httpx
import pytest

from omniforge.core import llm as llm_module
from omniforge.core.llm import AnthropicLLM, GeminiLLM, LLMResponse, OpenAICompatibleLLM
from omniforge.core.mailer import SMTPMailer
from omniforge.core.review import Reviewer
from omniforge.core.tasks import Run, Task
from omniforge.skills import handlers as H
from conftest import make_settings


# --------------------------------------------------------------- adapters
async def test_openai_compatible_complete_shapes_request():
    seen: list = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append({"url": str(request.url), "body": json.loads(request.content),
                     "auth": request.headers.get("authorization")})
        return httpx.Response(200, json={
            "model": "gpt-4o-mini",
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 9, "completion_tokens": 4},
        })

    adapter = OpenAICompatibleLLM("https://api.test/v1", "sk-x", "gpt-4o-mini")
    adapter.__dict__["_transport"] = httpx.MockTransport(handler)

    original = httpx.AsyncClient

    class Patched(original):  # inject the mock transport
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(handler)
            super().__init__(**kwargs)

    llm_module.httpx.AsyncClient = Patched
    try:
        response = await adapter.complete("be brief", "hi", json_mode=True)
    finally:
        llm_module.httpx.AsyncClient = original

    assert response.text == "hello" and response.prompt_tokens == 9
    assert seen[0]["body"]["response_format"] == {"type": "json_object"}
    assert seen[0]["auth"] == "Bearer sk-x"
    assert seen[0]["url"].endswith("/chat/completions")


async def test_openai_compatible_raises_on_http_error():
    original = httpx.AsyncClient

    class Failing(original):
        def __init__(self, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda r: httpx.Response(429, text="slow"))
            super().__init__(**kwargs)

    llm_module.httpx.AsyncClient = Failing
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await OpenAICompatibleLLM("https://api.test/v1", "k", "m").complete("s", "p")
    finally:
        llm_module.httpx.AsyncClient = original


async def test_anthropic_and_gemini_error_paths():
    bad = httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        await AnthropicLLM("k", "claude", transport=bad).complete("s", "p")
    with pytest.raises(httpx.HTTPStatusError):
        await GeminiLLM("k", "gemini", transport=bad).complete("s", "p")


# ------------------------------------------------------------------ mailer
class _FakeSMTP:
    sent: list = []
    fail = False

    def __init__(self, host, port, timeout=20):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        return None

    def login(self, user, password):
        return None

    def send_message(self, message):
        if _FakeSMTP.fail:
            raise OSError("smtp refused")
        _FakeSMTP.sent.append(message["Subject"])


async def test_smtp_mailer_success_and_failure(monkeypatch):
    import smtplib

    _FakeSMTP.sent.clear()
    _FakeSMTP.fail = False
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    mailer = SMTPMailer("smtp.test", 587, "user", "pw", "from@test")
    assert await mailer.send("to@test", "hello", "body") is True
    assert _FakeSMTP.sent == ["hello"]

    _FakeSMTP.fail = True
    assert await mailer.send("to@test", "again", "body") is False


# ------------------------------------------------------- review LLM critique
class _Critic:
    name = "critic"
    model = "critic"

    def __init__(self, payload: str) -> None:
        self.payload = payload

    async def complete(self, system, prompt, json_mode=False):
        return LLMResponse(text=self.payload, model="critic")


async def test_reviewer_blends_llm_critique():
    settings = make_settings("/tmp", review_with_llm=True)
    reviewer = Reviewer(settings, _Critic('{"score": 0.2, "issues": ["vague"]}'))
    task = Task(title="t", description="d", department="research", skill="web_research")
    result = await reviewer.review(task, "# Title\n\n- a\n- b\n- c")
    assert result.source == "heuristic+llm"
    assert not result.passed and "vague" in result.issues


async def test_reviewer_survives_bad_critique_json():
    settings = make_settings("/tmp", review_with_llm=True)
    reviewer = Reviewer(settings, _Critic("not json at all"))
    task = Task(title="t", department="research", skill="web_research")
    result = await reviewer.review(task, "# Title\n\n- a\n- b\n- c")
    assert result.source == "heuristic"
    assert result.passed


# ------------------------------------------------------------- skill handlers
class _Ctx:
    def __init__(self, spawned=None):
        self.task = Task(title="Task", department="product_ops", skill="x")
        self.run = Run(goal="Build a mobile puzzle game")
        self.agent = None
        self.context = "### Upstream\n- one\n- two"
        self.blackboard = None
        self.feedback = None
        self.retrieved = ""
        self._spawned = spawned if spawned is not None else []

    def spawn(self, title, department, skill, description=""):
        self._spawned.append(title)
        return {"ok": True, "task_id": "task_1", "title": title,
                "department": department, "skill": skill}

    async def call_tool(self, name, **kwargs):
        return json.dumps({"characters": 12, "words": 2, "lines": 1})


@pytest.mark.parametrize("name", sorted(H.HANDLERS))
async def test_every_handler_produces_output(name):
    ctx = _Ctx()
    result = H.HANDLERS[name](ctx)
    if hasattr(result, "__await__"):
        result = await result
    assert isinstance(result, str) and len(result) > 40
    assert any(marker in result for marker in ("#", "--", "name:", "jobs:"))


async def test_work_breakdown_spawns_subtasks():
    ctx = _Ctx()
    out = H.work_breakdown(ctx)
    if hasattr(out, "__await__"):
        out = await out
    assert len(ctx._spawned) == 2
    assert "Delegated" in out


async def test_data_profile_falls_back_when_the_tool_fails():
    class FailingCtx(_Ctx):
        async def call_tool(self, name, **kwargs):
            raise RuntimeError("tool down")

    out = await H.data_profile(FailingCtx())
    assert "Data profile" in out and "Context size" in out


# -------------------------------------------------------------- vault states
def test_vault_reports_unavailable_without_cryptography(tmp_path, monkeypatch):
    import omniforge.core.vault as vault_module

    monkeypatch.setattr(vault_module, "CRYPTO_AVAILABLE", False)
    vault = vault_module.Vault(tmp_path)
    assert vault.available is False
    assert vault.status()["backend"] == "unavailable"
    with pytest.raises(vault_module.VaultError):
        vault.encrypt("x")
    with pytest.raises(vault_module.VaultError):
        vault.decrypt("x")


# --------------------------------------------------------------- pg failures
def test_pg_connect_explains_missing_driver(monkeypatch):
    import builtins

    from omniforge.core import pg

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "psycopg":
            raise ImportError("no psycopg")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(pg.PostgresUnavailable) as exc:
        pg.connect("postgresql://localhost/x")
    assert "psycopg" in str(exc.value)


def test_pg_connect_reports_bad_url():
    pytest.importorskip("psycopg")
    from omniforge.core import pg

    with pytest.raises(pg.PostgresUnavailable):
        pg.connect("postgresql://user:pw@127.0.0.1:1/none?connect_timeout=1")


# ------------------------------------------------------------ integration edges
async def test_llm_integration_reports_failures(tmp_path):
    from omniforge.core.integrations import Integrations

    settings = make_settings(tmp_path, mock_llm=False, llm_api_key="sk-test")
    failing = Integrations(settings, transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    result = await failing.verify("llm")
    assert result["status"] == "error" and "500" in result["detail"]

    exploding = Integrations(
        settings,
        transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))),
    )
    result = await exploding.verify("llm")
    assert result["status"] == "error" and "ConnectError" in result["detail"]


async def test_mcp_stdio_missing_binary_is_reported():
    from omniforge.tools.mcp import MCPClient, MCPError

    client = MCPClient(["/nonexistent/binary"])
    with pytest.raises((MCPError, FileNotFoundError, OSError)):
        await client.start()
