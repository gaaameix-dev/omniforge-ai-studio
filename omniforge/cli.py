"""Command line entry point: `omniforge run "<goal>"`, `omniforge serve`, etc."""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from datetime import datetime, timezone

from .agents.roles import build_agent_registry, build_skill_registry, build_tool_registry
from .config import Settings
from .core.llm import build_llm
from .core.metrics import Metrics
from .core.orchestrator import Orchestrator
from .core.store import RunStore


def _build(settings: Settings) -> Orchestrator:
    skills = build_skill_registry()
    return Orchestrator(
        settings=settings,
        llm=build_llm(settings),
        skills=skills,
        agents=build_agent_registry(skills),
        tools=build_tool_registry(str(settings.data_dir), settings),
        store=RunStore(settings.db_path),
        metrics=Metrics(),
    )


def _print_run(run) -> None:
    print(f"\nrun {run.id}  status={run.status.value}  tokens={run.tokens}  cost=${run.cost_usd:.4f}")
    if run.plan:
        print(f"strategy: {run.plan.strategy}\n")
        for task in run.plan.tasks:
            mark = {"done": "[ok]", "failed": "[!!]", "skipped": "[--]"}.get(task.status.value, "[..]")
            agent = task.agent_id.split("__")[-1].replace("_", " ") if task.agent_id else "-"
            score = f"{task.review_score:.2f}" if task.review_score is not None else "  - "
            print(f"  {mark} {task.department:18s} {task.skill:26s} -> {agent:28s} score={score}")
    if run.summary:
        print("\n--- handoff summary ---\n" + run.summary)


async def _cmd_run(args, settings: Settings) -> int:
    orch = _build(settings)
    run = await orch.submit(args.goal, auto_approve=args.auto_approve)
    sub = orch.bus.subscribe(run_id=run.id)

    async def tail() -> None:
        while True:
            event = await sub.next(timeout=0.5)
            if event is None:
                if orch._jobs.get(run.id) and orch._jobs[run.id].done():
                    return
                continue
            if event.type in {"task.started", "task.finished", "task.failed", "task.skipped",
                              "task.reviewed", "task.retrying", "approval.requested"}:
                extra = ""
                if event.type == "task.reviewed":
                    extra = f" score={event.payload.get('score')}"
                if event.type == "task.retrying":
                    extra = f" attempt={event.payload.get('attempt')} {event.payload.get('reason', '')[:60]}"
                print(f"  [{event.ts}] {event.type:18s} {event.payload.get('title', '')}{extra}",
                      flush=True)
            if args.watch and event.type in {"agent.token", "run.token", "task.debated",
                                            "budget.exceeded", "alerts.fired"}:
                if event.type in {"agent.token", "run.token"}:
                    sys.stdout.write(event.payload.get("delta", ""))
                    sys.stdout.flush()
                else:
                    print(f"  [{event.ts}] {event.type:18s} "
                          f"{json.dumps(event.payload)[:120]}", flush=True)
            if event.type == "approval.requested":
                if sys.stdin.isatty():
                    answer = input("    approve this task? [y/N] ").strip().lower()
                    orch.approve(run.id, event.task_id or "", answer.startswith("y"), "cli decision")
                else:
                    print("    no terminal attached: answer via /api/runs/{id}/approvals "
                          "or pass --auto-approve", flush=True)

    tailer = asyncio.create_task(tail())
    await orch.wait(run.id)
    await tailer
    sub.close()

    if args.json:
        print(json.dumps(run.to_dict(), indent=2))
    else:
        _print_run(run)
    return 0 if run.status.value == "done" else 1


async def _cmd_demo(args, settings: Settings) -> int:
    goals = [
        "Build a 2D mobile puzzle game with 20 levels and a leaderboard",
        "Design and deploy a SaaS dashboard for freelance invoicing",
    ]
    orch = _build(settings)
    for goal in goals:
        run = await orch.run_to_completion(goal, auto_approve=True)
        _print_run(run)
    return 0


def _cmd_serve(args, settings: Settings) -> int:
    import uvicorn

    from .api.app import create_app

    print(f"OmniForge dashboard: http://{args.host}:{args.port}")
    uvicorn.run(create_app(settings), host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_agents(args, settings: Settings) -> int:
    skills = build_skill_registry()
    registry = build_agent_registry(skills)
    agents = registry.all()
    if args.department:
        agents = [a for a in agents if a.department == args.department]
    if args.search:
        needle = args.search.lower()
        agents = [a for a in agents if needle in a.name.lower() or needle in a.description.lower()]
    print(f"{len(agents)} agent roles\n")
    for a in agents:
        print(f"  {a.department:18s} {a.name:34s} {a.description}")
    return 0


def _cmd_skills(args, settings: Settings) -> int:
    skills = build_skill_registry()
    rows = skills.describe()
    if args.department:
        rows = [r for r in rows if r["department"] == args.department]
    print(f"{len(rows)} skills\n")
    for r in rows:
        flag = "python" if r["deterministic"] else "llm   "
        print(f"  {r['department']:18s} {r['name']:26s} [{flag}] {r['description']}")
    return 0


def _cmd_departments(args, settings: Settings) -> int:
    skills = build_skill_registry()
    agents = build_agent_registry(skills)
    from .agents.departments import DEPARTMENTS

    print(f"{len(DEPARTMENTS)} departments\n")
    for key, meta in DEPARTMENTS.items():
        print(f"  {key:18s} {len(agents.by_department(key)):3d} agents  "
              f"{len(skills.by_department(key)):2d} skills  {meta['name']}")
    return 0


def _cmd_doctor(args, settings: Settings) -> int:
    import shutil
    import sys
    from pathlib import Path

    checks: list[tuple[str, bool, str]] = []
    checks.append(("python >= 3.10", sys.version_info >= (3, 10), sys.version.split()[0]))
    for module in ("fastapi", "uvicorn", "httpx", "pydantic"):
        try:
            __import__(module)
            checks.append((f"import {module}", True, "ok"))
        except ImportError as exc:  # noqa: F821
            checks.append((f"import {module}", False, str(exc)))
    checks.append(("dashboard present", (Path(__file__).resolve().parents[1] / "web" / "index.html").exists(), "web/index.html"))
    data_dir = Path(settings.data_dir)
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".write-probe"
        probe.write_text("ok")
        probe.unlink()
        checks.append(("data dir writable", True, str(data_dir)))
    except OSError as exc:
        checks.append(("data dir writable", False, str(exc)))
    checks.append(("model backend", True, "live" if settings.live_mode else "mock (set OMNIFORGE_LLM_API_KEY to go live)"))
    checks.append(("approval gates", True, "on" if settings.require_approval else "off"))
    checks.append(("token budget", True, str(settings.budget_tokens) if settings.budget_tokens else "unlimited"))
    try:
        import sqlite3

        checks.append(("sqlite (python module)", True, sqlite3.sqlite_version))
    except ImportError as exc:  # pragma: no cover - stdlib
        checks.append(("sqlite (python module)", False, str(exc)))
    if shutil.which("sqlite3"):
        checks.append(("sqlite3 cli (optional)", True, shutil.which("sqlite3") or ""))

    width = max(len(name) for name, _, _ in checks)
    for name, ok, detail in checks:
        print(f"  [{'ok' if ok else '!!'}] {name:<{width}}  {detail}")

    from .core.integrations import Integrations

    print()
    for row in Integrations(settings).snapshot():
        mark = "ok" if row["status"] == "connected" else "--"
        print(f"  [{mark}] {row['label']:<{width}}  {row['status']}: {row['detail'][:60]}")

    orch = _build(settings)
    run = asyncio.run(orch.run_to_completion("Smoke test: write a one page test plan", auto_approve=True))
    healthy = run.status.value == "done" and run.plan is not None and len(run.plan.tasks) > 0
    print(f"\n  [{'ok' if healthy else '!!'}] smoke run{'':<{max(0, width - 9)}}  {run.id} -> {run.status.value}")
    all_ok = all(ok for _, ok, _ in checks) and healthy
    print("\ndoctor:", "healthy" if all_ok else "problems found")
    return 0 if all_ok else 1


def _cmd_eval(args, settings: Settings) -> int:
    from .evals.runner import run_all

    cases = None
    if args.case:
        from .evals.fixtures import CASES

        cases = [c for c in CASES if c["name"] in args.case]
        if not cases:
            print(f"no eval case named {args.case}")
            return 2
    summary = asyncio.run(run_all(cases, settings))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"evals: {summary['passed']}/{summary['total']} passed\n")
        for result in summary["results"]:
            mark = "PASS" if result["passed"] else "FAIL"
            print(f"  [{mark}] {result['case']:<18} {result['done']}/{result['tasks']} tasks  "
                  f"{result['tokens']} tok  {result['duration_s']}s")
            for check in result["checks"]:
                if not check["passed"]:
                    print(f"         - {check['check']}: {check['detail']}")
    return 0 if summary["ok"] else 1


def _cmd_replay(args, settings: Settings) -> int:
    store = RunStore(settings.db_path)
    record = store.get_run(args.run_id)
    if record is None:
        print(f"no run {args.run_id}")
        return 2
    orch = _build(settings)
    run = asyncio.run(orch.run_to_completion(record["goal"], auto_approve=True,
                                             meta={"replay_of": args.run_id}))
    _print_run(run)
    return 0 if run.status.value == "done" else 1


def _cmd_history(args, settings: Settings) -> int:
    store = RunStore(settings.db_path)
    rows = store.list_runs(limit=args.limit)
    if not rows:
        print("no runs recorded yet")
        return 0
    for row in rows:
        counts = " ".join(f"{k}={v}" for k, v in sorted((row.get("counts") or {}).items()))
        print(f"  {row['created_at']}  {row['id']}  {row['status']:<9} "
              f"{row['tokens']:>7} tok  {counts}  {row['goal'][:52]}")
    return 0


def _cmd_kb(args, settings: Settings) -> int:
    store = RunStore(settings.db_path)
    from .core.knowledge import KnowledgeBase

    kb = KnowledgeBase(store)
    if not args.query:
        print(json.dumps(kb.stats(), indent=2))
        return 0
    hits = kb.search(args.query, limit=args.limit)
    if not hits:
        print("no matches")
        return 0
    for hit in hits:
        print(f"  {hit['score']:.3f}  {hit['title']}  [{hit['run_id']}]")
        print(f"         {hit['snippet'][:140].replace(chr(10), ' ')}")
    return 0


def _cmd_integrations(args, settings: Settings) -> int:
    from .core.integrations import Integrations

    integrations = Integrations(settings)
    if args.verify:
        asyncio.run(integrations.verify_all())
    rows = integrations.snapshot()
    width = max(len(r["label"]) for r in rows)
    for row in rows:
        mark = {"connected": "ok", "not_configured": "--", "error": "!!", "unverified": "??"}[row["status"]]
        print(f"  [{mark}] {row['label']:<{width}}  {row['status']:<14} {row['detail']}")
        if row["status"] != "connected" and row["requires"]:
            print(f"       requires: {row['requires']}")
    return 0 if integrations.get("sqlite") else 1


def _cmd_tools(args, settings: Settings) -> int:
    from .agents.roles import build_tool_registry

    registry = build_tool_registry(str(settings.data_dir), settings)
    print(f"{len(registry)} tools\n")
    for tool in sorted(registry.all(), key=lambda t: (t.category, t.name)):
        flags = []
        if tool.destructive:
            flags.append("destructive")
        if not tool.enabled:
            flags.append("disabled")
        if tool.permissions:
            flags.append(" ".join(tool.permissions))
        print(f"  {tool.category:<10} {tool.name:<26} {', '.join(flags) or '-'}")
        print(f"             {tool.description}")
    return 0


def _cmd_backup(args, settings: Settings) -> int:
    from .core.backup import create_backup, verify_backup

    if args.verify:
        report = verify_backup(args.verify)
        print(json.dumps(report, indent=2))
        return 0 if report["ok"] else 1
    try:
        info = create_backup(settings.data_dir)
    except FileNotFoundError as exc:
        print(str(exc))
        return 2
    print(json.dumps(info, indent=2))
    return 0


def _cmd_restore(args, settings: Settings) -> int:
    from .core.backup import restore_backup

    try:
        result = restore_backup(args.file, settings.data_dir, force=args.force)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        print(f"restore refused: {exc}")
        return 2
    print(json.dumps(result, indent=2))
    return 0


def _cmd_users(args, settings: Settings) -> int:
    from .core import auth

    store = RunStore(settings.db_path)
    if args.create:
        try:
            user = auth.register_user(store, args.create, args.password, args.name or "")
        except auth.AuthError as exc:
            print(f"cannot create user: {exc}")
            return 2
        if args.role and args.role != user["role"]:
            store._conn.execute("UPDATE users SET role=? WHERE id=?", (args.role, user["id"]))
            store._conn.commit()
            user["role"] = args.role
        print(f"created {user['email']} ({user['role']})")
        return 0
    users = store.list_users()
    if not users:
        print("no accounts yet - create one with: omniforge users --create you@example.com --password ...")
        return 0
    for user in users:
        state = "disabled" if user["disabled"] else "active"
        print(f"  {user['email']:<32} {user['role']:<8} {state:<9} {user['created_at']}")
    return 0


def _cmd_ui_audit(args, settings: Settings) -> int:
    from .core.ui_audit import audit

    report = audit()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if report["score"] >= 0.8 else 1
    print(f"ui audit: {report['passed']}/{report['total']} checks (score {report['score']})")
    for check in report["checks"]:
        mark = "ok  " if check["ok"] else "FAIL"
        print(f"  [{mark}] {check['check']:<18} {check['detail'][:50]}")
    return 0 if report["score"] >= 0.8 else 1


def _cmd_verify(args, settings: Settings) -> int:
    """Live checks: every configured provider is asked one real question."""
    from .core.integrations import Integrations

    if getattr(args, "live", False):
        return _verify_live(settings)

    settings_obj = settings
    statuses = asyncio.run(Integrations(settings_obj).verify_all()) if hasattr(
        Integrations, "verify_all") else []
    if not statuses:
        statuses = [asyncio.run(Integrations(settings_obj).verify(key))
                    for key in ("llm", "github", "mcp", "sqlite", "sandbox", "http")]
    for status in statuses:
        mark = {"connected": "ok", "not_configured": "--", "error": "!!"}.get(status["status"], "??")
        print(f"  [{mark}] {status['label']:22s} {status['status']:15s} {status['detail'][:60]}")
    return 0 if all(s["status"] in {"connected", "not_configured"} for s in statuses) else 1


def _verify_live(settings: Settings) -> int:
    """Actually call each configured provider once and print the round trip."""
    import asyncio as _asyncio

    from .core.embeddings import EmbeddingProvider
    from .core.llm import build_llm
    from .core.media import ImageProvider, SpeechProvider
    from .core.media_jobs import JobMediaProvider

    results: list[tuple[str, bool, str]] = []

    async def check_llm() -> None:
        llm = build_llm(settings)
        if getattr(llm, "name", "mock") == "mock":
            results.append(("llm", False, "mock backend: set OMNIFORGE_LLM_API_KEY"))
            return
        try:
            response = await llm.complete("Reply with one word.", "ping")
            results.append(("llm", bool(response.text), response.text[:40]))
        except Exception as exc:  # noqa: BLE001
            results.append(("llm", False, f"{type(exc).__name__}: {exc}"[:80]))

    async def check_image() -> None:
        provider = ImageProvider(settings.media_api_key, model=settings.media_model,
                                 base_url=settings.media_base_url)
        if not provider.enabled:
            results.append(("image", False, "no OMNIFORGE_MEDIA_API_KEY"))
            return
        try:
            payload = await provider.generate("a plain grey square", size="512x512")
            results.append(("image", len(payload) > 100, f"{len(payload)} bytes"))
        except Exception as exc:  # noqa: BLE001
            results.append(("image", False, f"{type(exc).__name__}: {exc}"[:80]))

    async def check_speech() -> None:
        provider = SpeechProvider(settings.media_api_key, base_url=settings.media_base_url)
        if not provider.enabled:
            results.append(("speech", False, "no OMNIFORGE_MEDIA_API_KEY"))
            return
        try:
            payload = await provider.speak("hello")
            results.append(("speech", len(payload) > 100, f"{len(payload)} bytes"))
        except Exception as exc:  # noqa: BLE001
            results.append(("speech", False, f"{type(exc).__name__}: {exc}"[:80]))

    async def check_jobs() -> None:
        provider = JobMediaProvider(settings.media_job_url, settings.media_job_key)
        results.append(("media-jobs", provider.enabled,
                        "configured" if provider.enabled else "no OMNIFORGE_MEDIA_JOB_URL"))

    async def check_embeddings() -> None:
        provider = EmbeddingProvider(settings.embedding_api_key, model=settings.embedding_model,
                                     base_url=settings.llm_base_url)
        if not provider.enabled:
            results.append(("embeddings", False, "no OMNIFORGE_EMBEDDING_API_KEY"))
            return
        try:
            vectors = await provider.embed(["ping"])
            results.append(("embeddings", bool(vectors and vectors[0]),
                            f"{len(vectors[0])} dims"))
        except Exception as exc:  # noqa: BLE001
            results.append(("embeddings", False, f"{type(exc).__name__}: {exc}"[:80]))

    async def run_all() -> None:
        await _asyncio.gather(check_llm(), check_image(), check_speech(), check_jobs(),
                              check_embeddings())

    _asyncio.run(run_all())
    failures = 0
    for name, ok, detail in results:
        mark = "ok  " if ok else "--  "
        if not ok and "no OMNIFORGE" not in detail and "mock backend" not in detail:
            failures += 1
        print(f"  [{mark}] {name:<12} {detail}")
    configured = sum(1 for _, ok, _ in results if ok)
    print(f"live: {configured}/{len(results)} providers answered")
    return 0 if failures == 0 else 1


def _cmd_tenants(args, settings: Settings) -> int:
    from .core.pg import PostgresUnavailable
    from .core.tenancy import TenantError, create_tenant, drop_tenant, list_tenants

    url = settings.db_url
    if not url.startswith(("postgres://", "postgresql://")):
        print("tenants need a PostgreSQL backend (set OMNIFORGE_DB_URL)")
        return 2
    try:
        if args.create:
            print(json.dumps(create_tenant(url, args.create), indent=2))
        elif args.drop:
            print(json.dumps(drop_tenant(url, args.drop), indent=2))
        else:
            tenants = list_tenants(url)
            print(f"{len(tenants)} tenant schema(s)")
            for tenant in tenants:
                print("  ", tenant["schema"])
    except (TenantError, PostgresUnavailable) as exc:
        print(f"error: {exc}")
        return 2
    return 0


def _cmd_completions(args, settings: Settings) -> int:
    """Print a shell completion script for the CLI commands."""
    commands = sorted({
        action.dest for action in _parser(settings)._subparsers._group_actions[0].choices.keys()
    }) if False else []
    names = ["run", "serve", "agents", "skills", "departments", "info", "demo", "doctor",
             "eval", "replay", "history", "kb", "metrics", "integrations", "tools",
             "backup", "restore", "users", "vault", "plugins", "automation", "usage",
             "loadtest", "db", "alerts", "worker"]
    if args.shell == "bash":
        print("_omniforge() { COMPREPLY=( $(compgen -W \"" + " ".join(names) +
              "\" -- \"${COMP_WORDS[1]}\") ); }\ncomplete -F _omniforge omniforge")
    else:
        print("#compdef omniforge\n_omniforge() { _arguments '1:command:(" +
              " ".join(names) + ")' }\n_omniforge \"$@\"")
    return 0


def _cmd_worker(args, settings: Settings) -> int:
    from .core.worker import Worker

    orch = _build(settings)
    worker = Worker(orch, worker_id=args.worker_id, lease_seconds=args.lease_seconds,
                    poll_seconds=args.poll)
    if args.once:
        result = asyncio.run(worker.run_once())
        print(json.dumps(result or {"processed": 0, "queued": orch.store.queued_count()}, indent=2))
        return 0
    print(f"worker {worker.worker_id} polling (max_runs={args.max_runs})")
    try:
        report = asyncio.run(worker.run_forever(max_runs=args.max_runs))
    except KeyboardInterrupt:  # pragma: no cover - interactive
        report = worker.status()
    print(json.dumps(report, indent=2))
    return 0


def _cmd_db(args, settings: Settings) -> int:
    from .core import pgmirror
    from .core.pg import PostgresUnavailable

    if args.sync:
        store = RunStore(settings.db_path)
        try:
            result = pgmirror.sync(store, args.sync)
        except PostgresUnavailable as exc:
            print(f"cannot sync: {exc}")
            return 2
        print(json.dumps(result, indent=2))
        return 0
    if args.verify:
        try:
            print(json.dumps(pgmirror.verify(args.verify), indent=2))
        except PostgresUnavailable as exc:
            print(f"cannot verify: {exc}")
            return 2
        return 0
    store = RunStore(settings.db_path)
    print(json.dumps({"active": "sqlite", "path": str(settings.db_path),
                      "rows": store.stats()}, indent=2))
    return 0


def _cmd_alerts(args, settings: Settings) -> int:
    from .core.alerts import AlertEngine
    from .core.mailer import build_mailer

    store = RunStore(settings.db_path)
    engine = AlertEngine(store, settings, mailer=build_mailer(settings))
    if args.create:
        parts = args.create.split(",")
        if len(parts) != 4:
            print("use: --create 'name,metric,comparator,threshold'")
            return 2
        try:
            rule = engine.create(name=parts[0], metric=parts[1], comparator=parts[2],
                                 threshold=float(parts[3]))
        except ValueError as exc:
            print(f"invalid rule: {exc}")
            return 2
        print(f"created {rule['id']}: {rule['metric']} {rule['comparator']} {rule['threshold']}")
        return 0
    if args.delete:
        print({"deleted": engine.delete(args.delete)})
        return 0
    rules = engine.rules()
    if not rules:
        print("no alert rules")
        return 0
    for rule in rules:
        state = "on " if rule["enabled"] else "off"
        print(f"  {rule['id']}  {state}  {rule['metric']} {rule['comparator']} "
              f"{rule['threshold']}  -> {rule['channel']}  ({rule['name']})")
    return 0


def _cmd_loadtest(args, settings: Settings) -> int:
    from .core.loadtest import run_load

    orch = _build(settings)
    result = asyncio.run(run_load(orch, runs=args.runs, concurrency=args.concurrency))
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"load test: {result['runs']} runs, concurrency {result['concurrency']}")
        print(f"  done={result['done']} failed={result['failed']} "
              f"wall={result['wall_time_s']}s throughput={result['throughput_runs_per_s']}/s")
        lat = result["latency"]
        print(f"  latency  min={lat['min']}s p50={lat['p50']}s p95={lat['p95']}s max={lat['max']}s")
        print(f"  tokens   {result['tokens']}")
    return 0 if result["failed"] == 0 else 1


def _cmd_vault(args, settings: Settings) -> int:
    from .core.vault import Vault, VaultError

    vault = Vault(settings.data_dir, settings.secret_key)
    store = RunStore(settings.db_path)
    if args.rotate:
        try:
            report = vault.rotate(store, args.rotate)
        except VaultError as exc:
            print(f"rotation failed: {exc}")
            return 2
        print(json.dumps(report, indent=2))
        return 0
    print(json.dumps({**vault.status(), "secrets": [r["name"] for r in vault.names(store)]},
                     indent=2))
    return 0


def _cmd_plugins(args, settings: Settings) -> int:
    from .core import plugins as plugin_core

    store = RunStore(settings.db_path)
    if args.index:
        index = plugin_core.build_index(store, settings.data_dir, settings,
                                        base_url=args.base_url)
        print(json.dumps(index, indent=2))
        return 0
    if args.install:
        payload = pathlib.Path(args.install).read_bytes()
        try:
            record = plugin_core.install(store, settings.data_dir, payload, settings, None,
                                         datetime.now(timezone.utc).isoformat(timespec="seconds"))
        except Exception as exc:  # noqa: BLE001 - user-facing
            print(f"install failed: {exc}")
            return 2
        print(f"installed {record['name']}@{record['version']} ({record['trust']}) id={record['id']}")
        return 0
    for action, plugin_id in (("enable", args.enable), ("disable", args.disable),
                              ("remove", args.remove)):
        if plugin_id:
            orch = _build(settings)
            try:
                if action == "enable":
                    print(plugin_core.activate(orch, store, plugin_id))
                elif action == "disable":
                    print(plugin_core.deactivate(orch, store, plugin_id))
                else:
                    plugin_core.deactivate(orch, store, plugin_id)
                    print(plugin_core.uninstall(store, plugin_id))
            except plugin_core.PluginError as exc:
                print(str(exc))
                return 2
            return 0
    rows = store.list_plugins()
    if not rows:
        print("no plugins installed")
        return 0
    for row in rows:
        state = "enabled" if row["enabled"] else "disabled"
        print(f"  {row['name']:<24} {row['version']:<10} {row['trust']:<9} {state:<9} {row['id']}")
    return 0


def _cmd_automation(args, settings: Settings) -> int:
    store = RunStore(settings.db_path)
    if args.kind == "workflows":
        rows = store.list_workflows()
        print(f"{len(rows)} workflow(s)\n")
        for row in rows:
            print(f"  {row['id']}  v{row['version']}  {row['name']}  "
                  f"({row['goal_template'][:50]})")
        return 0
    if args.kind == "schedules":
        rows = store.list_schedules()
        print(f"{len(rows)} schedule(s)\n")
        for row in rows:
            state = "on " if row["enabled"] else "off"
            print(f"  {row['id']}  {state}  every {row['every_seconds']}s  next {row['next_run_at']}  {row['name']}")
        return 0
    rows = store.list_hooks()
    print(f"{len(rows)} webhook(s)\n")
    for row in rows:
        print(f"  {row['id']}  workflow={row['workflow_id']}  last={row['last_called_at']}")
    return 0


def _cmd_usage(args, settings: Settings) -> int:
    from .core.billing import Billing

    billing = Billing(settings, RunStore(settings.db_path))
    if args.pricing:
        for row in billing.pricing():
            print(f"  {row['model']:<28} in ${row['input_per_1m']:<7} out ${row['output_per_1m']}")
        return 0
    print(json.dumps(billing.usage(args.user_id, this_month=not args.all_time), indent=2))
    return 0


def _cmd_metrics(args, settings: Settings) -> int:
    store = RunStore(settings.db_path)
    print(json.dumps(store.stats(), indent=2))
    return 0


def _cmd_info(args, settings: Settings) -> int:
    orch = _build(settings)
    print(json.dumps(orch.describe(), indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omniforge", description="OmniForge AI Studio CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="plan and execute one goal")
    p_run.add_argument("goal")
    p_run.add_argument("--auto-approve", action="store_true", help="skip human approval gates")
    p_run.add_argument("--json", action="store_true", help="print the full run record")
    p_run.add_argument("--watch", action="store_true",
                       help="stream tokens and key events while the run works")

    p_serve = sub.add_parser("serve", help="start the HTTP API and dashboard")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)

    p_agents = sub.add_parser("agents", help="list agent roles")
    p_agents.add_argument("--department")
    p_agents.add_argument("--search")

    p_skills = sub.add_parser("skills", help="list skills")
    p_skills.add_argument("--department")

    sub.add_parser("departments", help="list departments with counts")
    sub.add_parser("info", help="framework summary as JSON")
    sub.add_parser("demo", help="run two example goals end to end")

    sub.add_parser("doctor", help="check the environment and run a smoke test")

    p_eval = sub.add_parser("eval", help="run the eval suite")
    p_eval.add_argument("--case", action="append", help="only these case names (repeatable)")
    p_eval.add_argument("--json", action="store_true")

    p_replay = sub.add_parser("replay", help="re-run a recorded goal")
    p_replay.add_argument("run_id")

    p_history = sub.add_parser("history", help="recent runs from the store")
    p_history.add_argument("--limit", type=int, default=20)

    p_kb = sub.add_parser("kb", help="knowledge base stats or search")
    p_kb.add_argument("query", nargs="?", default="")
    p_kb.add_argument("--limit", type=int, default=5)

    sub.add_parser("metrics", help="storage counters as JSON")

    p_ui = sub.add_parser("ui-audit", help="static accessibility and responsiveness audit")
    p_ui.add_argument("--json", action="store_true")

    p_verify = sub.add_parser("verify", help="live checks for every configured provider")
    p_verify.add_argument("--json", action="store_true")
    p_verify.add_argument("--live", action="store_true",
                          help="actually call each configured provider once")

    p_tenants = sub.add_parser("tenants", help="list, create or drop tenant schemas")
    p_tenants.add_argument("--create", metavar="NAME")
    p_tenants.add_argument("--drop", metavar="NAME")

    p_comp = sub.add_parser("completions", help="print a shell completion script")
    p_comp.add_argument("shell", choices=["bash", "zsh"], nargs="?", default="bash")

    p_worker = sub.add_parser("worker", help="process queued runs (worker mode)")
    p_worker.add_argument("--once", action="store_true", help="claim and run one, then exit")
    p_worker.add_argument("--max-runs", type=int, default=None)
    p_worker.add_argument("--worker-id", default=None)
    p_worker.add_argument("--lease-seconds", type=int, default=300)
    p_worker.add_argument("--poll", type=float, default=0.5)

    p_db = sub.add_parser("db", help="database status, Postgres mirror or verify")
    p_db.add_argument("--sync", metavar="POSTGRES_URL", help="mirror SQLite into PostgreSQL")
    p_db.add_argument("--verify", metavar="POSTGRES_URL", help="check a PostgreSQL mirror")

    p_alerts = sub.add_parser("alerts", help="list, create or delete alert rules")
    p_alerts.add_argument("--create", metavar="NAME,METRIC,COMPARATOR,THRESHOLD")
    p_alerts.add_argument("--delete", metavar="RULE_ID")

    p_load = sub.add_parser("loadtest", help="run N goals at a given concurrency")
    p_load.add_argument("--runs", type=int, default=5)
    p_load.add_argument("--concurrency", type=int, default=3)
    p_load.add_argument("--json", action="store_true")

    p_vault = sub.add_parser("vault", help="secrets vault status or key rotation")
    p_vault.add_argument("--rotate", metavar="NEW_KEY", help="re-encrypt under a new master key")

    p_plugins = sub.add_parser("plugins", help="list or manage plugin packages")
    p_plugins.add_argument("--install", metavar="ZIP")
    p_plugins.add_argument("--enable", metavar="ID")
    p_plugins.add_argument("--disable", metavar="ID")
    p_plugins.add_argument("--remove", metavar="ID")
    p_plugins.add_argument("--index", action="store_true",
                           help="print a registry index of installed plugins")
    p_plugins.add_argument("--base-url", default="", help="public base URL for index entries")

    p_auto = sub.add_parser("automation", help="list workflows, schedules or hooks")
    p_auto.add_argument("kind", choices=["workflows", "schedules", "hooks"])

    p_usage = sub.add_parser("usage", help="token and cost usage")
    p_usage.add_argument("--user-id", default=None)
    p_usage.add_argument("--all-time", action="store_true")
    p_usage.add_argument("--pricing", action="store_true")

    p_integrations = sub.add_parser("integrations", help="integration status (and verify)")
    p_integrations.add_argument("--verify", action="store_true", help="run live checks")

    sub.add_parser("tools", help="list tools with permissions")

    p_backup = sub.add_parser("backup", help="create a verified backup (or verify one)")
    p_backup.add_argument("--verify", metavar="ARCHIVE", help="verify an existing archive")

    p_restore = sub.add_parser("restore", help="restore a verified backup")
    p_restore.add_argument("--file", required=True)
    p_restore.add_argument("--force", action="store_true", help="replace an existing database")

    p_users = sub.add_parser("users", help="list accounts or create one")
    p_users.add_argument("--create", metavar="EMAIL")
    p_users.add_argument("--password", default="")
    p_users.add_argument("--name", default="")
    p_users.add_argument("--role", default="")

    args = parser.parse_args(argv)
    settings = Settings.from_env()
    handlers = {
        "run": lambda: asyncio.run(_cmd_run(args, settings)),
        "serve": lambda: _cmd_serve(args, settings),
        "agents": lambda: _cmd_agents(args, settings),
        "skills": lambda: _cmd_skills(args, settings),
        "departments": lambda: _cmd_departments(args, settings),
        "info": lambda: _cmd_info(args, settings),
        "demo": lambda: asyncio.run(_cmd_demo(args, settings)),
        "doctor": lambda: _cmd_doctor(args, settings),
        "eval": lambda: _cmd_eval(args, settings),
        "replay": lambda: _cmd_replay(args, settings),
        "history": lambda: _cmd_history(args, settings),
        "kb": lambda: _cmd_kb(args, settings),
        "metrics": lambda: _cmd_metrics(args, settings),
        "worker": lambda: _cmd_worker(args, settings),
        "completions": lambda: _cmd_completions(args, settings),
        "verify": lambda: _cmd_verify(args, settings),
        "ui-audit": lambda: _cmd_ui_audit(args, settings),
        "tenants": lambda: _cmd_tenants(args, settings),
        "db": lambda: _cmd_db(args, settings),
        "alerts": lambda: _cmd_alerts(args, settings),
        "loadtest": lambda: _cmd_loadtest(args, settings),
        "vault": lambda: _cmd_vault(args, settings),
        "plugins": lambda: _cmd_plugins(args, settings),
        "automation": lambda: _cmd_automation(args, settings),
        "usage": lambda: _cmd_usage(args, settings),
        "integrations": lambda: _cmd_integrations(args, settings),
        "tools": lambda: _cmd_tools(args, settings),
        "backup": lambda: _cmd_backup(args, settings),
        "restore": lambda: _cmd_restore(args, settings),
        "users": lambda: _cmd_users(args, settings),
    }
    return handlers[args.command]()


if __name__ == "__main__":
    sys.exit(main())
