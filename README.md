# CSE-HQ Bot MVP

CSE-HQ is a Discord bot MVP for project coordination. It provides:

- Role-aware project/task/bug operations (Leader, Co-Lead, Member)
- SQLite-backed persistence for dashboard settings, tasks, bugs, meetings, decisions, standups
- Weekly progress reporting
- Grounded project Q&A via pluggable AI provider (fake provider by default, Gemini optional)

## Setup

1. Create and activate a Python 3.11+ virtual environment.
2. Install dependencies:
   ```bash
   pip install -e .[dev]
   ```
3. Copy environment variables:
   ```bash
   cp .env.example .env
   ```
4. Set `DISCORD_TOKEN` to run the bot. Leave `AI_PROVIDER=fake` unless Gemini is configured.

## Run

```bash
python -m cse_hq_bot.bot
```

On startup, SQLite schema initialization is idempotent and safe to run repeatedly.

## Tests

```bash
pytest
```

## Notes

- Application-level permissions are enforced in service layer helpers, not only through Discord visibility.
- Q&A is constrained to persisted project context and cannot mutate records.
