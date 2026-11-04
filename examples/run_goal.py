"""Minimal script: submit one goal and print the result. No API key needed.

Run it from the repository root:  python examples/run_goal.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omniforge.agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
from omniforge.config import Settings
from omniforge.core.llm import build_llm
from omniforge.core.orchestrator import Orchestrator


async def main() -> None:
    settings = Settings.from_env()
    skills = build_skill_registry()
    orch = Orchestrator(
        settings=settings,
        llm=build_llm(settings),
        skills=skills,
        agents=build_agent_registry(skills),
        tools=build_tool_registry(str(settings.data_dir), settings),
    )

    sub = orch.bus.subscribe()
    run = await orch.submit("Build a 2D mobile puzzle game with 20 levels and a leaderboard")

    async def tail():
        while True:
            event = await sub.next(timeout=0.5)
            if event is None:
                if orch._jobs[run.id].done():
                    return
                continue
            if event.type.startswith("task."):
                print(f"  {event.type:14s} {event.payload.get('title', '')}")

    tailer = asyncio.create_task(tail())
    await orch.wait(run.id)
    await tailer

    print(f"\nstatus: {run.status.value}")
    for task in run.plan.tasks:
        print(f"  {task.status.value:8s} {task.department:18s} {task.skill}")
    print("\n" + (run.summary or ""))


if __name__ == "__main__":
    asyncio.run(main())
