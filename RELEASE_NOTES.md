# CSE-HQ v1.0.0

CSE-HQ v1.0.0 is the first stable release of the Discord-native project coordination bot.

## Highlights

- Discord-native dashboards and workflows for projects, tasks, bugs, meetings, decisions, standups, and weekly activity reporting.
- Durable project memory in SQLite, including bounded conversation history and restart-safe domain state.
- A grounded, permission-aware AI assistant using Gemini with an optional single-attempt OpenAI fallback.
- Explicitly confirmed AI Actions for supported internal project mutations; the model cannot execute actions directly.
- Read-only GitHub repository context with a bounded local cache, manual maintainer sync, and safe degradation when GitHub is unavailable.
- Idempotent Discord Forum publishing for bugs, pull requests, releases, and verified GitHub webhook events.
- Leader/CoLead operational diagnostics through `/health` without live provider probes or secret exposure.
- Automated offline regression, Ruff, Bandit, dependency, secret-signature, and CodeQL checks for pull requests.

## Deployment notes

- Python 3.11 or newer is required; CI validates Python 3.12.
- Existing SQLite databases are upgraded additively during startup. Back up the database before deploying a new version.
- Keep the SQLite database on persistent storage and run only one bot process against it.
- Natural AI thread chat requires `AI_ENABLE_MESSAGE_CONTENT=true` and Discord's Message Content privileged intent.
- The optional webhook listener must be placed behind public HTTPS or a trusted TLS-terminating reverse proxy.
- Pending AI proposal buttons are ephemeral Discord UI and are not restored after restart; no proposal executes automatically.

See `README.md` and `.env.example` for the complete configuration and operating guide.
