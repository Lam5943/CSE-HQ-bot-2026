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
    github_enabled: bool
    github_repository_owner: str | None
    github_repository_name: str | None
    github_token: str | None
    github_request_timeout: int
    github_cache_ttl: int
    github_max_results: int


def load_config() -> Config:
    load_dotenv()
    guild_id = os.getenv("DISCORD_GUILD_ID")
    return Config(
        discord_token=os.getenv("DISCORD_TOKEN"),
        discord_guild_id=int(guild_id) if guild_id else None,
        database_path=os.getenv("DATABASE_PATH", "./cse_hq.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        ai_enable_message_content=os.getenv("AI_ENABLE_MESSAGE_CONTENT", "false").lower() in {"1", "true", "yes", "on"},
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        ai_model=os.getenv("AI_MODEL") or os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        ai_provider=os.getenv("AI_PROVIDER", "fake").lower(),
        ai_max_context_items=max(1, int(os.getenv("AI_MAX_CONTEXT_ITEMS", "12"))),
        ai_max_history_messages=max(1, int(os.getenv("AI_MAX_HISTORY_MESSAGES", "8"))),
        ai_request_timeout=max(1, int(os.getenv("AI_REQUEST_TIMEOUT", "20"))),
        github_enabled=os.getenv("GITHUB_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
        github_repository_owner=os.getenv("GITHUB_REPOSITORY_OWNER"),
        github_repository_name=os.getenv("GITHUB_REPOSITORY_NAME"),
        github_token=os.getenv("GITHUB_TOKEN"),
        github_request_timeout=max(1, int(os.getenv("GITHUB_REQUEST_TIMEOUT", "15"))),
        github_cache_ttl=max(1, int(os.getenv("GITHUB_CACHE_TTL", "300"))),
        github_max_results=max(1, min(100, int(os.getenv("GITHUB_MAX_RESULTS", "30")))),
    )
