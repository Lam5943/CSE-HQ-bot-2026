from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    discord_token: str | None
    discord_guild_id: int | None
    database_path: str
    log_level: str
    gemini_api_key: str | None
    gemini_model: str
    ai_provider: str



def load_config() -> Config:
    load_dotenv()
    guild_id = os.getenv("DISCORD_GUILD_ID")
    return Config(
        discord_token=os.getenv("DISCORD_TOKEN"),
        discord_guild_id=int(guild_id) if guild_id else None,
        database_path=os.getenv("DATABASE_PATH", "./cse_hq.db"),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-1.5-flash"),
        ai_provider=os.getenv("AI_PROVIDER", "fake").lower(),
    )
