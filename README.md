# CSE-HQ Bot MVP

CSE-HQ is a Discord bot MVP for project coordination. It provides:

- Role-aware project, task, and bug operations for Leaders, Co-Leads, and Members
- SQLite-backed persistence for project settings, tasks, bugs, meetings, decisions, and standups
- Weekly progress reporting
- Grounded project Q&A through a pluggable AI provider
- A fake AI provider for local development and an optional Google Gemini provider

## Requirements

- Python 3.11 or newer
- A Discord application and bot token
- A server or local machine that can run a long-lived Python process
- Optional: a Google Gemini API key for Gemini-powered project Q&A

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

5. Edit `.env` and set at least `DISCORD_TOKEN`. Keep `AI_PROVIDER=fake` unless Gemini is configured.

## Environment Variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `DISCORD_TOKEN` | Yes | — | Token for the Discord bot. Never commit this value. |
| `DISCORD_GUILD_ID` | No | — | Optional Discord server ID for environment-specific configuration. |
| `DATABASE_PATH` | No | `./cse_hq.db` | Path to the SQLite database file. |
| `LOG_LEVEL` | No | `INFO` | Python logging level, such as `DEBUG`, `INFO`, or `WARNING`. |
| `AI_PROVIDER` | No | `fake` | Set to `fake` for local development or `gemini` to use Gemini. |
| `GEMINI_API_KEY` | Required when `AI_PROVIDER=gemini` | — | Google Gemini API key. |
| `GEMINI_MODEL` | No | `gemini-1.5-flash` | Gemini model name passed to the provider. |

Example development configuration:

```dotenv
DISCORD_TOKEN=your_discord_bot_token
DISCORD_GUILD_ID=your_discord_server_id
DATABASE_PATH=./cse_hq.db
LOG_LEVEL=INFO
AI_PROVIDER=fake
GEMINI_API_KEY=
GEMINI_MODEL=gemini-1.5-flash
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

Set `DISCORD_TOKEN` and the other values in `/etc/cse-hq-bot.env`. For Gemini, use `AI_PROVIDER=gemini` and provide `GEMINI_API_KEY`.

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

### `/weekly_report`

Displays the current weekly progress report. The response is private to the user who invoked the command.

The underlying service layer also supports project, task, bug, collaboration, reporting, and grounded Q&A operations. Role checks are enforced in the service layer rather than relying only on Discord channel visibility. Additional Discord commands should be added as the command surface is expanded.

## Configuring Google Gemini AI

Gemini is optional. The bot uses the fake provider by default, so the application can run without an external AI service.

### 1. Create a Gemini API key

Create an API key through Google AI Studio or your approved Google Cloud configuration. Keep the key private and do not commit it to Git.

### 2. Configure the environment

Update `.env` on a local machine or `/etc/cse-hq-bot.env` on a server:

```dotenv
AI_PROVIDER=gemini
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-1.5-flash
```

`GEMINI_API_KEY` is required when `AI_PROVIDER=gemini`. The configured model name is passed directly to the `google-generativeai` client.

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

The Q&A service builds a context from the project settings and up to the first 10 persisted tasks and bugs. The prompt instructs Gemini to use only that context, state when the context is insufficient, and never claim to modify project records. AI Q&A is read-only; it does not create or update tasks or bugs.

If Gemini initialization fails, verify all of the following:

- `AI_PROVIDER` is exactly `gemini`.
- `GEMINI_API_KEY` is present and valid.
- `GEMINI_MODEL` is a model available to the configured Gemini API account.
- The installed `google-generativeai` dependency is present in the active virtual environment.
- The server can make outbound HTTPS requests.

## Tests

Run the test suite with:

```bash
pytest
```

## Project Notes

- Application-level permissions are enforced in service-layer helpers, not only through Discord visibility.
- SQLite schema initialization is safe to run repeatedly.
- The fake AI provider is the recommended default for development and automated tests.
- Keep `.env`, Discord tokens, Gemini API keys, and production database backups out of version control.

## License

This project is licensed under the MIT License. See `LICENSE` for details.
