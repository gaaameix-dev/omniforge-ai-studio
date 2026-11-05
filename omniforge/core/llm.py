"""LLM backends: an OpenAI-compatible client and a deterministic mock.

The mock is not a toy. It lets the whole orchestrator - planning, routing,
dependency waves, approvals, streaming - run and be tested with no API key.
Swap in a real endpoint through OMNIFORGE_LLM_* and nothing else changes.
"""
from __future__ import annotations

import inspect
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

PLANNER_SYSTEM = (
    "You are the Master Orchestrator of OmniForge AI Studio, a multi-agent "
    "engineering studio. Break the user's goal into a small, executable plan of "
    "3 to 7 tasks. Each task is owned by one department and one skill. "
    "Reply with JSON only."
)

PLANNER_SCHEMA = """{
  "tasks": [
    {
      "key": "short_slug",
      "title": "Imperative task title",
      "description": "What the assigned agent must produce",
      "department": "<one department key>",
      "skill": "<one skill name from that department>",
      "depends_on": ["other_task_key"]
    }
  ]
}"""

# goal keyword -> (department, preferred skill)
KEYWORD_RULES: list[tuple[tuple[str, ...], str, str]] = [
    (("game", "npc", "quest", "level design", "gameplay", "player"), "game_development", "game_concept_design"),
    (("unreal", "unity", "godot", "physics", "shader", "engine"), "game_development", "engine_optimization"),
    (("agent", "multi-agent", "orchestrat", "llm", "prompt", "rag", "model"), "ai_agent_systems", "agent_architecture"),
    (("android", "ios", "mobile", "apk", "app store", "kotlin", "swift", "flutter"), "mobile", "android_implementation"),
    (("web", "website", "frontend", "backend", "react", "api", "saas", "dashboard"), "web", "frontend_implementation"),
    (("database", "schema", "sql", "postgres", "data model"), "web", "database_schema"),
    (("3d", "model", "texture", "rig", "character", "animation", "asset"), "three_d", "3d_modeling"),
    (("video", "audio", "sound", "music", "voice", "render", "graphic", "edit"), "media", "video_edit"),
    (("dataset", "data profile", "profile the data", "csv", "analytics", "spreadsheet"), "research", "data_profile"),
    (("research", "market", "competitor", "analysis", "analyse", "analyze", "report"), "research", "web_research"),
    (("test", "qa", "bug", "quality", "regression"), "qa", "test_plan"),
    (("deploy", "ci/cd", "ci", "cd", "docker", "kubernetes", "pipeline", "infra", "cloud"), "devops", "deploy_checklist"),
    (("launch", "roadmap", "product", "plan", "spec", "mvp"), "product_ops", "product_spec"),
]


@dataclass
class LLMResponse:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort JSON extraction from a model reply."""
    cleaned = strip_fences(text)
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(cleaned[start : end + 1])
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def heuristic_plan(goal: str, catalog: dict[str, list[str]]) -> dict[str, Any]:
    """Deterministic planner: keyword match -> department chain -> QA -> summary.

    Used by the mock backend and as the fallback when a live model returns
    something unparseable, so a run never dies on a bad completion.
    """
    text = goal.lower()
    picked: list[tuple[str, str]] = []
    for keywords, department, skill in KEYWORD_RULES:
        if any(k in text for k in keywords):
            if department not in [d for d, _ in picked]:
                picked.append((department, skill))

    # Always open with research so downstream agents share a common brief.
    if not picked:
        picked = [("web", "frontend_implementation")]
    build = [p for p in picked if p[0] not in {"research", "qa", "product_ops"}][:3]
    if not build:
        build = [("web", "frontend_implementation")]

    data_goal = any(
        k in text for k in ("dataset", "data profile", "profile the data", "csv", "analytics", "spreadsheet")
    )
    research_skill = "data_profile" if data_goal else "web_research"
    tasks: list[dict[str, Any]] = [
        {
            "key": "research",
            "title": "Research brief and constraints",
            "description": f"Collect what is known about the goal and list constraints, risks and references: {goal}",
            "department": "research",
            "skill": research_skill,
            "depends_on": [],
        }
    ]
    for idx, (department, skill) in enumerate(build, start=1):
        key = f"build_{idx}"
        tasks.append(
            {
                "key": key,
                "title": f"{department.replace('_', ' ').title()}: deliverable {idx}",
                "description": f"Produce the {skill.replace('_', ' ')} deliverable for: {goal}",
                "department": department,
                "skill": skill,
                "depends_on": ["research"],
            }
        )
    build_keys = [t["key"] for t in tasks[1:]]
    tasks.append(
        {
            "key": "qa",
            "title": "Test plan and review gate",
            "description": f"Review the produced work, list defects and write the test plan for: {goal}",
            "department": "qa",
            "skill": "test_plan",
            "depends_on": build_keys,
        }
    )
    tasks.append(
        {
            "key": "summary",
            "title": "Product spec and handoff summary",
            "description": f"Turn the results into a product spec, open risks and next steps for: {goal}",
            "department": "product_ops",
            "skill": "product_spec",
            "depends_on": ["qa"],
        }
    )
    return {"strategy": "heuristic", "tasks": tasks}


def estimate_tokens(text: str) -> int:
    """Rough token count (~4 characters per token); used when a stream sends no usage."""
    return max(1, len(text or "") // 4)


class BaseLLM:
    name = "base"

    async def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> LLMResponse:
        raise NotImplementedError

    async def stream(self, system: str, prompt: str, on_token, *,
                     json_mode: bool = False) -> LLMResponse:
        """Emit the answer incrementally. Backends without streaming emit one chunk."""
        response = await self.complete(system, prompt, json_mode=json_mode)
        if on_token is not None and response.text:
            result = on_token(response.text)
            if inspect.isawaitable(result):
                await result
        return response

    async def plan(self, goal: str, catalog: dict[str, list[str]]) -> dict[str, Any]:
        catalog_text = "\n".join(
            f"- {dept}: {', '.join(skills)}" for dept, skills in catalog.items()
        )
        prompt = (
            f"Goal:\n{goal}\n\nAvailable departments and skills:\n{catalog_text}\n\n"
            f"Return JSON matching this shape exactly:\n{PLANNER_SCHEMA}"
        )
        resp = await self.complete(PLANNER_SYSTEM, prompt, json_mode=True)
        data = parse_json_object(resp.text)
        if not data or not isinstance(data.get("tasks"), list) or not data["tasks"]:
            fallback = heuristic_plan(goal, catalog)
            fallback["strategy"] = f"{self.name}+heuristic_fallback"
            return fallback
        data["strategy"] = self.name
        return data

    async def synthesize(self, goal: str, results: list[dict[str, Any]]) -> str:
        body = "\n\n".join(f"## {r['title']} ({r['department']})\n{r['result']}" for r in results)
        prompt = (
            f"Goal: {goal}\n\nCompleted task outputs:\n{body}\n\n"
            "Write a short handoff summary: what was produced, what is still open, "
            "and the recommended next three actions."
        )
        resp = await self.complete("You are the Master Orchestrator writing a handoff.", prompt)
        return resp.text.strip()


class OpenAICompatibleLLM(BaseLLM):
    name = "openai-compatible"

    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 90.0,
                 temperature: float = 0.2, transport: Any | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.temperature = temperature
        self.transport = transport

    def _client_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        return kwargs

    async def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(f"{self.base_url}/chat/completions", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return LLMResponse(
            text=(choice.get("message") or {}).get("content", "") or "",
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
        )


    async def stream(self, system: str, prompt: str, on_token, *,
                     json_mode: bool = False) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "stream": True,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        chunks: list[str] = []
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            async with client.stream("POST", f"{self.base_url}/chat/completions",
                                     json=payload, headers=headers) as response:
                if response.status_code >= 400:
                    body = await response.aread()
                    raise httpx.HTTPStatusError(
                        f"{response.status_code}: {body[:200]!r}", request=response.request,
                        response=response,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in event.get("choices") or []:
                        delta = (choice.get("delta") or {}).get("content")
                        if delta:
                            chunks.append(delta)
                            if on_token is not None:
                                result = on_token(delta)
                                if inspect.isawaitable(result):
                                    await result
        text = "".join(chunks)
        return LLMResponse(text=text, model=self.model,
                           prompt_tokens=estimate_tokens(system + prompt),
                           completion_tokens=estimate_tokens(text))


class AnthropicLLM(BaseLLM):
    """Anthropic Messages API adapter."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.anthropic.com",
                 timeout: float = 90.0, transport: Any | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 4096,
            "system": system + ("\nReturn JSON only." if json_mode else ""),
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(f"{self.base_url}/v1/messages", json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        blocks = data.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict))
        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            prompt_tokens=int(usage.get("input_tokens") or 0),
            completion_tokens=int(usage.get("output_tokens") or 0),
        )


class GeminiLLM(BaseLLM):
    """Google Gemini generateContent adapter."""

    name = "gemini"

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://generativelanguage.googleapis.com/v1beta",
                 timeout: float = 90.0, transport: Any | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.transport = transport

    async def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> LLMResponse:
        payload: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2},
        }
        if json_mode:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        url = f"{self.base_url}/models/{self.model}:generateContent"
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if self.transport is not None:
            kwargs["transport"] = self.transport
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.post(url, json=payload, params={"key": self.api_key})
            resp.raise_for_status()
            data = resp.json()
        candidates = data.get("candidates") or [{}]
        parts = ((candidates[0].get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        usage = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            model=self.model,
            prompt_tokens=int(usage.get("promptTokenCount") or 0),
            completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        )


class MockLLM(BaseLLM):
    """Deterministic backend: same interface, no network, no key."""

    name = "mock"

    async def complete(self, system: str, prompt: str, *, json_mode: bool = False) -> LLMResponse:
        head = prompt.strip().splitlines()[0][:160] if prompt.strip() else "task"
        text = (
            "[mock output] No live model is connected, so this is a placeholder "
            f"deliverable for: {head}. Set OMNIFORGE_LLM_API_KEY and "
            "OMNIFORGE_MOCK_LLM=false to generate real content here."
        )
        return LLMResponse(text=text, model="mock-1")

    async def plan(self, goal: str, catalog: dict[str, list[str]]) -> dict[str, Any]:
        return heuristic_plan(goal, catalog)

    async def synthesize(self, goal: str, results: list[dict[str, Any]]) -> str:
        lines = [f"Handoff summary for: {goal}", ""]
        lines.append(f"Completed tasks: {len(results)}")
        for r in results:
            first = (r["result"] or "").strip().splitlines()[0][:120]
            lines.append(f"- {r['title']} [{r['department']}] -> {first}")
        lines.append("")
        lines.append("Next actions: connect a live model, review the generated artifacts, then run QA.")
        return "\n".join(lines)


def build_provider(settings, provider: str, model: str | None = None) -> BaseLLM:
    """One adapter for one provider, using that provider's credentials."""
    provider = (provider or "openai").lower()
    model = model or settings.llm_model
    if provider == "anthropic":
        return AnthropicLLM(settings.anthropic_api_key, model, settings.anthropic_base_url,
                            settings.request_timeout)
    if provider == "gemini":
        return GeminiLLM(settings.gemini_api_key, model, settings.gemini_base_url,
                         settings.request_timeout)
    return OpenAICompatibleLLM(settings.llm_base_url, settings.llm_api_key, model,
                               settings.request_timeout)


def build_llm(settings) -> BaseLLM:
    """Primary provider, plus any configured fallbacks behind a gateway."""
    from .gateway import ModelGateway

    if not settings.live_mode:
        return MockLLM()
    primary = build_provider(settings, settings.llm_provider)
    chain: list[BaseLLM] = [primary]
    for spec in getattr(settings, "llm_fallback", ()) or ():
        provider, _, model = spec.partition(":")
        if provider and settings.provider_key(provider):
            chain.append(build_provider(settings, provider, model or None))
    return ModelGateway(chain) if len(chain) > 1 else primary
