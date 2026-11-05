"""Runtime configuration, read from OMNIFORGE_* environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _parse_budgets(raw: str) -> dict:
    """'openai:5,anthropic:10' -> {'openai': 5.0, 'anthropic': 10.0} (USD per month)."""
    budgets: dict = {}
    for chunk in (raw or "").split(","):
        name, _, value = chunk.partition(":")
        try:
            if name.strip() and value.strip():
                budgets[name.strip().lower()] = float(value)
        except ValueError:
            continue
    return budgets


def _parse_prices(raw: str) -> dict:
    """'gpt-4o:2.5:10,claude:3:15' -> {'gpt-4o': [2.5, 10], ...} ($ per 1M tokens)."""
    prices: dict = {}
    for chunk in (raw or "").split(","):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) == 3:
            try:
                prices[parts[0]] = [float(parts[1]), float(parts[2])]
            except ValueError:
                continue
    return prices


@dataclass
class Settings:
    # --- model -------------------------------------------------------------
    llm_provider: str = "openai"          # openai | anthropic | gemini
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com"
    gemini_api_key: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    llm_fallback: tuple[str, ...] = ()    # e.g. ("anthropic:claude-3-5-sonnet-latest",)
    mock_llm: bool = True
    request_timeout: float = 90.0

    # --- execution ---------------------------------------------------------
    max_parallel: int = 4
    max_retries: int = 2                 # extra attempts after the first failure
    retry_base_delay: float = 0.4        # seconds, exponential with jitter
    max_tasks_per_run: int = 40          # guard against runaway delegation
    run_timeout: float = 0.0             # 0 disables the wall-clock cap
    fail_fast: bool = True               # stop the run when a task fails for good

    # --- debate ------------------------------------------------------------
    debate_candidates: int = 1           # >1 = run N candidates, keep the best
    prefer_generators: bool = True       # deterministic artifact over a model call

    # --- media -------------------------------------------------------------
    media_api_key: str = ""              # image generation (OpenAI-compatible)
    media_base_url: str = "https://api.openai.com/v1"
    media_model: str = "gpt-image-1"

    # --- review loop -------------------------------------------------------
    review_enabled: bool = True
    review_min_chars: int = 40
    review_with_llm: bool = False
    max_rework: int = 1                  # rework attempts after a failed review

    # --- approvals ---------------------------------------------------------
    require_approval: bool = False

    # --- cost control ------------------------------------------------------
    budget_tokens: int = 0               # 0 disables the cap
    price_per_1k_input: float = 0.0
    price_per_1k_output: float = 0.0

    # --- integrations ------------------------------------------------------
    github_token: str = ""
    github_base_url: str = "https://api.github.com"

    # --- sandbox -----------------------------------------------------------
    allow_code_exec: bool = False
    sandbox_timeout: float = 5.0
    sandbox_memory_mb: int = 256

    # --- identity / security -----------------------------------------------
    auth_enabled: bool = False           # multi-user mode (sessions + API keys)
    secret_key: str = ""                 # master key for the secrets vault
    session_ttl_hours: int = 72
    rate_limit_per_minute: int = 240
    rate_limit_burst: int = 60
    allow_anonymous: bool = False        # with auth on, allow read-only guests

    # --- mail / recovery ---------------------------------------------------
    mail_backend: str = "console"        # console | smtp | off
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_tls: bool = True
    public_base_url: str = "http://localhost:8000"
    reset_ttl_minutes: int = 30

    # --- automation --------------------------------------------------------
    webhook_url: str = ""                 # outbound run notifications
    scheduler_enabled: bool = True
    scheduler_tick_seconds: float = 5.0

    # --- billing -----------------------------------------------------------
    user_monthly_tokens: int = 0         # 0 = no quota
    provider_budgets: dict = field(default_factory=dict)  # {"openai": 5.0, ...} USD/month
    worker_mode: bool = False            # enqueue runs instead of executing in-process
    model_prices: dict = field(default_factory=dict)   # {"model-prefix": [in_per_1m, out_per_1m]}

    # --- retrieval ---------------------------------------------------------
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-small"

    # --- database ----------------------------------------------------------
    db_url: str = ""                     # sqlite:///path ; postgres is planned

    # --- plugins -----------------------------------------------------------
    plugins_enabled: bool = True
    plugin_allow_unsigned: bool = True
    plugin_allowed_permissions: tuple[str, ...] = ("net.read", "fs.write", "github.read")
    plugin_require_signature: bool = False

    # --- alerts ------------------------------------------------------------
    alert_email: str = ""                # receives failure and quota alerts
    alert_on_failure: bool = True

    # --- content policy ----------------------------------------------------
    moderation_enabled: bool = True
    moderation_blocked_terms: tuple[str, ...] = ()
    moderation_llm: bool = False          # add a model-based classifier pass

    # --- network policy ----------------------------------------------------
    http_allowlist: tuple[str, ...] = ()   # empty = allow any host

    # --- storage / api -----------------------------------------------------
    data_dir: Path = field(default_factory=lambda: Path("./data"))
    api_key: str = ""                    # when set, /api requires a bearer token
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        cors = os.getenv("OMNIFORGE_CORS_ORIGINS", "").strip()
        return cls(
            llm_provider=os.getenv("OMNIFORGE_LLM_PROVIDER", "openai").lower(),
            llm_base_url=os.getenv("OMNIFORGE_LLM_BASE_URL", cls.llm_base_url),
            llm_api_key=os.getenv("OMNIFORGE_LLM_API_KEY", ""),
            llm_model=os.getenv("OMNIFORGE_LLM_MODEL", cls.llm_model),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
            anthropic_base_url=os.getenv("OMNIFORGE_ANTHROPIC_BASE_URL",
                                         "https://api.anthropic.com"),
            gemini_api_key=os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", ""),
            gemini_base_url=os.getenv("OMNIFORGE_GEMINI_BASE_URL",
                                      "https://generativelanguage.googleapis.com/v1beta"),
            llm_fallback=tuple(
                f.strip() for f in os.getenv("OMNIFORGE_LLM_FALLBACK", "").split(",") if f.strip()
            ),
            mock_llm=_bool("OMNIFORGE_MOCK_LLM", True),
            request_timeout=_float("OMNIFORGE_REQUEST_TIMEOUT", 90.0),
            max_parallel=_int("OMNIFORGE_MAX_PARALLEL", 4),
            max_retries=_int("OMNIFORGE_MAX_RETRIES", 2),
            retry_base_delay=_float("OMNIFORGE_RETRY_BASE_DELAY", 0.4),
            max_tasks_per_run=_int("OMNIFORGE_MAX_TASKS", 40),
            run_timeout=_float("OMNIFORGE_RUN_TIMEOUT", 0.0),
            fail_fast=_bool("OMNIFORGE_FAIL_FAST", True),
            debate_candidates=_int("OMNIFORGE_DEBATE_CANDIDATES", 1),
            prefer_generators=_bool("OMNIFORGE_PREFER_GENERATORS", True),
            media_api_key=os.getenv("OMNIFORGE_MEDIA_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", ""),
            media_base_url=os.getenv("OMNIFORGE_MEDIA_BASE_URL", "https://api.openai.com/v1"),
            media_model=os.getenv("OMNIFORGE_MEDIA_MODEL", "gpt-image-1"),
            review_enabled=_bool("OMNIFORGE_REVIEW_ENABLED", True),
            review_min_chars=_int("OMNIFORGE_REVIEW_MIN_CHARS", 40),
            review_with_llm=_bool("OMNIFORGE_REVIEW_WITH_LLM", False),
            max_rework=_int("OMNIFORGE_MAX_REWORK", 1),
            require_approval=_bool("OMNIFORGE_REQUIRE_APPROVAL", False),
            budget_tokens=_int("OMNIFORGE_BUDGET_TOKENS", 0),
            price_per_1k_input=_float("OMNIFORGE_PRICE_IN", 0.0),
            price_per_1k_output=_float("OMNIFORGE_PRICE_OUT", 0.0),
            github_token=os.getenv("GITHUB_TOKEN", ""),
            github_base_url=os.getenv("OMNIFORGE_GITHUB_BASE_URL", "https://api.github.com"),
            allow_code_exec=_bool("OMNIFORGE_ALLOW_CODE_EXEC", False),
            sandbox_timeout=_float("OMNIFORGE_SANDBOX_TIMEOUT", 5.0),
            sandbox_memory_mb=_int("OMNIFORGE_SANDBOX_MEMORY_MB", 256),
            user_monthly_tokens=_int("OMNIFORGE_USER_MONTHLY_TOKENS", 0),
            provider_budgets=_parse_budgets(os.getenv("OMNIFORGE_PROVIDER_BUDGETS", "")),
            worker_mode=_bool("OMNIFORGE_WORKER_MODE", False),
            model_prices=_parse_prices(os.getenv("OMNIFORGE_MODEL_PRICES", "")),
            embedding_api_key=os.getenv("OMNIFORGE_EMBEDDING_API_KEY", "")
            or os.getenv("OPENAI_API_KEY", ""),
            embedding_model=os.getenv("OMNIFORGE_EMBEDDING_MODEL", "text-embedding-3-small"),
            db_url=os.getenv("OMNIFORGE_DB_URL", ""),
            plugins_enabled=_bool("OMNIFORGE_PLUGINS_ENABLED", True),
            plugin_require_signature=_bool("OMNIFORGE_PLUGIN_REQUIRE_SIGNATURE", False),
            plugin_allow_unsigned=_bool("OMNIFORGE_PLUGIN_ALLOW_UNSIGNED", True),
            plugin_allowed_permissions=tuple(
                p.strip() for p in os.getenv(
                    "OMNIFORGE_PLUGIN_PERMISSIONS", "net.read,fs.write,github.read"
                ).split(",") if p.strip()
            ),
            moderation_llm=_bool("OMNIFORGE_MODERATION_LLM", False),
            alert_email=os.getenv("OMNIFORGE_ALERT_EMAIL", ""),
            alert_on_failure=_bool("OMNIFORGE_ALERT_ON_FAILURE", True),
            moderation_enabled=_bool("OMNIFORGE_MODERATION_ENABLED", True),
            moderation_blocked_terms=tuple(
                t.strip() for t in os.getenv("OMNIFORGE_BLOCKED_TERMS", "").split(",") if t.strip()
            ),
            webhook_url=os.getenv("OMNIFORGE_WEBHOOK_URL", ""),
            scheduler_enabled=_bool("OMNIFORGE_SCHEDULER_ENABLED", True),
            scheduler_tick_seconds=_float("OMNIFORGE_SCHEDULER_TICK", 5.0),
            mail_backend=os.getenv("OMNIFORGE_MAIL_BACKEND", "console"),
            smtp_host=os.getenv("OMNIFORGE_SMTP_HOST", ""),
            smtp_port=_int("OMNIFORGE_SMTP_PORT", 587),
            smtp_user=os.getenv("OMNIFORGE_SMTP_USER", ""),
            smtp_password=os.getenv("OMNIFORGE_SMTP_PASSWORD", ""),
            smtp_from=os.getenv("OMNIFORGE_SMTP_FROM", ""),
            smtp_tls=_bool("OMNIFORGE_SMTP_TLS", True),
            public_base_url=os.getenv("OMNIFORGE_PUBLIC_BASE_URL", "http://localhost:8000"),
            reset_ttl_minutes=_int("OMNIFORGE_RESET_TTL_MINUTES", 30),
            http_allowlist=tuple(
                h.strip().lower() for h in os.getenv("OMNIFORGE_HTTP_ALLOWLIST", "").split(",") if h.strip()
            ),
            auth_enabled=_bool("OMNIFORGE_AUTH_ENABLED", False),
            secret_key=os.getenv("OMNIFORGE_SECRET_KEY", ""),
            session_ttl_hours=_int("OMNIFORGE_SESSION_TTL_HOURS", 72),
            rate_limit_per_minute=_int("OMNIFORGE_RATE_LIMIT_PER_MINUTE", 240),
            rate_limit_burst=_int("OMNIFORGE_RATE_LIMIT_BURST", 60),
            allow_anonymous=_bool("OMNIFORGE_ALLOW_ANONYMOUS", False),
            data_dir=Path(os.getenv("OMNIFORGE_DATA_DIR", "./data")),
            api_key=os.getenv("OMNIFORGE_API_KEY", ""),
            cors_origins=tuple(o.strip() for o in cors.split(",") if o.strip()),
        )

    def provider_key(self, provider: str | None = None) -> str:
        provider = (provider or self.llm_provider or "openai").lower()
        return {
            "openai": self.llm_api_key,
            "anthropic": self.anthropic_api_key,
            "gemini": self.gemini_api_key,
        }.get(provider, "")

    @property
    def live_mode(self) -> bool:
        return (not self.mock_llm) and bool(self.provider_key())

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / "omniforge.db"
