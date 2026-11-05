"""Deterministic generators for every shipped skill.

Each function turns a goal plus upstream context into a structured first draft:
a spec, a table, a checklist or a code skeleton. They run with no model at all, so
the framework produces usable artifacts offline; a live model still writes better
prose, and the prompt path is used whenever a skill has no handler.

These are scaffolding generators, not domain experts. Nothing here invents measured
numbers: budgets and targets are labelled as defaults you should tune.
"""
from __future__ import annotations

import json
import re
from typing import Any

# --------------------------------------------------------------------- helpers


def _goal(ctx: Any) -> str:
    run = getattr(ctx, "run", None)
    return (getattr(run, "goal", "") or "").strip() or getattr(ctx.task, "title", "the goal")


def _title(ctx: Any) -> str:
    return getattr(ctx.task, "title", "Deliverable")


def _name(ctx: Any) -> str:
    """A short product name inferred from the goal."""
    goal = _goal(ctx)
    words = [w for w in re.findall(r"[A-Za-z]{3,}", goal) if w.lower() not in STOP]
    return " ".join(words[:2]).title() or "Project"


STOP = {"the", "and", "for", "with", "build", "create", "make", "design", "write", "produce",
        "deliver", "a", "an", "of", "to", "in", "on", "my", "our", "new", "using", "use"}


def _context(ctx: Any) -> str:
    return (getattr(ctx, "context", "") or "").strip()


def _h(text: str, level: int = 2) -> str:
    return f"{'#' * level} {text}"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    sep = "|" + "|".join("---" for _ in headers) + "|"
    body = "\n".join("| " + " | ".join(str(cell) for cell in row) + " |" for row in rows)
    return "\n".join([head, sep, body])


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _checklist(items: list[str]) -> str:
    return "\n".join(f"- [ ] {item}" for item in items)


def _code(language: str, body: str) -> str:
    return f"```{language}\n{body.strip()}\n```"


def _stack(ctx: Any) -> str:
    text = _goal(ctx).lower()
    for keyword, stack in (
        ("android", "Kotlin + Jetpack Compose"), ("ios", "Swift + SwiftUI"),
        ("flutter", "Flutter + Dart"), ("react native", "React Native"),
        ("react", "React + TypeScript"), ("next", "Next.js + TypeScript"),
        ("unity", "Unity + C#"), ("unreal", "Unreal + C++"), ("godot", "Godot + GDScript"),
        ("python", "Python"), ("node", "Node.js + TypeScript"),
    ):
        if keyword in text:
            return stack
    return "the stack you pick"


def _upstream_rows(ctx: Any) -> list[list[str]]:
    """Summarise upstream sections as (name, first line) pairs."""
    rows = []
    for block in _context(ctx).split("### ")[1:]:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines:
            rows.append([lines[0][:60], (lines[1] if len(lines) > 1 else "")[:70]])
    return rows


def _head(ctx: Any, kind: str, extra: str = "") -> str:
    lines = [f"# {kind} — {_name(ctx)}", "", f"**Goal:** {_goal(ctx)}"]
    if extra:
        lines.append(f"**{extra}**")
    rows = _upstream_rows(ctx)
    if rows:
        lines += ["", _h("Inputs from upstream"), _table(["Section", "First line"], rows), ""]
    return "\n".join(lines)


def _tail(ctx: Any, assumptions: list[str]) -> str:
    return "\n".join([
        "", _h("Assumptions to confirm"),
        _bullets(assumptions),
        "", _h("Definition of done"),
        _checklist([
            "Reviewed by the owning department",
            "Open questions answered or parked with an owner",
            "Artifacts linked from the project",
        ]),
    ])


# ------------------------------------------------------------------ game dev


def game_concept_design(ctx: Any) -> str:
    name = _name(ctx)
    parts = [
        _head(ctx, "Game concept", f"Working title: {name}"),
        _h("Pillars"),
        _bullets(["One-sentence fantasy: what the player brags about",
                  "Core loop that fits in 30 seconds",
                  "A reason to come back tomorrow"]),
        _h("Core loop"),
        _code("text", "observe -> choose -> act -> reward -> escalate -> repeat"),
        _h("Scope"),
        _table(["Area", "MVP", "Later"],
               [["Levels", "5-8 handcrafted", "procedural"],
                ["Mechanics", "1 verb + 1 modifier", "second verb, combos"],
                ["Progression", "linear unlock", "meta progression"],
                ["Platforms", "one", "cross-play"]]),
        _h("Risks"),
        _table(["Risk", "Signal", "Mitigation"],
               [["Scope creep", "level count grows weekly", "freeze MVP list, cut instead of add"],
                ["Feel is wrong", "playtesters quit in 2 minutes", "prototype the verb first"],
                ["Art cost", "each asset > 1 day", "style constraints, modular kit"]]),
        _tail(ctx, ["Target platform", "Session length", "Monetisation (if any)"]),
    ]
    return "\n".join(parts)


def level_layout(ctx: Any) -> str:
    mechanics = ["movement", "first obstacle", "timed pressure", "new modifier",
                 "combination", "checkpoint", "mini-boss", "twist", "finale", "epilogue"]
    rows = [[str(i + 1), f"L{i + 1}", mechanics[i], f"{60 + i * 15}s",
             "easy" if i < 3 else "medium" if i < 7 else "hard"] for i in range(10)]
    return "\n".join([
        _head(ctx, "Level layout"),
        _h("Pacing"),
        _table(["#", "Name", "New mechanic", "Target length", "Difficulty"], rows),
        _h("Rules"),
        _bullets(["Teach before you test: every mechanic gets one safe room",
                  "Difficulty comes from timing and density, not from health sponges",
                  "First failure should happen after the player understands the verb"]),
        _h("Checklist"),
        _checklist(["Every level has one readable goal",
                    "Checkpoints no more than 90s apart",
                    "Playtested by someone who did not build it"]),
        _tail(ctx, ["Level count", "Average session", "Save/resume policy"]),
    ])


def gameplay_logic(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Gameplay logic"),
        _h("States"),
        _code("text", "Idle -> Windup -> Active -> Recovery -> Idle\n"
                      "any state -> Stunned -> Recovery\n"
                      "Active -> Hit -> apply damage -> Recovery"),
        _h("Rules as code"),
        _code("python", """
def on_hit(attacker, target, now):
    if target.invulnerable_until > now:
        return {"damage": 0, "reason": "iframe"}
    damage = max(0, attacker.power - target.armor)
    target.hp -= damage
    target.invulnerable_until = now + IFRAME_S
    return {"damage": damage, "hp": target.hp, "dead": target.hp <= 0}
"""),
        _h("Tuning table"),
        _table(["Parameter", "Start value", "Why"],
               [["Windup", "0.12s", "readable without feeling slow"],
                ["i-frames", "0.40s", "forgiving but not free"],
                ["Hitstop", "0.05s", "impact feel"],
                ["Input buffer", "0.15s", "forgives early presses"]]),
        _tail(ctx, ["Tick rate", "Determinism (replays/netcode)", "Difficulty curve"]),
    ])


def npc_behavior(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "NPC behaviour"),
        _h("Behaviour tree"),
        _code("text", """Selector
├── Sequence [IsThreatNear]
│   ├── FaceThreat
│   ├── ChooseAction -> (attack | dodge | call-allies)
│   └── PlayAnimation
├── Sequence [IsPatrolling]
│   └── MoveToNextWaypoint
└── Idle
"""),
        _h("Perception"),
        _table(["Sense", "Range", "Refresh", "Notes"],
               [["Sight", "18m, 100° cone", "0.2s", "raycast to chest"],
                ["Hearing", "10m", "event", "footsteps, gunfire"],
                ["Damage", "any", "event", "always wakes the NPC"]]),
        _h("Fallbacks"),
        _bullets(["Lost target -> last known position -> search 5s -> patrol",
                  "Path blocked twice -> re-path with wider radius -> teleport as last resort",
                  "Stuck > 3s -> play fidget, then re-evaluate"]),
        _tail(ctx, ["NPC count on screen", "Budget per frame", "Difficulty scaling"]),
    ])


def engine_optimization(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Engine optimisation", f"Target: {_stack(ctx)}"),
        _h("Frame budget at 60 fps (16.6 ms)"),
        _table(["Slice", "Budget", "Measure with"],
               [["Gameplay scripts", "4.0 ms", "profiler timeline"],
                ["Rendering", "8.0 ms", "GPU capture"],
                ["Physics", "2.0 ms", "physics stats"],
                ["Audio/UI", "1.5 ms", "profiler"],
                ["Headroom", "1.1 ms", "keep it"]]),
        _h("First passes"),
        _checklist(["Profile before touching anything; write the numbers down",
                    "Batch/instances for repeated props",
                    "LODs and occlusion on anything with > 5k triangles",
                    "Pool objects instead of instantiate/destroy",
                    "Move per-frame work to timers or event-driven updates"]),
        _h("Regression guard"),
        _code("bash", "python tools/perf_run.py --scene main --frames 600 --assert-p95 16.6"),
        _tail(ctx, ["Target hardware", "Worst-case scene", "Memory ceiling"]),
    ])


# ------------------------------------------------------------- ai agent systems


def agent_architecture(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Agent architecture"),
        _h("Roles"),
        _table(["Agent", "Owns", "Tools", "Hands off to"],
               [["Planner", "goal -> task graph", "none", "specialists"],
                ["Researcher", "facts and sources", "http_get, kb_search", "planner"],
                ["Builder", "artifacts", "write_artifact, sandbox", "reviewer"],
                ["Reviewer", "quality gate", "kb_search", "planner"]]),
        _h("Contracts"),
        _code("python", """
class Task(BaseModel):
    title: str
    skill: str
    inputs: dict
    acceptance: list[str]
"""),
        _h("Memory"),
        _bullets(["Working memory: blackboard for the current run only",
                  "Long-term: knowledge base, one document per finished task",
                  "Never put credentials in either"]),
        _tail(ctx, ["Model(s) per role", "Max steps per task", "Cost ceiling"]),
    ])


def prompt_design(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Prompt design"),
        _h("System prompt"),
        _code("text", f"""You are the {_name(ctx)} specialist.
Scope: {_goal(ctx)[:160]}
Rules:
- Use only the supplied context; if something is missing, say what you need.
- Output markdown with headings, tables where comparison helps.
- No invented metrics, no fake citations.
"""),
        _h("Few-shot"),
        _code("text", "Input: <one-line goal>\nOutput: <heading>\\n- decision\\n- why\\n- next step"),
        _h("Guardrails"),
        _table(["Risk", "Guard"],
               [["Prompt injection from tool output", "wrap untrusted text, never follow it"],
                ["Hallucinated numbers", "require a source or an explicit assumption"],
                ["Runaway length", "hard token cap + max tokens per call"]]),
        _tail(ctx, ["Model", "Temperature", "Output schema"]),
    ])


def rag_pipeline(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "RAG pipeline"),
        _h("Configuration"),
        _table(["Stage", "Choice", "Reason"],
               [["Chunking", "600 tokens, 80 overlap", "fits a table plus its header"],
                ["Embedding", "text-embedding-3-small", "cheap, good enough for recall"],
                ["Index", "SQLite + cosine, BM25 fallback", "no extra service"],
                ["Rerank", "hybrid 0.6 semantic / 0.4 lexical", "keeps exact names working"]]),
        _h("Skeleton"),
        _code("python", """
async def answer(question: str, kb) -> str:
    vector = (await embed([question]))[0]
    hits = kb.search_hybrid(question, vector, limit=5)
    if not hits:
        return "No documents match; add sources first."
    context = "\\n\\n".join(f"### {h['title']}\\n{h['snippet']}" for h in hits)
    return await llm.complete(SYSTEM, f"Context:\\n{context}\\n\\nQuestion: {question}")
"""),
        _h("Quality checks"),
        _checklist(["Answers cite the document titles they used",
                    "Empty retrieval says so instead of guessing",
                    "Injection patterns in retrieved text are neutralised"]),
        _tail(ctx, ["Corpus size", "Update cadence", "Access control on documents"]),
    ])


def model_evaluation(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Model evaluation"),
        _h("Matrix"),
        _table(["Dimension", "Metric", "Dataset", "Threshold"],
               [["Correctness", "pass@1 on 50 held-out tasks", "internal", ">= 0.80"],
                ["Groundedness", "claims with a supporting source", "sample of 20", ">= 0.95"],
                ["Format", "valid JSON / required sections", "all outputs", "1.00"],
                ["Latency", "p95 seconds", "live traffic", "<= 12s"],
                ["Cost", "USD per 1k tasks", "billing", "budget"]]),
        _h("Protocol"),
        _checklist(["Freeze the prompt and the dataset before comparing",
                    "Run each variant 3 times; report the spread, not just the mean",
                    "Score with a rubric written before the run",
                    "Record model version and date with every result"]),
        _tail(ctx, ["Who signs off", "Release gate", "Rollback plan"]),
    ])


def ml_training(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Training plan"),
        _h("Data"),
        _table(["Split", "Share", "Notes"],
               [["Train", "80%", "dedupe first"],
                ["Validation", "10%", "same distribution"],
                ["Test", "10%", "never touched during tuning"]]),
        _h("Hyperparameters (starting point)"),
        _code("yaml", """optimizer: adamw
lr: 2.0e-5
batch_size: 8
grad_accum: 4
epochs: 3
warmup_ratio: 0.03
scheduler: cosine
"""),
        _h("Gates"),
        _checklist(["Baseline recorded before training",
                    "Loss curves compared, not just final numbers",
                    "Held-out set evaluated once, at the end",
                    "Artifact + config + data hash stored together"]),
        _tail(ctx, ["Compute budget", "Licence of the data", "Rollback criteria"]),
    ])


# ---------------------------------------------------------------------- mobile


def mobile_ui_design(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Mobile UI", f"Stack: {_stack(ctx)}"),
        _h("Screens"),
        _table(["Screen", "Purpose", "Primary action"],
               [["Home", "resume or start", "Continue"],
                ["List", "browse items", "Open"],
                ["Detail", "act on one item", "Save"],
                ["Settings", "preferences", "Toggle"],
                ["Empty/Error", "explain and recover", "Retry"]]),
        _h("Navigation"),
        _code("text", "Home -> List -> Detail\nHome -> Settings\nDetail -> (back) List"),
        _h("Rules"),
        _bullets(["Thumb zone: primary action in the bottom third",
                  "44px minimum touch target",
                  "Every screen has a loading, empty and error state",
                  "Dark mode and large-font settings must not clip"]),
        _tail(ctx, ["Design system", "Localisation", "Offline behaviour"]),
    ])


def android_implementation(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Android implementation", "Kotlin + Jetpack Compose"),
        _h("Structure"),
        _code("text", "app/src/main/java/.../ui/HomeScreen.kt\n"
                      "app/src/main/java/.../data/Repository.kt\n"
                      "app/src/main/java/.../MainActivity.kt"),
        _h("Screen"),
        _code("kotlin", """
@Composable
fun HomeScreen(state: HomeState, onAction: (HomeAction) -> Unit) {
    Scaffold(topBar = { TopAppBar(title = { Text("Home") }) }) { padding ->
        LazyColumn(Modifier.padding(padding)) {
            items(state.items) { item ->
                ListItem(
                    headlineContent = { Text(item.title) },
                    supportingContent = { Text(item.subtitle) },
                    modifier = Modifier.clickable { onAction(HomeAction.Open(item.id)) }
                )
            }
        }
    }
}
"""),
        _h("Checklist"),
        _checklist(["ViewModel owns state; composables stay stateless",
                    "Handle process death with SavedStateHandle",
                    "Release build with R8 and a real signing key"]),
        _tail(ctx, ["minSdk", "Permissions", "Offline cache"]),
    ])


def ios_implementation(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "iOS implementation", "Swift + SwiftUI"),
        _h("Screen"),
        _code("swift", """
struct HomeView: View {
    @StateObject private var model = HomeViewModel()

    var body: some View {
        NavigationStack {
            List(model.items) { item in
                NavigationLink(item.title) { DetailView(item: item) }
            }
            .navigationTitle("Home")
            .task { await model.load() }
        }
    }
}
"""),
        _h("Checklist"),
        _checklist(["Views are value types; state lives in the view model",
                    "Handle Dynamic Type and VoiceOver labels",
                    "Signing profile + privacy manifest before submission"]),
        _tail(ctx, ["Minimum iOS", "App Transport Security", "Push permission"]),
    ])


def app_release_prep(ctx: Any) -> str:
    meta = {"title": _name(ctx)[:30], "short_description": _goal(ctx)[:80],
            "category": "Productivity", "contains_ads": False}
    return "\n".join([
        _head(ctx, "Store release"),
        _h("Listing metadata"),
        _code("json", json.dumps(meta, indent=2)),
        _h("Assets"),
        _table(["Asset", "Spec"],
               [["Icon", "512x512 PNG, no alpha"],
                ["Screenshots", "2-8 per device class, real UI"],
                ["Feature graphic", "1024x500"],
                ["Privacy policy URL", "required"]]),
        _h("Pre-flight"),
        _checklist(["Version and build number bumped",
                    "Release build tested on a low-end device",
                    "Crash-free session baseline recorded",
                    "Store policy checked for the permissions you request"]),
        _tail(ctx, ["Release date", "Rollout percentage", "Support contact"]),
    ])


def mobile_perf_tuning(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Mobile performance"),
        _h("Budgets"),
        _table(["Metric", "Target", "Where to look"],
               [["Cold start", "< 2.0s", "startup trace"],
                ["Frame time", "< 16.6ms (60fps)", "frame profiler"],
                ["Memory", "< 200MB", "heap dump"],
                ["APK size", "< 30MB", "bundle analyser"],
                ["Network", "< 100KB per screen", "network inspector"]]),
        _h("Steps"),
        _checklist(["Measure on the cheapest supported device",
                    "Fix the biggest frame first, then re-measure",
                    "Lazy-load images and list items",
                    "Never do disk or network work on the main thread"]),
        _tail(ctx, ["Device matrix", "ANR budget", "Battery target"]),
    ])


# ------------------------------------------------------------------------- web


def frontend_implementation(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Frontend", f"Stack: {_stack(ctx)}"),
        _h("Page skeleton"),
        _code("html", """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>App</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <main id="app"></main>
  <script type="module" src="/app.js"></script>
</body>
</html>"""),
        _h("State"),
        _code("javascript", """const state = { items: [], loading: false, error: null };

async function load() {
  state.loading = true; render();
  try {
    const res = await fetch('/api/items');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.items = await res.json();
  } catch (err) {
    state.error = err.message;
  } finally {
    state.loading = false; render();
  }
}"""),
        _h("Quality"),
        _checklist(["Loading, empty and error states on every list",
                    "Keyboard focus visible everywhere",
                    "No layout shift: reserve image dimensions"]),
        _tail(ctx, ["Framework", "Design system", "Browser support"]),
    ])


def backend_api_design(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Backend API"),
        _h("Endpoints"),
        _table(["Method", "Path", "Purpose", "Auth"],
               [["GET", "/api/items", "list with pagination", "read"],
                ["POST", "/api/items", "create", "write"],
                ["GET", "/api/items/{id}", "detail", "read"],
                ["PATCH", "/api/items/{id}", "update", "write"],
                ["DELETE", "/api/items/{id}", "delete", "admin"]]),
        _h("Contract"),
        _code("python", """
class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)

class ItemOut(ItemIn):
    id: str
    created_at: str

@app.post("/api/items", response_model=ItemOut, status_code=201)
async def create_item(payload: ItemIn) -> ItemOut:
    ...
"""),
        _h("Non-negotiables"),
        _checklist(["Errors are JSON with a stable shape and a useful message",
                    "Pagination on every list endpoint",
                    "Idempotency key on create where retries are possible"]),
        _tail(ctx, ["Auth model", "Rate limits", "Versioning"]),
    ])


def auth_flow(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Auth flow"),
        _h("Sequence"),
        _code("text", """client -> POST /auth/login {email, password}
server -> verify hash (PBKDF2/argon2) -> issue session token (hashed at rest)
client -> Authorization: Bearer <token>
server -> resolve identity -> check role -> allow/deny
client -> POST /auth/logout -> token revoked
"""),
        _h("Rules"),
        _table(["Concern", "Decision"],
               [["Password storage", "PBKDF2-SHA256 210k iterations or argon2id"],
                ["Sessions", "random token, stored hashed, expiring"],
                ["Reset", "single-use token, 30 min, no account enumeration"],
                ["MFA", "TOTP + single-use backup codes"],
                ["Rate limit", "per identity and per IP"]]),
        _h("Checklist"),
        _checklist(["No credentials in logs, URLs or frontend bundles",
                    "Constant-time comparison for tokens",
                    "Sessions invalidated on password change"]),
        _tail(ctx, ["SSO needed?", "Session TTL", "Compliance requirements"]),
    ])


def web_perf_audit(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Web performance audit"),
        _h("Targets"),
        _table(["Metric", "Good", "Needs work"],
               [["LCP", "<= 2.5s", "> 4.0s"],
                ["INP", "<= 200ms", "> 500ms"],
                ["CLS", "<= 0.1", "> 0.25"],
                ["TTFB", "<= 0.8s", "> 1.8s"]]),
        _h("Order of work"),
        _checklist(["Measure on a throttled profile, not on localhost",
                    "Compress and serve modern image formats",
                    "Split the bundle; defer anything below the fold",
                    "Cache static assets with content hashes"]),
        _h("Command"),
        _code("bash", "npx lighthouse http://localhost:8000 --preset=desktop --quiet"),
        _tail(ctx, ["Traffic profile", "Devices", "Third-party scripts"]),
    ])


# ------------------------------------------------------------------------ 3D


def model_3d(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "3D model spec"),
        _h("Budget"),
        _table(["Asset class", "Triangles", "Materials", "LODs"],
               [["Hero character", "35k", "3", "3"],
                ["NPC", "12k", "2", "2"],
                ["Prop (repeated)", "800", "1", "1"],
                ["Environment kit", "5k/module", "1", "2"]]),
        _h("Topology rules"),
        _bullets(["Quads for deformable areas, triangles acceptable for static props",
                  "Even edge flow around joints; no n-gons on the silhouette",
                  "Pivot at the base, transforms applied, scale 1:1:1"]),
        _h("Naming"),
        _code("text", "SM_<asset>_<variant>_LOD<n>\nSK_<character>_<part>\nM_<asset>_<material>"),
        _tail(ctx, ["Art direction", "Target platform", "Texture budget"]),
    ])


def texture_pbr(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "PBR material"),
        _h("Channels"),
        _table(["Map", "Resolution", "Notes"],
               [["Base colour", "2048", "sRGB, no baked lighting"],
                ["Roughness", "2048", "linear, packed with metallic"],
                ["Normal", "2048", "tangent space, Y+ convention"],
                ["AO", "1024", "multiplied in engine, not baked into colour"]]),
        _h("Workflow"),
        _bullets(["Block the silhouette first, texture after the form is locked",
                  "Check tiling at 0.5x and 2x zoom",
                  "Sample on a neutral grey background for value balance"]),
        _tail(ctx, ["Style (stylised/realistic)", "Engine", "Texel density"]),
    ])


def rigging(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Rig spec"),
        _h("Skeleton"),
        _code("text", "root\n└─ hips\n   ├─ spine_01 -> spine_02 -> chest -> neck -> head\n"
                      "   ├─ clavicle_L -> upperarm_L -> forearm_L -> hand_L\n"
                      "   └─ thigh_L -> calf_L -> foot_L -> toe_L"),
        _h("Weights"),
        _bullets(["Max 4 influences per vertex",
                  "Check elbows, knees and shoulders at 90° and 120°",
                  "No weight bleeding across the midline"]),
        _h("Test poses"),
        _checklist(["T-pose -> A-pose -> crouch",
                    "Arm overhead, full rotation",
                    "Extreme hip flexion"]),
        _tail(ctx, ["Engine", "Face rig needed?", "Retarget standard"]),
    ])


def character_animation(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Animation set"),
        _table(["Clip", "Frames (24fps)", "Loop", "Blend"],
               [["idle", "1-72", "yes", "additive breathing"],
                ["walk", "1-32", "yes", "1D blend by speed"],
                ["run", "1-24", "yes", "1D blend by speed"],
                ["attack", "1-18", "no", "hitstop on frame 6"],
                ["jump", "1-30", "no", "split takeoff/land"]]),
        _h("Rules"),
        _bullets(["Anticipation before every action, even 2 frames",
                  "Feet planted: no sliding at locomotion speeds",
                  "Interrupt windows marked per clip"]),
        _tail(ctx, ["Style", "Root motion?", "Engine retarget"]),
    ])


def scene_lighting(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Lighting plan"),
        _table(["Light", "Type", "Purpose", "Setting"],
               [["Key", "directional", "shape and direction", "warm, 3.0 lux"],
                ["Fill", "skylight", "lift shadows", "cool, 0.6"],
                ["Rim", "spot", "separate from background", "behind subject"],
                ["Practical", "point", "motivate the scene", "2 per room max"]]),
        _h("Pipeline"),
        _checklist(["Lock exposure and white balance before look-dev",
                    "Bake static lighting; keep dynamic lights for the hero",
                    "Check readability on a phone screen, not a monitor"]),
        _tail(ctx, ["Time of day", "Mood", "Performance budget"]),
    ])


# --------------------------------------------------------------------- media


def video_edit(ctx: Any) -> str:
    rows = [[str(i + 1), f"Shot {i + 1}", f"{(i * 3) + 2}s", "cut",
             "establish" if i == 0 else "detail"] for i in range(8)]
    return "\n".join([
        _head(ctx, "Edit decision list"),
        _table(["#", "Shot", "Duration", "Transition", "Beat"], rows),
        _h("Assembly rules"),
        _bullets(["Cut on motion, not mid-gesture",
                  "Keep the first 3 seconds free of titles",
                  "Music hit lands within 2 frames of the hero cut"]),
        _h("Deliverables"),
        _table(["Version", "Spec"],
               [["Master", "1920x1080, H.264, 12 Mbps"],
                ["Vertical", "1080x1920, safe margins 10%"],
                ["Captions", "burned-in + .srt"]]),
        _tail(ctx, ["Runtime target", "Music licence", "Platform specs"]),
    ])


def sound_design(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Sound design cue sheet"),
        _table(["Cue", "Type", "Trigger", "Notes"],
               [["ui_click", "SFX", "button press", "short, dry, -18 LUFS"],
                ["impact", "SFX", "hit", "layer transient + body"],
                ["ambience", "loop", "scene load", "seamless 30s"],
                ["music_calm", "music", "menu", "loopable, low intensity"]]),
        _h("Mix targets"),
        _table(["Bus", "Level", "Note"],
               [["Dialogue", "-16 LUFS", "always intelligible"],
                ["Music", "-20 LUFS", "duck 4dB under dialogue"],
                ["SFX", "-18 LUFS", "peaks -6dBTP"]]),
        _h("Checklist"),
        _checklist(["No clicks at loop points",
                    "Mono compatibility checked",
                    "Licences recorded per asset"]),
        _tail(ctx, ["Platform", "Headphone mix?", "Localisation"]),
    ])


def music_compose(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Music structure"),
        _h("Arrangement"),
        _table(["Section", "Bars", "Intensity", "Elements"],
               [["Intro", "8", "1/5", "pad + pulse"],
                ["A", "16", "2/5", "drums enter"],
                ["B", "16", "3/5", "lead melody"],
                ["Breakdown", "8", "1/5", "drop drums"],
                ["Drop", "16", "5/5", "full arrangement"],
                ["Outro", "8", "2/5", "fade elements"]]),
        _h("Settings"),
        _code("text", "Tempo: 100-120 BPM\nKey: minor for tension, major for resolve\n"
                      "Loop points: bar 1 and bar 65, 2-bar tail for reverb"),
        _h("Deliverables"),
        _checklist(["Full track + 30s cut + loop",
                    "Stems per section",
                    "Licence and composer credit recorded"]),
        _tail(ctx, ["Mood", "Reference tracks", "Runtime"]),
    ])


def graphic_design(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Graphic assets"),
        _h("Tokens"),
        _code("css", ":root {\n  --bg: #0b0f14;\n  --fg: #e7eef7;\n"
                     "  --accent: #4f9cf9;\n  --radius: 10px;\n  --space: 8px;\n}"),
        _h("Asset list"),
        _table(["Asset", "Size", "Format"],
               [["Logo", "512x512", "SVG + PNG"],
                ["Social card", "1200x630", "PNG"],
                ["App icon", "1024x1024", "PNG"],
                ["Banner", "1500x500", "PNG"]]),
        _h("Rules"),
        _bullets(["One accent colour, one neutral scale, one typeface family",
                  "Contrast ratio 4.5:1 for body text",
                  "Export at 2x for anything that will be scaled"]),
        _tail(ctx, ["Brand guidelines", "Print needed?", "Localisation"]),
    ])


def render_pipeline(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Render pipeline"),
        _h("Settings"),
        _table(["Setting", "Value", "Why"],
               [["Engine", "Cycles/Eevee or equivalent", "quality vs speed"],
                ["Samples", "128 + denoise", "clean without 4x the time"],
                ["Resolution", "1920x1080", "deliverable target"],
                ["Colour", "Filmic/ACES", "highlight roll-off"]]),
        _h("Queue plan"),
        _code("bash", "for shot in shots/*.blend; do\n"
                      "  blender -b \"$shot\" -o //out/#### -F PNG -a\n"
                      "done"),
        _h("Checklist"),
        _checklist(["Test frame rendered before the full queue",
                    "Output naming includes shot and frame",
                    "Disk space checked for the full sequence"]),
        _tail(ctx, ["Deadline", "Hardware", "Delivery format"]),
    ])


# ------------------------------------------------------------------ research


def web_research(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Research brief"),
        _h("Questions to answer"),
        _bullets([f"What exactly is being asked: {_goal(ctx)[:120]}",
                  "What would make this wrong? (list the assumptions)",
                  "Who already solved it, and what did it cost them?",
                  "What is the smallest experiment that reduces the risk?"]),
        _h("Source plan"),
        _table(["Source type", "Why", "How to verify"],
               [["Primary docs", "authoritative", "check the version and date"],
                ["Vendor pages", "features and pricing", "look for the pricing page, not the blog"],
                ["Community reports", "real-world friction", "require 2+ independent reports"],
                ["Benchmarks", "numbers", "check methodology, not headlines"]]),
        _h("Output contract"),
        _checklist(["Every claim carries a source or is labelled an assumption",
                    "Contradictions are reported, not averaged away",
                    "No source is quoted for something it does not say"]),
        "", "> This generator writes the plan and the structure. It does not fetch "
            "the web by itself - use the `http_get` tool or connect a search provider.",
        _tail(ctx, ["Depth", "Deadline", "Audience"]),
    ])


def doc_writing(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Document outline"),
        _h("Structure"),
        _table(["Section", "Purpose", "Length"],
               [["Summary", "decision in 5 lines", "short"],
                ["Context", "why now", "short"],
                ["Options", "2-3 with trade-offs", "medium"],
                ["Recommendation", "one option, with reasons", "medium"],
                ["Risks", "what could go wrong", "short"],
                ["Next steps", "owners and dates", "short"]]),
        _h("Writing rules"),
        _bullets(["Lead with the conclusion",
                  "One idea per paragraph",
                  "Numbers carry a unit and a source",
                  "Every claim that could be argued gets a reason"]),
        _tail(ctx, ["Audience", "Tone", "Reviewers"]),
    ])


def competitive_scan(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Competitive scan"),
        _h("Matrix"),
        _table(["Competitor", "Core promise", "Price", "Weak spot", "What we do differently"],
               [["A", "", "", "", ""], ["B", "", "", "", ""], ["C", "", "", "", ""]]),
        _h("How to fill it"),
        _checklist(["Use their own pricing page, not a review site",
                    "Quote the weak spot from real user reports",
                    "Record the date you checked - pricing moves"]),
        "", "> Structure only: fill the cells from primary sources; nothing here is a "
            "claim about a real company.",
        _tail(ctx, ["Market", "Segment", "Decision this informs"]),
    ])


# ------------------------------------------------------------------------ QA


def automated_tests(ctx: Any) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", _goal(ctx).lower())[:40].strip("_") or "feature"
    return "\n".join([
        _head(ctx, "Automated tests"),
        _h("Test file"),
        _code("python", f"""
import pytest
from fastapi.testclient import TestClient

def test_{slug}_happy_path(client: TestClient) -> None:
    response = client.post("/api/{slug}", json={{"name": "example"}})
    assert response.status_code == 201
    assert response.json()["name"] == "example"

def test_{slug}_rejects_empty_payload(client: TestClient) -> None:
    assert client.post("/api/{slug}", json={{}}).status_code == 422

def test_{slug}_requires_auth(client: TestClient) -> None:
    assert client.post("/api/{slug}", json={{"name": "x"}}).status_code == 401
"""),
        _h("Coverage plan"),
        _table(["Level", "What it proves"],
               [["Unit", "pure logic and validation"],
                ["API", "contract, status codes, auth"],
                ["Integration", "database and external services (mocked)"],
                ["E2E", "one happy path end to end"]]),
        _tail(ctx, ["CI target", "Fixtures needed", "Flaky-test policy"]),
    ])


def bug_triage(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Bug triage"),
        _h("Matrix"),
        _table(["Severity", "Meaning", "Response"],
               [["S1", "data loss, security, outage", "stop and fix now"],
                ["S2", "main flow broken, no workaround", "fix this sprint"],
                ["S3", "workaround exists", "schedule"],
                ["S4", "cosmetic", "backlog"]]),
        _h("Report template"),
        _code("text", "Title: <what breaks> when <condition>\n"
                      "Steps: 1) ... 2) ... 3) ...\nExpected: ...\nActual: ...\n"
                      "Environment: version, OS, account\nEvidence: log line or screenshot"),
        _h("Rules"),
        _bullets(["Reproduce before assigning",
                  "One bug per report",
                  "Close with a test that would have caught it"]),
        _tail(ctx, ["Tracker", "Owners", "SLA"]),
    ])


def load_test(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Load test"),
        _h("Scenario"),
        _table(["Stage", "Users", "Duration", "Goal"],
               [["Warm-up", "5", "30s", "catch obvious errors"],
                ["Steady", "50", "3m", "p95 latency"],
                ["Spike", "200", "30s", "graceful degradation"],
                ["Recovery", "10", "1m", "no stuck workers"]]),
        _h("Script"),
        _code("python", """
from locust import HttpUser, task, between

class ApiUser(HttpUser):
    wait_time = between(0.5, 2.0)

    @task(3)
    def list_items(self):
        self.client.get("/api/runs")

    @task(1)
    def submit_goal(self):
        self.client.post("/api/runs", json={"goal": "load test goal"})
"""),
        _h("Pass criteria"),
        _checklist(["p95 under the target at steady load",
                    "Error rate under 1%",
                    "No queue growth after recovery"]),
        _tail(ctx, ["Target latency", "Infrastructure", "Test data"]),
    ])


def security_review(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Security review"),
        _h("Checklist (OWASP-shaped)"),
        _table(["Area", "Check", "How to verify"],
               [["AuthN", "sessions expire, tokens hashed", "read the code + a forced-expiry test"],
                ["AuthZ", "every route checks a role", "hit routes with a viewer token"],
                ["Injection", "parameterised SQL only", "grep for string-built queries"],
                ["Secrets", "no keys in code or logs", "scan the repo and log output"],
                ["Input", "size and type limits", "upload a 100MB file"],
                ["Dependencies", "no known CVEs", "pip-audit / npm audit"]]),
        _h("Commands"),
        _code("bash", "pip-audit\nbandit -r omniforge\ngrep -rn 'password\\|secret\\|token' --include=*.py | head"),
        _h("Report"),
        _bullets(["Finding, severity, reproduction, fix, owner",
                  "No exploitation steps beyond what a maintainer needs"]),
        _tail(ctx, ["Threat model", "Compliance scope", "Disclosure contact"]),
    ])


# -------------------------------------------------------------------- devops


def containerization(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Containerisation"),
        _h("Dockerfile"),
        _code("dockerfile", """FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["uvicorn", "omniforge.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
"""),
        _h("Compose"),
        _code("yaml", """services:
  app:
    build: .
    ports: ["8000:8000"]
    environment:
      OMNIFORGE_DATA_DIR: /data
    volumes: ["./data:/data"]
    restart: unless-stopped
"""),
        _h("Rules"),
        _checklist(["Pin base image by digest for production",
                    "Non-root user",
                    "Healthcheck that touches the app, not just the port"]),
        _tail(ctx, ["Registry", "Secrets handling", "Image size target"]),
    ])


def monitoring_setup(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Monitoring"),
        _h("Signals"),
        _table(["Signal", "Metric", "Alert when"],
               [["Traffic", "http_requests_total", "drops > 50% vs 1h ago"],
                ["Errors", "http_errors_total / total", "> 2% for 5m"],
                ["Latency", "http_request_duration p95", "> 1s for 5m"],
                ["Saturation", "queue_depth", "> 100 for 10m"],
                ["Workers", "worker.failures", "> 3 in 10m"]]),
        _h("Prometheus rule"),
        _code("yaml", """groups:
  - name: omniforge
    rules:
      - alert: HighErrorRate
        expr: rate(omniforge_http_errors_total[5m]) > 0.02
        for: 5m
        labels: {severity: page}
        annotations: {summary: "Error rate above 2%"}
"""),
        _h("Checklist"),
        _checklist(["Every alert links to a runbook",
                    "Dashboards show the four signals, not 40 charts",
                    "Synthetic check from outside the network"]),
        _tail(ctx, ["SLO", "On-call", "Retention"]),
    ])


def infra_as_code(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Infrastructure as code"),
        _h("Layout"),
        _code("text", "infra/\n  main.tf\n  variables.tf\n  outputs.tf\n  envs/{dev,prod}.tfvars"),
        _h("Skeleton"),
        _code("hcl", """resource "aws_instance" "app" {
  ami           = var.ami
  instance_type = var.instance_type
  tags = { Name = "omniforge-${var.env}" }
}

variable "env" { type = string }
variable "ami" { type = string }
variable "instance_type" { default = "t3.small" }
"""),
        _h("Rules"),
        _checklist(["State in a remote backend with locking",
                    "Plan reviewed before apply; never apply from a laptop for prod",
                    "Secrets from a secret manager, never tfvars"]),
        _tail(ctx, ["Cloud", "Environments", "Cost ceiling"]),
    ])


# --------------------------------------------------------------- product ops


def roadmap_planning(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Roadmap"),
        _table(["Phase", "Scope", "Exit criteria", "Owner"],
               [["0 - prove", "one thin slice end to end", "demo runs on real data", "eng"],
                ["1 - usable", "core flow + auth + errors", "5 users finish the task", "eng"],
                ["2 - safe", "limits, audit, backups", "restore drill passes", "ops"],
                ["3 - scale", "queue mode, Postgres", "load test hits p95 target", "eng"]]),
        _h("Rules"),
        _bullets(["Each phase ships something a user can use",
                  "No phase starts before the previous exit criteria are met",
                  "Cut scope, never cut the exit criteria"]),
        _tail(ctx, ["Timeline", "Team size", "Dependencies"]),
    ])


def metrics_report(ctx: Any) -> str:
    return "\n".join([
        _head(ctx, "Metrics report"),
        _h("KPIs"),
        _table(["KPI", "Definition", "Target", "Source"],
               [["Activation", "users finishing one run", "40%", "runs table"],
                ["Completion", "runs ending done", "> 90%", "runs.status"],
                ["Quality", "mean review score", "> 0.8", "tasks.review_score"],
                ["Cost per run", "USD / runs", "budget", "usage"],
                ["Retention", "users back in 7 days", "30%", "sessions"]]),
        _h("Queries"),
        _code("sql", """SELECT status, COUNT(*) AS runs
FROM runs
WHERE created_at >= date('now', '-30 day')
GROUP BY status ORDER BY runs DESC;

SELECT ROUND(AVG(review_score), 2) AS quality
FROM tasks WHERE review_score IS NOT NULL;
"""),
        _h("Rules"),
        _bullets(["Define each KPI once, in one place",
                  "Show the denominator",
                  "No metric without an owner"]),
        _tail(ctx, ["Audience", "Cadence", "Dashboard"]),
    ])


GENERATORS: dict[str, Any] = {
    "game_concept_design": game_concept_design,
    "level_layout": level_layout,
    "gameplay_logic": gameplay_logic,
    "npc_behavior": npc_behavior,
    "engine_optimization": engine_optimization,
    "agent_architecture": agent_architecture,
    "prompt_design": prompt_design,
    "rag_pipeline": rag_pipeline,
    "model_evaluation": model_evaluation,
    "ml_training": ml_training,
    "mobile_ui_design": mobile_ui_design,
    "android_implementation": android_implementation,
    "ios_implementation": ios_implementation,
    "app_release_prep": app_release_prep,
    "mobile_perf_tuning": mobile_perf_tuning,
    "frontend_implementation": frontend_implementation,
    "backend_api_design": backend_api_design,
    "auth_flow": auth_flow,
    "web_perf_audit": web_perf_audit,
    "model_3d": model_3d,
    "3d_modeling": model_3d,
    "texture_pbr": texture_pbr,
    "rigging": rigging,
    "character_animation": character_animation,
    "scene_lighting": scene_lighting,
    "video_edit": video_edit,
    "sound_design": sound_design,
    "music_compose": music_compose,
    "graphic_design": graphic_design,
    "render_pipeline": render_pipeline,
    "web_research": web_research,
    "doc_writing": doc_writing,
    "competitive_scan": competitive_scan,
    "automated_tests": automated_tests,
    "bug_triage": bug_triage,
    "load_test": load_test,
    "security_review": security_review,
    "containerization": containerization,
    "monitoring_setup": monitoring_setup,
    "infra_as_code": infra_as_code,
    "roadmap_planning": roadmap_planning,
    "metrics_report": metrics_report,
}
