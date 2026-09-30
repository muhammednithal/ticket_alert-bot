# Ticket Alert Bot 🎟️

An automated Telegram bot that monitors movie ticket availability for targeted show links, alerting subscribed users instantly as soon as ticket bookings open. Supports monitoring **multiple URLs** simultaneously.

---

## 🚀 Features

- **Multi-URL Monitoring:** Watch multiple show/venue URLs at the same time. Each URL is tracked independently.
- **Dynamic Admin Control:** Admin can add/remove URLs, pause/resume monitoring, change check intervals, and broadcast messages — all via Telegram.
- **Subscriber Self-Service:** Users can subscribe, unsubscribe, check status, and view all watched URLs.
- **Instant Broadcast Alerts:** Sends notification alerts with the specific booking link that opened to all subscribers.
- **PostgreSQL Storage:** Subscribers, watched URLs, and settings are stored in PostgreSQL — no data loss on deploys or restarts.
- **Anti-Blocking Safety:** Incorporates request headers and randomized sleep jitter between check intervals.

---

## 📋 Prerequisites

- **Python:** 3.12 or higher
- **`uv` Package Manager:** Fast Python package and project manager. [Install uv](https://github.com/astral-sh/uv)
- **PostgreSQL Database:** A running PostgreSQL instance (local, Supabase, Neon, Railway, etc.)
- **Telegram Bot Token & Admin Chat ID:** Obtained from [@BotFather](https://t.me/BotFather) and [@userinfobot](https://t.me/userinfobot) on Telegram.

---

## ⚙️ Environment Variables

Create a `.env` file in the root directory (or copy from `.env.example`):

```bash
cp .env.example .env
```

| Variable                 | Description                                                            | Default |
| :----------------------- | :--------------------------------------------------------------------- | :------ |
| `TELEGRAM_BOT_TOKEN`     | **Required.** Your Telegram bot API token from BotFather               | `""`    |
| `ADMIN_CHAT_ID`          | **Required.** Telegram Chat ID of the admin who can run admin commands | `""`    |
| `DATABASE_URL`           | **Required.** PostgreSQL connection string                             | `""`    |
| `CHECK_INTERVAL_SECONDS` | Default time in seconds between check cycles                           | `60`    |

---

## 🗄️ Database

The bot auto-creates three tables on first run:

| Table         | Purpose                                                                    |
| :------------ | :------------------------------------------------------------------------- |
| `subscribers` | Telegram chat IDs of subscribed users                                      |
| `watch_urls`  | URLs being monitored — each with an ID, optional label, and alerted status |
| `settings`    | Key-value store for dynamic settings (`check_interval`, `paused`)          |

No manual table creation needed — just provide a valid `DATABASE_URL`.

---

## 🤖 Telegram Bot Commands

### Subscriber Commands (everyone)

| Command   | Description                                                                  |
| :-------- | :--------------------------------------------------------------------------- |
| `/start`  | Subscribe to booking alerts                                                  |
| `/stop`   | Unsubscribe from alerts                                                      |
| `/status` | View bot status — active/total URLs, subscriber count, interval, pause state |
| `/urls`   | Show all currently watched URLs                                              |
| `/help`   | Show all available commands (admins see admin commands too)                  |

### Admin Commands (ADMIN_CHAT_ID only)

| Command                  | Description                                               |
| :----------------------- | :-------------------------------------------------------- |
| `/addurl <url>`          | Add a URL to the watch list                               |
| `/addurl <label> <url>`  | Add a URL with a label (e.g., `AAA-Oct3`)                 |
| `/removeurl <id>`        | Remove a URL by its `#id`                                 |
| `/listurls`              | List all URLs with their alert status                     |
| `/broadcast <message>`   | Send a custom message to all subscribers                  |
| `/subs`                  | View the subscriber count and list of chat IDs            |
| `/pause`                 | Pause the monitoring loop                                 |
| `/resume`                | Resume the monitoring loop                                |
| `/setinterval <seconds>` | Change the check interval dynamically (minimum 10s)       |
| `/check`                 | Run a one-off check on all active URLs and report results |
| `/clearalerts`           | Reset all alerted URLs so they get rechecked              |
| `/admin`                 | Show admin-only command help                              |

---

## 🛠️ Setup & Running

### 1. Install Dependencies

```bash
uv sync
```

### 2. Configure Environment

Update your `.env` file:

```env
TELEGRAM_BOT_TOKEN=your_bot_token_here
ADMIN_CHAT_ID=123456789
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

### 3. Run the Bot

```bash
uv run bmsalret.py
```

### 4. Add URLs to Watch

In Telegram, send:

```
/addurl https://TEST.com/cinemas/hyderabad/allu-cinemas-kokapet/buytickets/ALUC/20261003
```

Or with a label:

```
/addurl AAA-Oct3 https://test.com/.../ALUC/20261003
```

---

## 📁 Project Structure

```
script/
├── bmsalret.py         # Main bot application code
├── pyproject.toml      # Project configuration and dependencies
├── uv.lock             # Lockfile for reproducible builds
├── .env                # Environment variables (ignored by git)
├── .env.example        # Example environment template
├── .gitignore          # Git ignore rules
└── README.md           # Documentation
```
