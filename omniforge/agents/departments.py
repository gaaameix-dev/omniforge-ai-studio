"""The 10 departments and their 120 agent role templates (12 per department).

Roles are templates, not running processes: each one is a system prompt plus a
skill and tool allow-list that the orchestrator binds to the runtime on demand.
"""
from __future__ import annotations

from collections import OrderedDict

DEPARTMENTS: OrderedDict[str, dict] = OrderedDict(
    [
        ("game_development", {
            "name": "Game Development",
            "description": "Game and simulation design, interactive 3D content, gameplay logic, "
                           "engine programming, physics and real-time performance.",
        }),
        ("ai_agent_systems", {
            "name": "AI & Agent Engineering",
            "description": "Model engineering, agent architecture, prompt and retrieval design, "
                           "evaluation and training.",
        }),
        ("mobile", {
            "name": "Android & Mobile App Development",
            "description": "Android, iOS and cross-platform app build, mobile UX and device performance.",
        }),
        ("web", {
            "name": "Web Development",
            "description": "Frontend, backend services, data modelling, auth and web performance.",
        }),
        ("three_d", {
            "name": "3D Modeling & Digital Art",
            "description": "Modelling, texturing, rigging, animation, lighting and VFX assets.",
        }),
        ("media", {
            "name": "Media, Video & Audio Production",
            "description": "Video editing, motion graphics, sound design, music, voice and rendering.",
        }),
        ("research", {
            "name": "Research & Intelligence",
            "description": "Information retrieval, data analysis, competitive intelligence and documentation.",
        }),
        ("qa", {
            "name": "Quality Assurance & Testing",
            "description": "Test strategy, automation, triage, load and security review.",
        }),
        ("devops", {
            "name": "Automation & DevOps",
            "description": "CI/CD, containers, infrastructure as code, monitoring and release engineering.",
        }),
        ("product_ops", {
            "name": "Product Management & Operations",
            "description": "Planning, roadmaps, specs, risk, launch coordination and human approvals.",
        }),
    ]
)

# department -> [(role name, one-line scope), ...]
ROLES: dict[str, list[tuple[str, str]]] = {
    "game_development": [
        ("Game Designer", "Owns the core loop, pillars and player experience."),
        ("Gameplay Programmer", "Implements systems, rules and state machines."),
        ("Level Designer", "Builds beat sheets, layout and pacing for playable spaces."),
        ("3D World Builder", "Assembles environments, props and streaming zones."),
        ("NPC AI Engineer", "Behaviour trees, perception and dialogue hooks."),
        ("Quest Writer", "Branching quests, dialogue and narrative continuity."),
        ("Unreal Engine Developer", "Blueprints, C++ modules and Unreal build settings."),
        ("Unity Developer", "Prefabs, MonoBehaviours and Unity pipeline work."),
        ("Physics Engineer", "Rigid bodies, cloth, collision and stability tuning."),
        ("Shader Artist", "Materials, shader graphs and stylised rendering."),
        ("Game Economy Designer", "Progression, currency sinks and balance curves."),
        ("Multiplayer Engineer", "Netcode, replication and matchmaking."),
    ],
    "ai_agent_systems": [
        ("ML Engineer", "Trains and serves models behind the agents."),
        ("LLM Engineer", "Prompt, tool-calling and structured-output plumbing."),
        ("Prompt Architect", "Designs system prompts and few-shot sets per role."),
        ("Agent Architect", "Defines roles, handoff contracts and topology."),
        ("RL Researcher", "Reward design and policy improvement."),
        ("RAG Engineer", "Chunking, embeddings and grounded retrieval."),
        ("Model Evaluator", "Eval sets, metrics and regression gates."),
        ("Data Engineer", "Pipelines, schemas and dataset hygiene."),
        ("MLOps Engineer", "Model registry, deployment and drift monitoring."),
        ("Fine-tuning Specialist", "Datasets, hyperparameters and adapter training."),
        ("AI Safety Reviewer", "Refusal policy, guardrails and abuse review."),
        ("Embedding Engineer", "Vector store design and similarity quality."),
    ],
    "mobile": [
        ("Android Developer", "Kotlin/Compose features and platform integration."),
        ("iOS Developer", "SwiftUI features and Apple platform integration."),
        ("Flutter Developer", "Cross-platform Dart implementation."),
        ("React Native Developer", "JS/TS mobile implementation and native bridges."),
        ("Mobile UI Designer", "Screen flow, components and state design."),
        ("Mobile Performance Engineer", "Startup, memory and jank budgets."),
        ("App Store Release Manager", "Signing, listings and store review."),
        ("Push Notification Engineer", "Delivery, segmentation and deep links."),
        ("Mobile QA Engineer", "Device matrix and release regression."),
        ("Kotlin Architect", "Module boundaries and Android architecture."),
        ("Swift Architect", "App architecture and Swift concurrency."),
        ("AR/VR Mobile Engineer", "ARCore/ARKit scenes and device constraints."),
    ],
    "web": [
        ("Frontend Developer", "Components, state and accessible markup."),
        ("Backend Developer", "Services, jobs and business logic."),
        ("Database Architect", "Schemas, indexes and migration strategy."),
        ("Fullstack Developer", "Ships a thin vertical slice end to end."),
        ("API Designer", "Contracts, versioning and error semantics."),
        ("UI/UX Designer", "Flows, hierarchy and interaction states."),
        ("Web Performance Engineer", "Core Web Vitals and payload budgets."),
        ("Auth Engineer", "Sessions, tokens and role models."),
        ("Frontend Animator", "Motion system and micro-interactions."),
        ("SEO Engineer", "Crawlability, metadata and structured data."),
        ("Realtime Systems Engineer", "WebSockets, presence and event fanout."),
        ("Accessibility Specialist", "WCAG conformance and assistive tech testing."),
    ],
    "three_d": [
        ("3D Modeler", "Mesh construction and topology."),
        ("Texture Artist", "PBR map sets and texel density."),
        ("Character Rigger", "Skeletons, weights and IK."),
        ("Animator", "Timing, keyframes and blend trees."),
        ("Lighting Artist", "Light rigs, mood and render settings."),
        ("VFX Artist", "Particles, simulations and compositing."),
        ("Environment Artist", "Scene composition and modular kits."),
        ("Technical Artist", "Asset pipeline, tooling and optimisation."),
        ("Concept Artist", "Visual exploration and design sheets."),
        ("Retopology Specialist", "Game-ready topology from high-poly scans."),
        ("PBR Material Artist", "Physically based materials and reference matching."),
        ("Asset Optimizer", "LODs, bakes and draw-call reduction."),
    ],
    "media": [
        ("Video Editor", "Story order, pacing and cut decisions."),
        ("Motion Designer", "Titles, transitions and animated graphics."),
        ("Sound Designer", "Foley, ambience and cue sheets."),
        ("Music Composer", "Score brief, arrangement and stems."),
        ("Voice Director", "Casting notes, scripts and performance direction."),
        ("Graphic Designer", "Layout, type and colour systems."),
        ("Render Wrangler", "Render queues, passes and delivery."),
        ("Podcast Producer", "Structure, editing and mastering."),
        ("Colorist", "Grade, LUTs and shot matching."),
        ("Subtitle Specialist", "Timing, translation and caption styling."),
        ("Audio Mixing Engineer", "Levels, loudness targets and stems."),
        ("Storyboard Artist", "Shot-by-shot boards and camera notes."),
    ],
    "research": [
        ("Research Analyst", "Structured briefs with claims and sources."),
        ("Market Researcher", "Sizing, segments and demand signals."),
        ("Data Analyst", "Profiles, metrics and honest caveats."),
        ("Competitive Intelligence Analyst", "Competitor matrices and gap analysis."),
        ("Documentation Writer", "Docs for readers without context."),
        ("Knowledge Base Curator", "Structure, tagging and freshness of a KB."),
        ("Web Scraper Engineer", "Collection pipelines within site terms."),
        ("Insight Synthesizer", "Folds findings into a decision-ready summary."),
        ("Survey Designer", "Question sets that avoid leading bias."),
        ("Technical Writer", "Specs, READMEs and release notes."),
        ("Fact Checker", "Verifies claims and flags the unverifiable."),
        ("Benchmark Analyst", "Comparable numbers and their provenance."),
    ],
    "qa": [
        ("QA Engineer", "Test execution and defect reports."),
        ("Automation Test Engineer", "Test code for critical paths."),
        ("Performance Test Engineer", "Load profiles and thresholds."),
        ("Security Tester", "Threat models and exploit checks."),
        ("Bug Triage Specialist", "Severity, reproduction and ownership."),
        ("Test Plan Writer", "Levels, cases and exit criteria."),
        ("Regression Analyst", "Change impact and regression scope."),
        ("Accessibility Tester", "WCAG checks with assistive tech."),
        ("API Test Engineer", "Contract and edge-case testing."),
        ("Mobile Test Engineer", "Device matrix coverage."),
        ("Load Test Engineer", "Sustained load and failure modes."),
        ("Release QA Gatekeeper", "Signs off or blocks a release."),
    ],
    "devops": [
        ("DevOps Engineer", "Builds and runs the delivery path."),
        ("CI/CD Engineer", "Pipeline stages, caching and gates."),
        ("Kubernetes Engineer", "Workloads, scaling and rollout strategy."),
        ("Docker Specialist", "Images, layers and compose setups."),
        ("Cloud Architect", "Topology, regions and cost shape."),
        ("Infrastructure as Code Engineer", "Declarative, reviewable infrastructure."),
        ("Monitoring Engineer", "Logs, metrics, alerts and SLOs."),
        ("Release Manager", "Release calendar and rollback readiness."),
        ("Build & Deployment Agent", "Compiles, uploads and triggers distribution."),
        ("Security Hardening Engineer", "Least privilege and secret handling."),
        ("Cost Optimization Engineer", "Spend attribution and right-sizing."),
        ("Incident Responder", "Triage, mitigation and postmortem."),
    ],
    "product_ops": [
        ("Product Manager", "Owns the problem, scope and trade-offs."),
        ("Project Planner", "Milestones, dependencies and sequencing."),
        ("Roadmap Owner", "Now/next/later and cut lines."),
        ("Resource Planner", "Capacity, budget and staffing shape."),
        ("Stakeholder Liaison", "Alignment, updates and escalation."),
        ("Spec Writer", "Requirements a builder can act on."),
        ("Approval Coordinator", "Human review gates and sign-offs."),
        ("Risk Analyst", "Risk register and mitigations."),
        ("Operations Manager", "Runbooks and operational readiness."),
        ("Customer Feedback Analyst", "Themes from support and reviews."),
        ("Launch Coordinator", "Launch timeline and readiness checks."),
        ("Metrics Analyst", "KPI tree and what the numbers mean."),
    ],
}
