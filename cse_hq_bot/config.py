import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    discord_guild_id: int | None
    database_path: str
    log_level: str
    ai_enable_message_content: bool
    gemini_api_key: str | None
    ai_model: str
    ai_provider: str
    ai_max_context_items: int
    ai_max_history_messages: int
    ai_request_timeout: int
    ai_fallback_enabled: bool
    ai_fallback_provider: str
    openai_api_key: str | None
    openai_model: str | None
    ai_action_expiration_seconds: int
    github_enabled: bool
    github_repository_owner: str | None
    github_repository_name: str | None
    github_token: str | None
    github_request_timeout: int
    github_cache_ttl: int
    github_max_results: int
    github_webhook_enabled: bool
    github_webhook_secret: str | None
    webhook_host: str
    webhook_port: int
    github_webhook_path: str
    github_bug_label: str


def load_config() -> Config:
    load_dotenv()
    guild_id = os.getenv("DISCORD_GUILD_ID")
    return Config(
        discord_token=os.getenv("DISCORD_TOKEN"),
        discord_guild_id=int(guild_id) if guild_id else None,
        database_path=os.getenv("DATABASE_PATH", "./cse_hq.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_enable_message_content=os.getenv(
            "AI_ENABLE_MESSAGE_CONTENT", "false"
        ).lower()
        in {"1", "true", "yes", "on"},
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        ai_model=os.getenv("AI_MODEL") or os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        ai_provider=os.getenv("AI_PROVIDER", "fake").lower(),
        ai_max_context_items=max(1, int(os.getenv("AI_MAX_CONTEXT_ITEMS", "12"))),
        ai_max_history_messages=max(1, int(os.getenv("AI_MAX_HISTORY_MESSAGES", "8"))),
        ai_request_timeout=max(1, int(os.getenv("AI_REQUEST_TIMEOUT", "20"))),
        ai_fallback_enabled=os.getenv("AI_FALLBACK_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        ai_fallback_provider=os.getenv("AI_FALLBACK_PROVIDER", "openai").lower(),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        openai_model=os.getenv("OPENAI_MODEL"),
        ai_action_expiration_seconds=max(
            60, int(os.getenv("AI_ACTION_EXPIRATION_SECONDS", "600"))
        ),
        github_enabled=os.getenv("GITHUB_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        github_repository_owner=os.getenv("GITHUB_REPOSITORY_OWNER"),
        github_repository_name=os.getenv("GITHUB_REPOSITORY_NAME"),
        github_token=os.getenv("GITHUB_TOKEN"),
        github_request_timeout=max(1, int(os.getenv("GITHUB_REQUEST_TIMEOUT", "15"))),
        github_cache_ttl=max(1, int(os.getenv("GITHUB_CACHE_TTL", "300"))),
        github_max_results=max(1, min(100, int(os.getenv("GITHUB_MAX_RESULTS", "30")))),
        github_webhook_enabled=os.getenv(
            "GITHUB_WEBHOOK_ENABLED", "false"
        ).lower()
        in {"1", "true", "yes", "on"},
        github_webhook_secret=os.getenv("GITHUB_WEBHOOK_SECRET"),
        # The deployment-controlled listener intentionally defaults to all interfaces.
        webhook_host=(os.getenv("WEBHOOK_HOST") or "0.0.0.0").strip(),  # nosec B104
        webhook_port=max(
            1, min(65535, int(os.getenv("WEBHOOK_PORT") or "8080"))
        ),
        github_webhook_path=(
            os.getenv("GITHUB_WEBHOOK_PATH") or "/webhooks/github"
        ).strip(),
        github_bug_label=(os.getenv("GITHUB_BUG_LABEL") or "bug").strip(),
    )
