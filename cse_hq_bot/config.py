import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    database_path: str
    log_level: str
    ai_enable_message_content: bool
    welcome_enabled: bool
    welcome_channel_id: str | None
    web_research_enabled: bool
    tavily_api_key: str | None
    web_research_max_results: int
    web_research_timeout: int
    gemini_api_key: str | None
    ai_model: str
    groq_api_key: str | None
    groq_model: str
    groq_max_output_tokens: int
    ai_provider: str
    ai_max_context_items: int
    ai_max_history_messages: int
    ai_request_timeout: int
    ai_primary_retries: int
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
    return Config(
        discord_token=os.getenv("DISCORD_TOKEN"),
        database_path=os.getenv("DATABASE_PATH", "./cse_hq.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_enable_message_content=os.getenv(
            "AI_ENABLE_MESSAGE_CONTENT", "false"
        ).lower()
        in {"1", "true", "yes", "on"},
        welcome_enabled=os.getenv("WELCOME_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        welcome_channel_id=(os.getenv("WELCOME_CHANNEL_ID") or "").strip() or None,
        web_research_enabled=os.getenv("WEB_RESEARCH_ENABLED", "false").lower()
        in {"1", "true", "yes", "on"},
        tavily_api_key=(os.getenv("TAVILY_API_KEY") or "").strip() or None,
        web_research_max_results=max(
            1,
            min(8, int(os.getenv("WEB_RESEARCH_MAX_RESULTS", "4"))),
        ),
        web_research_timeout=max(
            1,
            int(os.getenv("WEB_RESEARCH_TIMEOUT", "12")),
        ),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        ai_model=os.getenv("AI_MODEL", "gemini-1.5-flash"),
        groq_api_key=os.getenv("GROQ_API_KEY"),
        groq_model=os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b"),
        groq_max_output_tokens=max(1, int(os.getenv("GROQ_MAX_OUTPUT_TOKENS", "700"))),
        ai_provider=os.getenv("AI_PROVIDER", "fake").lower(),
        ai_max_context_items=max(1, int(os.getenv("AI_MAX_CONTEXT_ITEMS", "6"))),
        ai_max_history_messages=max(1, int(os.getenv("AI_MAX_HISTORY_MESSAGES", "4"))),
        ai_request_timeout=max(1, int(os.getenv("AI_REQUEST_TIMEOUT", "30"))),
        ai_primary_retries=max(0, min(2, int(os.getenv("AI_PRIMARY_RETRIES", "1")))),
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
