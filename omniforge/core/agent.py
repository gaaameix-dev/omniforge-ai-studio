"""Agent runtime: one role template + LLM + skills + tools + bus access."""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .budget import Budget
from .events import Event
from .llm import BaseLLM
from .memory import Blackboard
from .message_bus import MessageBus
from .registry import SkillRegistry, ToolRegistry
from .tasks import Run, Task


@dataclass
class AgentTemplate:
    """A role, not a running process. 120+ of these ship with the framework."""

    id: str
    name: str
    department: str
    description: str = ""
    skills: list[str] = field(default_factory=list)
    primary_skills: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    name_tokens: set[str] = field(default_factory=set)
    max_delegations: int = 2
    permissions: tuple[str, ...] = ("net.read",)


@dataclass
class SkillContext:
    """Everything a deterministic skill handler is allowed to touch."""

    task: Task
    run: Run
    agent: AgentTemplate
    context: str
    blackboard: Blackboard
    feedback: str | None = None
    delegate: Callable[..., Any] | None = None
    tool: Callable[..., Any] | None = None
    retrieved: str = ""

    def spawn(self, title: str, department: str, skill: str, description: str = "") -> dict:
        """Ask the orchestrator for a subtask (bounded, deduplicated)."""
        if self.delegate is None:
            return {"ok": False, "reason": "delegation is not available for this run"}
        return self.delegate(
            {"title": title, "department": department, "skill": skill, "description": description}
        )

    async def call_tool(self, name: str, **kwargs: Any) -> Any:
        """Call a registered tool through the permission and approval checks."""
        if self.tool is None:
            raise RuntimeError("tool calling is not available for this run")
        return await self.tool(name, **kwargs)


class Agent:
    """Binds a template to the shared runtime and executes one task."""

    def __init__(
        self,
        template: AgentTemplate,
        *,
        llm: BaseLLM,
        skills: SkillRegistry,
        tools: ToolRegistry,
        bus: MessageBus,
        blackboard: Blackboard,
        budget: Budget | None = None,
    ) -> None:
        self.template = template
        self.llm = llm
        self.skills = skills
        self.tools = tools
        self.bus = bus
        self.blackboard = blackboard
        self.budget = budget

    @property
    def id(self) -> str:
        return self.template.id

    async def _emit(self, type: str, run_id: str, task_id: str | None = None, **payload) -> None:
        await self.bus.publish(
            Event(type=type, run_id=run_id, task_id=task_id, agent_id=self.id, payload=payload)
        )

    async def _complete(self, system: str, prompt: str, run, task):
        """Call the model, streaming deltas as throttled events when enabled."""
        tracer = getattr(self, "tracer", None)
        if tracer is None:
            return await self._complete_inner(system, prompt, run, task)
        with tracer.span("llm", run_id=run.id, task=task.title,
                         model=getattr(self.llm, "model", self.llm.name),
                         prompt_chars=len(prompt)) as span:
            response = await self._complete_inner(system, prompt, run, task)
            span.attributes["completion_chars"] = len(getattr(response, "text", "") or "")
            span.attributes["tokens"] = (getattr(response, "prompt_tokens", 0)
                                         + getattr(response, "completion_tokens", 0))
            return response

    async def _complete_inner(self, system: str, prompt: str, run, task):
        if not getattr(self, "stream_tokens", True) or not hasattr(self.llm, "stream"):
            return await self.llm.complete(system, prompt)

        import time as _time

        buffer: list[str] = []
        state = {"last": 0.0}

        async def on_token(delta: str) -> None:
            buffer.append(delta)
            now = _time.monotonic()
            if now - state["last"] < 0.15 and sum(len(part) for part in buffer) < 120:
                return
            text = "".join(buffer)
            buffer.clear()
            state["last"] = now
            await self._emit("agent.token", run.id, task.id, agent=self.template.name,
                             delta=text)

        response = await self.llm.stream(system, prompt, on_token)
        if buffer:
            await self._emit("agent.token", run.id, task.id, agent=self.template.name,
                             delta="".join(buffer))
        return response

    async def execute(
        self,
        task: Task,
        run: Run,
        context: str,
        *,
        feedback: str | None = None,
        delegate: Callable[..., Any] | None = None,
        tool: Callable[..., Any] | None = None,
        retrieved: str = "",
    ) -> str:
        skill = self.skills.get(task.skill)
        if skill is None:
            raise KeyError(f"skill '{task.skill}' is not registered")

        await self._emit(
            "agent.started", run.id, task.id,
            agent=self.template.name, skill=skill.name, rework=bool(feedback),
        )
        ctx = SkillContext(
            task=task, run=run, agent=self.template, context=context,
            blackboard=self.blackboard, feedback=feedback, delegate=delegate,
            tool=tool, retrieved=retrieved,
        )

        prefer = getattr(self, "prefer_generators", True)
        use_handler = skill.handler is not None and (
            prefer or getattr(self.llm, "name", "") == "mock"
        )
        if use_handler:
            result = skill.handler(ctx)
            if inspect.isawaitable(result):
                result = await result
            output = str(result)
            mode = "deterministic"
            tokens = 0
        else:
            prompt = skill.render(task, context, run.goal, feedback)
            system = self.template.system_prompt or skill.prompt_system
            resp = await self._complete(system, prompt, run, task)
            output = (resp.text or "").strip()
            mode = f"llm:{resp.model}"
            tokens = resp.prompt_tokens + resp.completion_tokens
            if self.budget is not None:
                if resp.prompt_tokens or resp.completion_tokens:
                    self.budget.add(resp.prompt_tokens, resp.completion_tokens)
                else:
                    self.budget.add_estimate(prompt, output)

        self.blackboard.set(run.id, task.title, output)
        await self._emit(
            "agent.finished", run.id, task.id,
            agent=self.template.name, mode=mode, chars=len(output), tokens=tokens,
        )
        return output

    async def run_tool(self, name: str, **kwargs) -> Any:
        """Invoke a registered tool with this agent's permissions enforced."""
        if name not in self.tools:
            raise KeyError(f"tool '{name}' is not available to agent {self.id}")
        return await self.tools.call_checked(
            name, kwargs, permissions=self.template.permissions, agent_id=self.id
        )
