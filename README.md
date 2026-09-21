# CSE-HQ v1.0.0

[![CI](https://github.com/Lam5943/CSE-HQ-bot-2026/actions/workflows/ci.yml/badge.svg)](https://github.com/Lam5943/CSE-HQ-bot-2026/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Lam5943/CSE-HQ-bot-2026/actions/workflows/codeql.yml/badge.svg)](https://github.com/Lam5943/CSE-HQ-bot-2026/actions/workflows/codeql.yml)

CSE-HQ is a Discord-native project coordination bot. Version 1.0.0 provides:

- Role-aware project, task, and bug operations for Leaders, Co-Leads, and Members
- SQLite-backed persistence for project settings, tasks, bugs, meetings, decisions, and standups
- Weekly progress reporting
- Public weekly dashboard snapshots with SQLite-backed scheduling and deduplication
- Grounded project Q&A through a pluggable AI provider
- A private, grounded assistant with persistent sessions, bounded image understanding, a provider-independent mentor personality, direct @mention replies, read-only web research, and explicitly confirmed internal actions
- Automatic new-member welcomes using the shared CSE-HQ Discord card design
- A fake AI provider for local development, Groq as the recommended free production primary, Gemini as an optional alternative, and optional OpenAI failover
- Idempotent Discord Forum publishing for bugs, pull requests, releases, and verified GitHub webhook events
- Automatic pull-request validation with offline tests, quality gates, dependency auditing, secret-signature checks, and CodeQL

See [RELEASE_NOTES.md](RELEASE_NOTES.md) for the stable release summary.

## Requirements

- Python 3.11 or newer (CI validates Python 3.12)
- A Discord application and bot token
- A server or local machine that can run a long-lived Python process
- Optional: a Groq API key for the recommended free production AI path
- Optional: a Google Gemini API key when Gemini is selected instead
- Optional: an OpenAI API key and configured model when paid fallback is explicitly enabled
- Optional: a Tavily API key for bounded, read-only live web research

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

5. Edit `.env` and set at least `DISCORD_TOKEN`. Keep `AI_PROVIDER=fake` for local development; use `AI_PROVIDER=groq` with a Groq key for the recommended free production path, or `AI_PROVIDER=gemini` if Gemini is intentionally selected. If you want natural AI session chat in Discord threads, set `AI_ENABLE_MESSAGE_CONTENT=true` and also enable the Message Content intent for the bot in the Discord Developer Portal. Enable `WELCOME_ENABLED=true` only after enabling Discord's Server Members Intent. For live read-only web research, set `WEB_RESEARCH_ENABLED=true` and provide `TAVILY_API_KEY`.

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Token for the Discord bot. Never commit this value. |
| `DATABASE_PATH` | No | `./cse_hq.db` | Path to the SQLite database file. |
| `LOG_LEVEL` | No | `INFO` | Python logging level, such as `DEBUG`, `INFO`, or `WARNING`. |
| `AI_ENABLE_MESSAGE_CONTENT` | No | `false` | Enable Discord message-content intent for natural `/ai` thread chat. When disabled, AI threads return one concise configuration hint per user instead of failing silently. |
| `WELCOME_ENABLED` | No | `false` | Enable automatic welcome messages for new human members. Requires Discord's privileged Server Members Intent. |
| `WELCOME_CHANNEL_ID` | No | — | Optional text-channel override for welcomes. When omitted, CSE-HQ prefers the server System Messages channel and then the first writable text channel. |
| `WEB_RESEARCH_ENABLED` | No | `false` | Enable bounded read-only live web search for explicit research requests and freshness-sensitive external questions. |
| `TAVILY_API_KEY` | Required when web research is enabled | — | Tavily API key used only with the Search endpoint. Never commit this value. |
| `WEB_RESEARCH_MAX_RESULTS` | No | `4` | Maximum search results injected into one AI request, capped at 8. |
| `WEB_RESEARCH_TIMEOUT` | No | `12` | Total timeout in seconds for one Tavily search request. |
| `AI_PROVIDER` | No | `fake` | `fake`, `groq`, or `gemini`. Groq is the recommended free production provider. |
| `GROQ_API_KEY` | Required when `AI_PROVIDER=groq` | — | Groq API key. Never commit this value. |
| `GROQ_MODEL` | No | `qwen/qwen3.8-27b` | Groq model used for text and vision when `AI_PROVIDER=groq`. |
| `AI_MODEL` | No | `gemini-1.5-flash` | Gemini model name used only when `AI_PROVIDER=gemini`. |
| `GEMINI_API_KEY` | Required when `AI_PROVIDER=gemini` | — | Google Gemini API key. |
| `AI_MAX_CONTEXT_ITEMS` | No | `6` | Maximum bounded project records retrieved for one AI answer; the lower default reduces free-tier token pressure. |
| `AI_MAX_HISTORY_MESSAGES` | No | `4` | Maximum persisted user/assistant messages loaded into one AI request. |
| `AI_REQUEST_TIMEOUT` | No | `30` | Per-attempt request timeout in seconds. |
| `AI_PRIMARY_RETRIES` | No | `1` | Retry count for transient rate-limit or provider-unavailable failures. Timeouts are not automatically retried. |
| `AI_FALLBACK_ENABLED` | No | `false` | Enable one OpenAI fallback attempt after an eligible primary-provider failure. Keep disabled to avoid paid fallback usage. |
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
DATABASE_PATH=./cse_hq.db
LOG_LEVEL=INFO
AI_ENABLE_MESSAGE_CONTENT=false
WELCOME_ENABLED=false
WELCOME_CHANNEL_ID=
WEB_RESEARCH_ENABLED=false
TAVILY_API_KEY=
WEB_RESEARCH_MAX_RESULTS=4
WEB_RESEARCH_TIMEOUT=12
AI_PROVIDER=fake
GROQ_API_KEY=
GROQ_MODEL=qwen/qwen3.8-27b
AI_MODEL=gemini-1.5-flash
GEMINI_API_KEY=
AI_MAX_CONTEXT_ITEMS=6
AI_MAX_HISTORY_MESSAGES=4
AI_REQUEST_TIMEOUT=30
AI_PRIMARY_RETRIES=1
AI_FALLBACK_ENABLED=false
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
3. Under **Privileged Gateway Intents**, enable **Message Content** when `AI_ENABLE_MESSAGE_CONTENT=true`, and enable **Server Members Intent** when `WELCOME_ENABLED=true`. Leave unused privileged intents disabled.
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

The bot requires `DISCORD_TOKEN`. On startup, the SQLite schema is initialized
and upgraded additively in an idempotent way, so a fresh or supported older
database does not require a separate migration command.

To stop the bot locally, press `Ctrl+C`.

## Running on a Linux Server with systemd

This is the documented deployment shape, not a claim of support for every host
or process manager. Before deployment, confirm:

- Python 3.11+ and outbound HTTPS access are available.
- The bot is invited with `bot` and `applications.commands`, and can view/send
  messages, create private threads, and use application commands. Forum publishing
  additionally needs view, create-public-thread, send-message, and thread reply
  permissions in each configured Forum.
- The default Guilds and Guild Messages intents remain enabled. Discord's
  privileged Message Content intent is required only when natural AI thread chat
  is enabled with `AI_ENABLE_MESSAGE_CONTENT=true`. Direct bot mentions can be
  handled without opening a private AI session.
- Automatic welcomes require `WELCOME_ENABLED=true` and Discord's privileged
  Server Members Intent. If `WELCOME_CHANNEL_ID` is unset, CSE-HQ selects the
  server System Messages channel or another writable text channel.
- `DATABASE_PATH` points to backed-up persistent storage. Run one CSE-HQ process
  against a SQLite database; multi-process SQLite deployment is not tested.
- Groq/Gemini/OpenAI credentials and model names are present only for providers you
  enable. GitHub uses a read-only fine-grained token or GitHub App installation
  token with metadata, issues, pull requests, commits, checks, and branch reads.
- If webhooks are enabled, the configured `WEBHOOK_PORT` and
  `GITHUB_WEBHOOK_PATH` are reachable through public HTTPS or a trusted
  TLS-terminating reverse proxy. The built-in listener does not terminate TLS.

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

## Runtime Architecture

```text
Discord
  ↓
UI / Commands
  ↓
Application Services
  ↓
Repositories
  ↓
SQLite
```

The main domains are Project, Tasks, Bugs, Meetings, Decisions, Standups,
Activity, Weekly Dashboard Publishing, GitHub, Forum Publishing, AI Sessions, AI Actions, and Health. Domain
services and SQLite remain authoritative for project records. External providers
are isolated behind adapters.

```text
AIService
  ↓
RetrievalPlanner
  ├── ProjectContextService
  └── Tavily Search (optional, read-only)
  ↓
PromptBuilder
  ↓
AIProviderRouter
  ├── Groq (recommended free primary)
  ├── Gemini (optional primary)
  └── OpenAI fallback (optional, disabled by default)
```

```text
AI proposal
  ↓
Explicit confirmation
  ↓
AIActionService
  ↓
Domain Service
  ↓
Mutation
```

The model only proposes an action. Ownership, expiry, permissions, current state,
and the explicit confirmation are validated by application services before an
existing domain service performs a mutation. `HealthService` reads only bounded
local process and SQLite state; it does not contact external providers.

## Roles and Permissions

- **Member:** can use read surfaces, manage permitted assigned/owned work, submit
  their own standup, and use their own private AI sessions.
- **Leader / CoLead:** can perform project-level management, GitHub sync, Forum
  setup, weekly dashboard setup/manual publishing, health diagnostics, and other privileged domain mutations.
- Authorization is enforced by application services. Discord buttons, command
  visibility, thread privacy, and ephemeral responses are not the sole security
  boundary.

## Bot Usage

The stable command surface is:

| Command | Access | Purpose |
| --- | --- | --- |
| `/dashboard` | All roles; management restricted | Project status and management panel |
| `/tasks` | All roles; mutations permission-aware | Task browsing and workflow |
| `/bugs` | All roles; mutations permission-aware | Bug browsing and workflow |
| `/meetings` | All roles; mutations permission-aware | Meeting lifecycle, participants, notes, and action tasks |
| `/decisions` | All roles; mutations permission-aware | Decision history, creation, and editing |
| `/standup` | All roles | Own daily submission plus permitted team/history views |
| `/github` | All roles; sync restricted to Leader/CoLead | Read-only cached GitHub context |
| `/weekly_report` | All roles | Current weekly progress report |
| `/weekly_dashboard` | Leader/CoLead only | Publish the current ISO-week dashboard snapshot to the configured public channel |
| `/ai` | All roles | Private grounded AI sessions and confirmed proposals |
| `/health` | Leader/CoLead only | Bounded operational diagnostics |
| `/setup forums` | Leader/CoLead only | Persist Discord Forum mappings |
| `/setup dashboard` | Leader/CoLead only | Configure the public dashboard channel, weekday, and local publish time |

### `/dashboard`

Opens a private interactive dashboard Embed with:

- Project overview with status-aware color, goal, phase, sprint, deadline, and Discord timestamp
- Visual task progress bar plus open/completed counts, bug health, and meeting snapshot
- Refresh button to reload the latest project data
- **Manage Dashboard** button (Leader/Co-Lead only through service-layer permissions) to update project management fields

### Weekly public dashboard

`/dashboard` remains a private realtime management surface. Public team visibility is handled separately:

- `/setup dashboard` stores the target text channel and weekly schedule in SQLite. Defaults are Monday at 09:00 in `Asia/Ho_Chi_Minh`.
- `/weekly_dashboard` lets a Leader/CoLead manually publish or refresh the current ISO-week snapshot. If the stored message still exists in the configured channel, CSE-HQ edits it in place; if it was deleted or the configured channel changed, CSE-HQ creates a replacement and updates the stored publication mapping.
- The long-running bot process checks the schedule once per minute and publishes the first due snapshot for the week.
- If the bot is offline at the scheduled time, it catches up later in the same ISO week.
- `dashboard_publications.week_key` remains unique so scheduled publishing stays idempotent; manual refresh updates the existing publication instead of creating uncontrolled duplicates.
- Public snapshots use the same polished status/progress presentation without management controls; project mutations remain behind the existing private command/service permission boundaries.

### CSE-HQ UI Design System v1

Discord does not expose arbitrary embed font-size controls, so CSE-HQ improves readability through information hierarchy rather than custom typography. Dashboard surfaces share one visual language: a branded surface title, short subtitle, compact three-column KPI cards where useful, spacious two-line list rows, semantic status colors only when status matters, and a consistent footer. Detail cards reuse the same shell so moving from a list into an individual Task, Bug, Meeting, Decision, GitHub item, or AI surface does not feel like switching products.

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
- **New Session** to create a private Discord thread-backed AI session named `session của <display name> #<number>`, numbered independently for each user
- **My Sessions** to list persisted sessions as clickable Discord thread mentions instead of raw thread IDs
- Deleted Discord AI threads are reconciled to `DELETED` session state and automatically disappear from **My Sessions**; a list-time check also cleans deletions missed while the bot was offline
- Natural thread conversation for authorized session owners only
- Bounded persisted conversation history controlled by `AI_MAX_HISTORY_MESSAGES`
- Native multimodal image understanding through Groq or Gemini for PNG, JPEG, and WEBP attachments in private AI threads
- Up to 3 images per message, 8 MB per image, and 12 MB total inline image bytes per request
- Image bytes are transient request data; SQLite stores only attachment filename/media-type metadata in conversation history

The assistant is intentionally bounded and permission-aware:

- CSE-HQ remains the source of truth for project state and permissions
- Project-specific answers are grounded only in retrieved records the actor may access
- If project evidence is missing, the assistant should say so explicitly
- Supported mutations produce a persisted preview with **Confirm** and **Cancel**; no mutation occurs when the preview is shown
- Confirmation ownership, expiry, session state, permissions, target state, and lifecycle transitions are checked again before execution
- Only the current user's standup may be submitted or updated; other-user and team-wide standup writes are rejected
- GitHub, repository, deletion, batch, autonomous, and multi-step actions remain unavailable
- AI Vision v1 is read-only: image-backed Task/Bug/Meeting/Decision/Standup mutations are rejected rather than inferred from pixels
- Multimodal requests stay on the selected vision-capable primary provider; the text-only OpenAI fallback is not used for image requests
- Prior AI replies are continuity only; fresh project retrieval wins on every request

### Direct @mention replies

Outside private AI sessions, members can mention the bot directly in a server
channel:

```text
@CSE HQ Assistant what is blocking my task?
@CSE HQ Assistant search the web for the latest Qwen update
```

Mention replies are stateless and read-only. They reuse the same actor permissions,
project grounding, personality policy, image validation, and source validation as
private AI sessions, but they never create AI Action proposals. If a public mention
asks CSE-HQ to mutate Tasks, Bugs, Meetings, Decisions, or Standups, the bot explains
that project changes must go through a private `/ai` session with Confirm/Cancel.
This prevents a casual public ping from becoming an execution boundary.

### Read-only Web Research v1

When `WEB_RESEARCH_ENABLED=true`, CSE-HQ can retrieve fresh public web evidence
through Tavily Search. Qwen/Groq remains the reasoning model: Tavily only supplies
bounded search results, each normalized into `WEB-###` context records with a
source URL. The model is instructed to treat webpage snippets as untrusted data,
cite matching `WEB-###` IDs, and never invent source IDs. CSE-HQ appends the
consulted source links to the Discord answer.

Web research activates when the user explicitly asks to search/research the web,
or for freshness-sensitive external questions that the project retrieval planner
does not recognize as internal project memory. Recognized questions such as
"what am I currently working on?" stay entirely inside CSE-HQ records instead of
wasting a web-search credit.

The integration is deliberately retrieval-only:

- Uses only Tavily's Search endpoint.
- Does not expose crawl, browser automation, form submission, account actions,
  GitHub writes, or arbitrary HTTP tools to the model.
- Search results cannot trigger project mutations.
- If live research is required but unavailable, CSE-HQ says it cannot verify the
  current web instead of presenting stale model knowledge as current fact.
- Result count, snippet size, timeout, and provider context remain bounded.

### Automatic member welcome

When `WELCOME_ENABLED=true`, CSE-HQ listens for new human guild members and sends
a branded welcome card. `WELCOME_CHANNEL_ID` can pin the destination; otherwise
the bot prefers the server System Messages channel and then the first text channel
where it has View Channel and Send Messages permissions. Bot accounts are ignored.

The welcome event only sends presentation content. It creates no project record,
AI session, task, or external action.

### Language Following

CSE-HQ follows the language of the current user message, independent of the selected
AI provider. Vietnamese or Vietnamese-English chat is answered primarily in natural
Vietnamese while technical terms may remain in English. The current message wins
over older conversation history when choosing response language, and ambiguous
language defaults to Vietnamese.

If a provider unexpectedly drifts into a clearly Chinese response when the user did
not write in or request Chinese, CSE-HQ retries the generation once with an explicit
language correction. Small Chinese quotes or translation questions do not trigger
this safeguard.

### Personality Layer v1

CSE-HQ owns its personality independently of Groq, Gemini, or any future model.
The model is instructed to behave like an experienced engineering mentor and
trusted teammate rather than a manager:

- Diagnose situations without judging teammates.
- Offer options, tradeoffs, scope cuts, and the smallest useful next step instead
  of using pressure or manufactured urgency.
- Never use deadlines, rankings, workload, or another member's progress to shame
  or guilt a user.
- Challenge weak assumptions and evaluate ideas before praising them.
- Use light situational humor about bugs, CI, tools, and project weirdness, never
  make a teammate the target of the joke.
- Suppress humor for security incidents and serious failures; debugging stays calm
  and surgical.
- Protect sustainable pace during planning and acknowledge concrete progress
  naturally when something is actually shipped or fixed.

A deterministic response-posture classifier adjusts presentation for normal Q&A,
brainstorming, debugging, incidents, planning, and celebration. This classifier
changes style only; it does not change retrieval, permissions, provider routing,
or AI Action authorization.

**CSE-HQ remembers. The model reasons. The user decides.**

### AI Vision v1

Private AI session messages may include Discord image attachments. CSE-HQ validates
and reads supported images transiently, then passes them to the selected vision-capable
primary provider as multimodal input. Supported formats are PNG, JPEG, and WEBP.

Vision input is deliberately bounded:

- Maximum 3 images per message
- Maximum 8 MB per image
- Maximum 12 MB total image bytes per request, leaving headroom below provider inline-request limits
- Attachment bytes are validated against PNG/JPEG/WEBP file signatures
- Downloads use a bounded timeout and are never written to the project filesystem
- Raw image bytes are never persisted in SQLite
- Image text and visual content are treated as untrusted user-provided data, not instructions
- Image-backed project mutations are not supported in v1; confirmed AI Actions remain text-driven and permission-checked

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

### `/health`

Shows a private, bounded operational snapshot for Leaders and Co-Leads. It
reports the Discord connection, SQLite connectivity, Groq, Gemini, OpenAI fallback,
read-only web research, welcome configuration, GitHub cache sync, webhook listener
and latest delivery, and the three Forum mappings. It also includes the installed
application version.

`/health` uses local process and SQLite state only. It does not make live calls
to Discord, Groq, Gemini, OpenAI, Tavily, or GitHub, and it never displays tokens, secrets,
model names, database paths, webhook payloads, repository identifiers, or Discord
channel IDs.

Health states mean:

- `HEALTHY`: the component is configured and its latest bounded check succeeded.
- `DEGRADED`: the component is usable with reduced assurance, stale state, or a
  working fallback after a primary failure.
- `DISABLED`: an optional integration is intentionally disabled or not
  configured; this does not make the bot unhealthy.
- `FAILED`: the component check or configured integration failed. Core Discord
  or database failure makes overall health `FAILED`; an isolated optional
  integration failure makes it `DEGRADED`.

### `/setup forums`

Allows a Leader or Co-Lead to select existing Discord Forum channels for Bugs,
Pull Requests, and Releases. CSE-HQ validates that the bot can view the channel,
create public threads, send starter messages, and reply inside threads before it
saves all three mappings. The optional `test_publish` switch creates a harmless
test post in each configured Forum. Forum channel IDs are stored in SQLite, never
in environment variables.

The underlying service layer supports project, task, bug, meeting, decision,
standup, activity, reporting, grounded Q&A, GitHub cache, Forum publishing, and
health operations. Role checks are enforced in the service layer rather than
relying only on Discord channel visibility.

## Configuring Groq AI (recommended free production path)

Groq is the recommended production provider when you want to keep CSE-HQ inside
a free API allowance. The integration uses Groq's OpenAI-compatible Responses API
through the existing `openai` Python dependency, so no extra Groq SDK is required.

Configure the server environment:

```dotenv
AI_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key
GROQ_MODEL=qwen/qwen3.8-27b
AI_MAX_CONTEXT_ITEMS=6
AI_MAX_HISTORY_MESSAGES=4
AI_REQUEST_TIMEOUT=30
AI_PRIMARY_RETRIES=1
AI_FALLBACK_ENABLED=false
```

`qwen/qwen3.8-27b` supports text and image inputs. CSE-HQ keeps image requests
bounded to three attachments so they remain within the provider's documented
vision request shape. To avoid accidental paid usage, the recommended deployment
keeps `AI_FALLBACK_ENABLED=false`.

CSE-HQ retries a normalized rate-limit or temporary provider-unavailable error at
most `AI_PRIMARY_RETRIES` times. It does not automatically retry a timeout, so a
slow provider cannot silently multiply the user's wait by another full timeout
window.

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
AI_REQUEST_TIMEOUT=30
AI_PRIMARY_RETRIES=1
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

The Q&A service builds bounded, permission-aware context from CSE-HQ records and, when enabled, the normalized GitHub cache. The same rendered prompt package is sent to the selected provider. It instructs the model to treat records as untrusted data, state when evidence is insufficient, preserve source IDs, and never claim to modify project or GitHub records.

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
AI_REQUEST_TIMEOUT=30
AI_PRIMARY_RETRIES=1
```

The primary provider retries normalized rate-limit or provider-unavailable failures up to `AI_PRIMARY_RETRIES`; timeouts are not retried. Optional fallback is attempted only after an eligible rate-limit, timeout, temporary connection, or provider-unavailable failure. Configuration errors, malformed provider responses, permission/session failures, retrieval failures, and other application errors do not trigger fallback. The same system instructions, bounded context, conversation history, and question are reused; retrieval and citation validation run only once.

Each provider attempt is limited by `AI_REQUEST_TIMEOUT`, provider SDK retries are disabled where CSE-HQ owns retry behavior, and at most one fallback call is made. Therefore the worst-case provider time may approach two timeout windows. Missing OpenAI credentials do not stop successful primary-provider startup or requests, but an actual fallback attempt returns a controlled configuration error. Failover improves resilience but does not guarantee availability.

## AI Actions v2.1

AI Actions v2.1 extends the existing bounded mutation path across CSE-HQ's internal project-management domains. The language model may interpret a request into a structured proposal, but it cannot execute service methods. CSE-HQ validates the proposal, persists it, displays its exact effect, and requires the proposal owner to press **Confirm** before application code invokes the existing domain service.

Supported actions are:

- Tasks: create, assign, start, block, complete, and reopen
- Bugs: assign, transition to a supported status, resolve, and reopen
- Meetings: create, start, complete, cancel, add a participant, and add a note
- Decisions: create and edit
- Standups: submit or update the requesting user's standup for the current day

Every proposal is limited to one action, expires after `AI_ACTION_EXPIRATION_SECONDS`, and is bound to its private AI session and requesting actor. Confirmation does not call Groq, Gemini, or OpenAI again. Instead, CSE-HQ atomically claims the pending proposal, re-fetches the target, rechecks session state, ownership, permissions, and lifecycle state, then calls the existing domain service. Repeated or simultaneous confirmation cannot execute the same proposal twice. Successful mutations use the existing domain activity logging; cancelled, expired, stale, and failed proposals do not report false success.

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

Run `/github`, then use **Sync** as a Leader or Co-Lead. Normal browsing and AI retrieval use SQLite-cached normalized records; they do not call GitHub on every interaction. A failed refresh records stale status while preserving the last successful snapshot. Never commit `GITHUB_TOKEN` or log authorization headers.

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

## Operational Logging and Troubleshooting

Operational log records use consistent bounded fields where applicable, including
`component`, `operation`, `entity_id`, `session_id`, `delivery_id`, `result`, and
`error_category`. AI provider/fallback outcomes, GitHub sync failures, webhook
processing, Forum publication failures, and diagnostics failures are recorded
without logging prompts, private conversation content, credentials, raw webhook
payloads, or authorization headers.

For first-line troubleshooting:

1. Run `/health` as a Leader or Co-Lead and identify the first degraded or failed
   component.
2. Check service logs with `journalctl -u cse-hq-bot.service -f` and filter on the
   reported component and operation fields.
3. For GitHub, compare the last sync and latest webhook delivery states; cache
   sync and inbound webhooks are independent.
4. For AI degradation, verify that the primary is configured and whether the
   fallback is configured; `/health` does not test providers by sending a prompt.
5. For Forum publishing, rerun `/setup forums` with `test_publish` after fixing
   Discord channel permissions.

## Known Limitations

- One GitHub repository is supported, read-only. CSE-HQ performs no GitHub writes.
- SQLite is the only database and the supported deployment is a single bot
  process with persistent local storage.
- Tavily web research is optional and bounded by the configured/free account quota;
  CSE-HQ does not silently replace unavailable live research with a claim of freshness.
- AI answers are bounded by retrieved records and provider availability; failover
  is one optional OpenAI attempt after eligible selected-primary failures, not a general
  provider pool.
- Discord Views attached to pending AI proposals are not restored after restart.
  Persisted proposals never execute automatically; create a fresh proposal if the
  original confirmation buttons are gone.
- `/health` reports bounded local/configured/latest-result state and deliberately
  avoids expensive live provider probes.
- Forum publishing requires pre-existing Discord Forum channels configured with
  `/setup forums`; missing Forum configuration does not disable core project work.
- The webhook listener requires an external HTTPS endpoint or reverse proxy in
  production and does not provide deployment, paging, or auto-restart features.

## Tests

Install the development dependencies, then run the same validation gates used by
CI:

```bash
python -m pytest
ruff check cse_hq_bot tests tools
python -m compileall -q cse_hq_bot tests tools
bandit -q -r cse_hq_bot tools
python -m pip check
pip-audit
python tools/check_secret_signatures.py
```

The `CI` workflow runs these gates for every pull request and every push to
`main`, using Python 3.12 and no live Discord, GitHub, Groq, Gemini, OpenAI, or webhook
credentials. Superseded runs for the same pull request or branch are cancelled.
The dependency cache is optional and contains no environment files, credentials,
databases, or runtime state.

The separate `CodeQL` workflow analyzes Python on pull requests, pushes to
`main`, and a weekly schedule. It has read-only repository access plus only the
`security-events: write` permission required to upload analysis results. CodeQL
status must be taken from the GitHub check; local validation is not reported as a
CodeQL pass.

Latest local repository validation (2026-09-21): 247 offline tests passed. Ruff,
Python compilation, Bandit, `pip check`, `pip-audit`, and tracked-file plus
Git-history secret-signature scanning also passed. The previous 18 Ruff findings
were fixed. The five B608 findings were removed by replacing dynamic SQL with
fixed, allowlisted, parameterized statements. The intentional `0.0.0.0` listener
default retains its narrowly documented Bandit B104 suppression because it is
deployment-controlled and required by the webhook configuration contract.

### Recommended main branch protection

Require pull requests before merge and require these checks:

- `Tests / Quality`
- `CodeQL`

Also require branches to be up to date before merging. Branch protection remains
an administrator setting and is not modified by these workflows.

## Project Notes

- Application-level permissions are enforced in service-layer helpers, not only through Discord visibility.
- SQLite schema initialization is safe to run repeatedly.
- Meaningful task, bug, meeting, decision, and standup mutations append immutable activity records after the primary write succeeds.
- `ProjectContextService` provides permission-aware, structured project reads for reporting and grounded retrieval without bypassing domain services.
- The fake AI provider is the recommended default for development and automated tests.
- Keep `.env`, Discord tokens, Groq/Gemini/OpenAI API keys, and production database backups out of version control.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
