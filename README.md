# CSE-HQ Bot MVP

CSE-HQ is a Discord bot MVP for project coordination. It provides:

- Role-aware project, task, and bug operations for Leaders, Co-Leads, and Members
- SQLite-backed persistence for project settings, tasks, bugs, meetings, decisions, and standups
- Weekly progress reporting
- Grounded project Q&A through a pluggable AI provider
- A private, grounded assistant with persistent sessions and explicitly confirmed internal actions
- A fake AI provider for local development, Gemini as the production primary, and optional OpenAI failover
- Idempotent Discord Forum publishing for bugs, pull requests, releases, and verified GitHub webhook events

## Requirements

- Python 3.11 or newer
- A Discord application and bot token
- A server or local machine that can run a long-lived Python process
- Optional: a Google Gemini API key for Gemini-powered project Q&A and the private `/ai` assistant
- Optional: an OpenAI API key and configured model when AI provider failover is enabled

## Local Setup

1. Clone the repository and enter the project directory:

   ```bash
   git clone https://github.com/Lam5943/CSE-HQ-bot-2026.git
   cd CSE-HQ-bot-2026
   ```

2. Create and activate a Python virtual environment:

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```

   On Windows PowerShell, use:

   ```powershell
   py -3.11 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Install the project and development dependencies:

   ```bash
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev]"
   ```

4. Create the environment file:

   ```bash
   cp .env.example .env
   ```

5. Edit `.env` and set at least `DISCORD_TOKEN`. Keep `AI_PROVIDER=fake` unless Gemini is configured. If you want natural AI session chat in Discord threads, set `AI_ENABLE_MESSAGE_CONTENT=true` and also enable the Message Content intent for the bot in the Discord Developer Portal.

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Token for the Discord bot. Never commit this value. |
| `DISCORD_GUILD_ID` | No | — | Optional Discord server ID for environment-specific configuration. |
| `DATABASE_PATH` | No | `./cse_hq.db` | Path to the SQLite database file. |
| `LOG_LEVEL` | No | `INFO` | Python logging level, such as `DEBUG`, `INFO`, or `WARNING`. |
| `AI_ENABLE_MESSAGE_CONTENT` | No | `false` | Enable Discord message-content intent for natural `/ai` thread chat. When disabled, AI threads return one concise configuration hint per user instead of failing silently. |
| `AI_PROVIDER` | No | `fake` | Set to `fake` for local development or `gemini` to use Gemini. |
| `AI_MODEL` | No | `gemini-1.5-flash` | Gemini model name passed to the provider when `AI_PROVIDER=gemini`. |
| `GEMINI_API_KEY` | Required when `AI_PROVIDER=gemini` | — | Google Gemini API key. The app fails fast with a configuration error if this is missing. |
| `AI_MAX_CONTEXT_ITEMS` | No | `12` | Maximum bounded project records retrieved for one AI answer. |
| `AI_MAX_HISTORY_MESSAGES` | No | `8` | Maximum persisted user/assistant messages loaded into one AI request. |
| `AI_REQUEST_TIMEOUT` | No | `20` | Per-attempt request timeout in seconds. Failover may use one primary attempt plus one fallback attempt. |
| `AI_FALLBACK_ENABLED` | No | `false` | Enable one OpenAI fallback attempt after an eligible Gemini availability failure. |
| `AI_FALLBACK_PROVIDER` | No | `openai` | Fallback provider name. AI Provider Failover v1 supports only `openai`. |
| `OPENAI_API_KEY` | Required for fallback attempts | — | OpenAI API key. It is optional at startup and unused while fallback is disabled. |
| `OPENAI_MODEL` | Required for fallback attempts | — | Deployment-selected OpenAI model; no OpenAI model is hardcoded. |
| `AI_ACTION_EXPIRATION_SECONDS` | No | `600` | Seconds before an unconfirmed AI action proposal expires; minimum 60 seconds. |
| `GITHUB_ENABLED` | No | `false` | Enable the optional read-only GitHub integration. |
| `GITHUB_REPOSITORY_OWNER` | Required when GitHub is enabled | — | Owner of the single repository connected in v1. |
| `GITHUB_REPOSITORY_NAME` | Required when GitHub is enabled | — | Repository name connected in v1. |
| `GITHUB_TOKEN` | Required when GitHub is enabled | — | Environment-only GitHub App or fine-grained token with read-only repository permissions. |
| `GITHUB_REQUEST_TIMEOUT` | No | `15` | GitHub API timeout in seconds. |
| `GITHUB_CACHE_TTL` | No | `300` | Seconds before cached GitHub data is displayed as stale. |
| `GITHUB_MAX_RESULTS` | No | `30` | Maximum normalized records fetched per GitHub resource type (capped at 100). |
| `GITHUB_WEBHOOK_ENABLED` | No | `false` | Start the inbound GitHub webhook endpoint. |
| `GITHUB_WEBHOOK_SECRET` | Required when webhooks are enabled | — | Shared secret used to verify `X-Hub-Signature-256`. Never commit it. |
| `WEBHOOK_HOST` | No | `0.0.0.0` | Interface used by the webhook HTTP listener. |
| `WEBHOOK_PORT` | No | `8080` | Port used by the webhook HTTP listener. |
| `GITHUB_WEBHOOK_PATH` | No | `/webhooks/github` | Fixed POST path for GitHub webhook deliveries. |
| `GITHUB_BUG_LABEL` | No | `bug` | Exact, case-insensitive GitHub Issue label used for deterministic bug routing. |

Example development configuration:

```dotenv
DISCORD_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_discord_server_id
DATABASE_PATH=./cse_hq.db
LOG_LEVEL=INFO
AI_ENABLE_MESSAGE_CONTENT=false
AI_PROVIDER=fake
AI_MODEL=gemini-1.5-flash
GEMINI_API_KEY=
AI_MAX_CONTEXT_ITEMS=12
AI_MAX_HISTORY_MESSAGES=8
AI_REQUEST_TIMEOUT=20
AI_ACTION_EXPIRATION_SECONDS=600
GITHUB_WEBHOOK_ENABLED=false
GITHUB_WEBHOOK_SECRET=
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080
GITHUB_WEBHOOK_PATH=/webhooks/github
GITHUB_BUG_LABEL=bug
```

## Discord Application Setup

1. Open the Discord Developer Portal and create or select an application.
2. Open **Bot**, create the bot user, and copy its token into `DISCORD_TOKEN`.
3. Under **Privileged Gateway Intents**, enable only the intents required by the bot and by your Discord server configuration.
4. Use **OAuth2 → URL Generator** to invite the bot to your server.
5. Select the `bot` and `applications.commands` scopes.
6. Grant the bot permission to view channels, send messages, and use application commands.
7. Invite the bot using the generated URL.

Treat the Discord token as a password. If it is exposed, regenerate it immediately in the Developer Portal.

## Running the Bot

Start the bot from the project directory with the virtual environment activated:

```bash
python -m cse_hq_bot.bot
```

The bot requires `DISCORD_TOKEN`. On startup, the SQLite schema is initialized in an idempotent way, so restarting the bot does not require a manual database migration for the existing MVP schema.

To stop the bot locally, press `Ctrl+C`.

## Running on a Linux Server with systemd

The following example assumes:

- The repository is installed at `/opt/cse-hq-bot`
- A dedicated Linux user named `csebot` runs the service
- The virtual environment is `/opt/cse-hq-bot/.venv`
- The environment file is `/etc/cse-hq-bot.env`

### 1. Prepare the server

```bash
sudo useradd --system --create-home --home-dir /opt/cse-hq-bot --shell /usr/sbin/nologin csebot
sudo mkdir -p /opt/cse-hq-bot
sudo chown -R csebot:csebot /opt/cse-hq-bot
sudo -u csebot git clone https://github.com/Lam5943/CSE-HQ-bot-2026.git /opt/cse-hq-bot
sudo -u csebot python3.11 -m venv /opt/cse-hq-bot/.venv
sudo -u csebot /opt/cse-hq-bot/.venv/bin/python -m pip install --upgrade pip
sudo -u csebot /opt/cse-hq-bot/.venv/bin/pip install -e /opt/cse-hq-bot
```

### 2. Create the server environment file

```bash
sudo cp /opt/cse-hq-bot/.env.example /etc/cse-hq-bot.env
sudo nano /etc/cse-hq-bot.env
sudo chmod 600 /etc/cse-hq-bot.env
sudo chown root:root /etc/cse-hq-bot.env
```

Set `DISCORD_TOKEN` and the other values in `/etc/cse-hq-bot.env`. For Gemini, use `AI_PROVIDER=gemini`, set `AI_MODEL`, and provide `GEMINI_API_KEY`.

For a server deployment, consider using an absolute database path so that the database location is independent of the service working directory:

```dotenv
DATABASE_PATH=/var/lib/cse-hq-bot/cse_hq.db
```

Create the database directory and grant access to the service user:

```bash
sudo mkdir -p /var/lib/cse-hq-bot
sudo chown csebot:csebot /var/lib/cse-hq-bot
```

### 3. Create the systemd service

Create `/etc/systemd/system/cse-hq-bot.service`:

```ini
[Unit]
Description=CSE-HQ Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=csebot
Group=csebot
WorkingDirectory=/opt/cse-hq-bot
EnvironmentFile=/etc/cse-hq-bot.env
ExecStart=/opt/cse-hq-bot/.venv/bin/python -m cse_hq_bot.bot
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cse-hq-bot.service
sudo systemctl status cse-hq-bot.service
```

View live logs:

```bash
sudo journalctl -u cse-hq-bot.service -f
```

After pulling a new version, update the installation and restart the service:

```bash
cd /opt/cse-hq-bot
sudo -u csebot git pull --ff-only
sudo -u csebot /opt/cse-hq-bot/.venv/bin/pip install -e /opt/cse-hq-bot
sudo systemctl restart cse-hq-bot.service
```

Back up the SQLite database before upgrades or server maintenance:

```bash
sudo cp /var/lib/cse-hq-bot/cse_hq.db /var/lib/cse-hq-bot/cse_hq.db.backup
sudo chown csebot:csebot /var/lib/cse-hq-bot/cse_hq.db.backup
```

## Bot Usage

The current MVP exposes these Discord application commands:

### `/dashboard`

Opens a private interactive dashboard Embed with:

- Project overview (name, description, goal, phase, sprint, deadline, status)
- Task and bug summary metrics
- Refresh button to reload the latest project data
- **Manage Dashboard** button (Leader/Co-Lead only through service-layer permissions) to update project management fields

### `/tasks`

Opens a private task-management panel:

- Task list in an Embed
- Refresh button
- **Create Task** modal (title, description, priority, assignee user ID, deadline) backed by `TaskService`

### `/bugs`

Opens a private bug-tracker panel:

- Bug list in an Embed
- Filter selector for open/all bugs
- Refresh button
- **Report Bug** modal (title, description, severity, assignee user ID) backed by `BugService`

### `/meetings`

Opens a private meetings panel with:

- Meeting counts and browse modes for upcoming, active, and history
- **Create** modal for title, description, agenda, and scheduled time
- Detail actions for lifecycle changes, participant management, structured notes, linked decisions, and action-task creation

### `/decisions`

Opens a private decisions panel with:

- Paginated browse and search views
- **Record Decision** modal with title, decision text, context, rationale, and alternatives
- Detail view for linked meeting, recorder, and editable decision metadata

### `/standup`

Opens a private standup panel with:

- Your current daily submission status
- **Submit / Update** modal for previous work, current work, and optional blockers
- Team and recent-history views suitable for Discord embed pagination

### `/ai`

Opens the private AI assistant home panel with:

- Grounded Q&A for tasks, bugs, meetings, decisions, standups, GitHub context, and recent activity
- Human-confirmed proposals for one supported Task, Bug, Meeting, Decision, or own Standup mutation at a time
- **New Session** to create a private Discord thread-backed AI session
- **My Sessions** to list your persisted AI sessions
- Natural thread conversation for authorized session owners only
- Bounded persisted conversation history controlled by `AI_MAX_HISTORY_MESSAGES`

The assistant is intentionally bounded and permission-aware:

- CSE-HQ remains the source of truth for project state and permissions
- Project-specific answers are grounded only in retrieved records the actor may access
- If project evidence is missing, the assistant should say so explicitly
- Supported mutations produce a persisted preview with **Confirm** and **Cancel**; no mutation occurs when the preview is shown
- Confirmation ownership, expiry, session state, permissions, target state, and lifecycle transitions are checked again before execution
- Only the current user's standup may be submitted or updated; other-user and team-wide standup writes are rejected
- GitHub, repository, deletion, batch, autonomous, and multi-step actions remain unavailable
- Prior AI replies are continuity only; fresh project retrieval wins on every request

### `/weekly_report`

Displays the current weekly progress report. The response is private to the user who invoked the command.

### `/github`

Opens a private, read-only development context panel backed by the local GitHub cache:

- Repository status, open issue/PR counts, failing checks, and cache freshness
- Bounded issue, pull-request, commit, and branch browsers
- Review and CI/check status normalized for reporting and Gemini grounding
- Manual sync restricted by service-layer Leader/Co-Lead permissions
- Explicit task/bug links to cached GitHub issues or pull requests

GitHub v1 never creates, edits, closes, merges, comments, reviews, triggers workflows, or pushes code. GitHub remains authoritative; SQLite only retains a bounded cache so existing data remains available after a failed refresh.

### `/setup forums`

Allows a Leader or Co-Lead to select existing Discord Forum channels for Bugs,
Pull Requests, and Releases. CSE-HQ validates that the bot can view the channel,
create public threads, send starter messages, and reply inside threads before it
saves all three mappings. The optional `test_publish` switch creates a harmless
test post in each configured Forum. Forum channel IDs are stored in SQLite, never
in environment variables.

The underlying service layer also supports project, task, bug, meeting, decision, standup, collaboration, reporting, and grounded Q&A operations. Role checks are enforced in the service layer rather than relying only on Discord channel visibility. Additional Discord commands should be added as the command surface is expanded.

## Configuring Google Gemini AI

Gemini is optional. The bot uses the fake provider by default, so the application can run without an external AI service.

### 1. Create a Gemini API key

Create an API key through Google AI Studio or your approved Google Cloud configuration. Keep the key private and do not commit it to Git.

### 2. Configure the environment

Update `.env` on a local machine or `/etc/cse-hq-bot.env` on a server:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
AI_MODEL=gemini-1.5-flash
AI_REQUEST_TIMEOUT=20
AI_FALLBACK_ENABLED=false
AI_FALLBACK_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=
AI_ACTION_EXPIRATION_SECONDS=600
```

`GEMINI_API_KEY` is required when `AI_PROVIDER=gemini`. `AI_MODEL` is passed directly to the supported `google.genai` client. `AI_REQUEST_TIMEOUT` is enforced both at the SDK request layer and by the provider's async deadline.

### 3. Restart the bot

For a local process:

```bash
python -m cse_hq_bot.bot
```

For systemd:

```bash
sudo systemctl restart cse-hq-bot.service
sudo journalctl -u cse-hq-bot.service -n 100 --no-pager
```

The Q&A service builds bounded, permission-aware context from CSE-HQ records and, when enabled, the normalized GitHub cache. The same rendered prompt package is sent to either provider. It instructs the model to treat records as untrusted data, state when evidence is insufficient, preserve source IDs, and never claim to modify project or GitHub records.

If Gemini initialization fails, verify all of the following:

- `AI_PROVIDER` is exactly `gemini`.
- `GEMINI_API_KEY` is present and valid.
- `AI_MODEL` is a model available to the configured Gemini API account.
- The installed `google-genai` dependency is present in the active virtual environment.
- The server can make outbound HTTPS requests.

### Optional OpenAI fallback

Gemini remains the primary provider. Enable a single OpenAI fallback attempt with:

```dotenv
AI_PROVIDER=gemini
AI_FALLBACK_ENABLED=true
AI_FALLBACK_PROVIDER=openai
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=your_configured_openai_model
AI_REQUEST_TIMEOUT=20
```

Fallback is attempted only after a normalized rate-limit, timeout, temporary connection, or provider-unavailable failure. Configuration errors, malformed provider responses, permission/session failures, retrieval failures, and other application errors do not trigger fallback. The same system instructions, bounded context, conversation history, and question are reused; retrieval and citation validation run only once.

Each provider attempt is limited by `AI_REQUEST_TIMEOUT`, SDK retries are disabled for the OpenAI fallback, and at most one fallback call is made. Therefore the worst-case provider time may approach two timeout windows. Missing OpenAI credentials do not stop Gemini startup or successful Gemini requests, but an actual fallback attempt returns a controlled configuration error. Failover improves resilience but does not guarantee availability.

## AI Actions v2.1

AI Actions v2.1 extends the existing bounded mutation path across CSE-HQ's internal project-management domains. The language model may interpret a request into a structured proposal, but it cannot execute service methods. CSE-HQ validates the proposal, persists it, displays its exact effect, and requires the proposal owner to press **Confirm** before application code invokes the existing domain service.

Supported actions are:

- Tasks: create, assign, start, block, complete, and reopen
- Bugs: assign, transition to a supported status, resolve, and reopen
- Meetings: create, start, complete, cancel, add a participant, and add a note
- Decisions: create and edit
- Standups: submit or update the requesting user's standup for the current day

Every proposal is limited to one action, expires after `AI_ACTION_EXPIRATION_SECONDS`, and is bound to its private AI session and requesting actor. Confirmation does not call Gemini or OpenAI again. Instead, CSE-HQ atomically claims the pending proposal, re-fetches the target, rechecks session state, ownership, permissions, and lifecycle state, then calls the existing domain service. Repeated or simultaneous confirmation cannot execute the same proposal twice. Successful mutations use the existing domain activity logging; cancelled, expired, stale, and failed proposals do not report false success.

Closing a session marks its pending proposals failed and non-executable. Expiration is enforced from the persisted timestamp at confirmation time (including the exact expiry boundary), independently of Discord's View timeout. State or permission drift makes the claimed proposal fail rather than returning it to `PENDING`.

Assignments and meeting participants resolve only to known Discord members available to the message context and are revalidated against the current Discord guild member cache at confirmation time. Ambiguous, removed, or no-longer-visible members are rejected. Meeting and decision titles are accepted only when they resolve unambiguously; stable `MEETING-###` and `DEC-###` identifiers are preferred. Raw or malformed model output, unknown actions, arbitrary parameters, and inaccessible records cannot become executable proposals.

Meeting times are normalized to an absolute value before the proposal is persisted, so the confirmation preview shows the exact scheduled time. Meeting lifecycle validation remains authoritative in `MeetingService`. Meeting notes preserve the proposed content exactly.

Decision edits show explicit old and new values and update only the requested fields. Stale decision state is rejected before overwrite, and successful edits retain the existing domain activity history. Standup proposals are restricted to the owner and current date, reuse the existing daily upsert rules, and are rejected if the date changes before confirmation.

Proposal records remain auditable across bot restarts, but confirmation Views are not restored after a restart in the current architecture. A pending proposal therefore never executes silently; the user must create a fresh proposal if its original buttons are no longer active.

GitHub remains strictly read-only. Deletion, GitHub writes, batch actions, chained workflows, autonomous execution, shell commands, repository changes, other-user standup writes, and delegated confirmation are not supported. **The AI cannot modify project data without explicit user confirmation.**

## Configuring GitHub Integration v1

GitHub integration is optional and disabled by default. Use a GitHub App installation token or fine-grained personal access token limited to read-only access for repository metadata, issues, pull requests, commits, checks, and branches.

```dotenv
GITHUB_ENABLED=true
GITHUB_REPOSITORY_OWNER=your_owner
GITHUB_REPOSITORY_NAME=your_repository
GITHUB_TOKEN=your_read_only_token
GITHUB_REQUEST_TIMEOUT=15
GITHUB_CACHE_TTL=300
GITHUB_MAX_RESULTS=30
```

Run `/github`, then use **Sync** as a Leader or Co-Lead. Normal browsing and Gemini retrieval use SQLite-cached normalized records; they do not call GitHub on every interaction. A failed refresh records stale status while preserving the last successful snapshot. Never commit `GITHUB_TOKEN` or log authorization headers.

## Discord Forum Publishing and GitHub Webhooks

Forum publishing is a presentation layer over the existing domain data. Internal
bug creation and meaningful bug updates publish after the domain transaction has
succeeded. A failed Discord publication is logged but never rolls back the bug.
The `forum_publications` mapping enforces one Forum post per entity; later status,
assignment, PR, and CI events update or reply in the mapped thread instead of
creating duplicates.

Inbound GitHub webhooks are optional and independent of the read-only cache sync.
To enable them, configure the single repository, shared secret, listener, and bug
label:

```dotenv
GITHUB_REPOSITORY_OWNER=your_owner
GITHUB_REPOSITORY_NAME=your_repository
GITHUB_WEBHOOK_ENABLED=true
GITHUB_WEBHOOK_SECRET=replace_with_a_strong_random_secret
WEBHOOK_HOST=0.0.0.0
WEBHOOK_PORT=8080
GITHUB_WEBHOOK_PATH=/webhooks/github
GITHUB_BUG_LABEL=bug
```

Configure the same secret and POST URL in the repository webhook settings. The
listener verifies `X-Hub-Signature-256`, rejects another repository, and deduplicates
`X-GitHub-Delivery` before routing supported pull-request, issue, release, workflow,
and check events. GitHub Issues publish to the Bug Forum only when they contain the
configured label; internal `BUG-###` and external `GH-ISSUE-###` identities remain
separate. PR opened/reopened/ready/closed/merged events reuse one post, and CI
updates reply inside an existing PR thread when available. Release posts are
created only for `release.published`.

All external titles and bodies are bounded and mentions are neutralized. Raw
payloads and secrets are never posted or logged. Put the listener behind HTTPS or
a trusted TLS-terminating reverse proxy in production. This integration performs
no GitHub writes and uses no AI for event classification.

## Tests

Run the test suite with:

```bash
pytest
```

Latest Forum Publishing validation (2026-09-21): 231 offline tests passed. Python
compilation, Ruff and Bandit on the changed code, `pip check`, `pip-audit`, and
working-tree plus Git-history secret-signature scans also passed. The intentional
`0.0.0.0` listener default has a documented Bandit B104 suppression because it is
deployment-controlled and required by the webhook configuration contract.
Repository-wide Ruff and Bandit still report pre-existing style findings and five
dynamic-SQL heuristics outside this milestone.

## Project Notes

- Application-level permissions are enforced in service-layer helpers, not only through Discord visibility.
- SQLite schema initialization is safe to run repeatedly.
- Meaningful task, bug, meeting, decision, and standup mutations append immutable activity records after the primary write succeeds.
- `ProjectContextService` provides permission-aware, structured project reads for future reporting and grounded retrieval without bypassing domain services.
- The fake AI provider is the recommended default for development and automated tests.
- Keep `.env`, Discord tokens, Gemini/OpenAI API keys, and production database backups out of version control.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
