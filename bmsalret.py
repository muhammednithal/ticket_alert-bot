import os
import time
import random
import threading
import datetime as dt
from zoneinfo import ZoneInfo

import requests
import psycopg2
from dotenv import load_dotenv

# Load variables from .env if present
load_dotenv()

# ---- Settings (environment variables) ----
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID", "").strip()
INTERVAL = int(os.environ.get("CHECK_INTERVAL_SECONDS", "1800"))
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

API = f"https://api.telegram.org/bot{BOT_TOKEN}"
IST = ZoneInfo("Asia/Kolkata")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-IN,en;q=0.9",
}

lock = threading.Lock()


# ===================== PostgreSQL Storage =====================
def get_db():
    """Get a new database connection."""
    return psycopg2.connect(DATABASE_URL)


def init_db():
    """Create tables if they don't exist."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS subscribers (
                    chat_id TEXT PRIMARY KEY,
                    subscribed_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS watch_urls (
                    id SERIAL PRIMARY KEY,
                    url TEXT UNIQUE NOT NULL,
                    label TEXT DEFAULT '',
                    alerted BOOLEAN DEFAULT FALSE,
                    added_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
        conn.commit()
    print("Database tables initialized.")


# ---------- Subscriber DB operations ----------
def db_add_subscriber(chat_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO subscribers (chat_id) VALUES (%s) ON CONFLICT (chat_id) DO NOTHING",
                (str(chat_id),),
            )
        conn.commit()


def db_remove_subscriber(chat_id):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM subscribers WHERE chat_id = %s", (str(chat_id),))
        conn.commit()


def db_get_subscribers():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT chat_id FROM subscribers ORDER BY chat_id")
            return [row[0] for row in cur.fetchall()]


def db_subscriber_count():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM subscribers")
            return cur.fetchone()[0]


# ---------- Watch URLs DB operations ----------
def db_add_url(url, label=""):
    """Add a URL to the watch list. Returns (success, id_or_message)."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO watch_urls (url, label) VALUES (%s, %s) RETURNING id",
                    (url.strip(), label.strip()),
                )
                row = cur.fetchone()
            conn.commit()
            return True, row[0]
    except psycopg2.errors.UniqueViolation:
        return False, "URL already exists in watch list"
    except Exception as e:
        return False, str(e)


def db_remove_url(url_id):
    """Remove a URL by its ID. Returns True if a row was deleted."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM watch_urls WHERE id = %s", (url_id,))
            deleted = cur.rowcount > 0
        conn.commit()
    return deleted


def db_get_active_urls():
    """Get all non-alerted URLs to check."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, url, label FROM watch_urls WHERE alerted = FALSE ORDER BY id"
            )
            return cur.fetchall()


def db_get_all_urls():
    """Get all URLs (including alerted ones)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, url, label, alerted FROM watch_urls ORDER BY id")
            return cur.fetchall()


def db_mark_alerted(url_id):
    """Mark a URL as alerted (bookings opened)."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE watch_urls SET alerted = TRUE WHERE id = %s", (url_id,))
        conn.commit()


def db_reset_alerts():
    """Reset all alerted flags so URLs are rechecked."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE watch_urls SET alerted = FALSE")
            count = cur.rowcount
        conn.commit()
    return count


def db_url_count():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM watch_urls")
            return cur.fetchone()[0]


# ---------- Settings DB operations ----------
def db_get_setting(key, default=None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT value FROM settings WHERE key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else default


def db_set_setting(key, value):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                (key, str(value)),
            )
        conn.commit()


# ===================== Runtime State =====================
check_interval = INTERVAL
paused = False


def load_state():
    """Load dynamic settings from DB into globals."""
    global check_interval, paused
    check_interval = int(db_get_setting("check_interval", str(INTERVAL)))
    paused = db_get_setting("paused", "false").lower() == "true"


# ===================== Telegram Helpers =====================
def send(chat_id, msg):
    if not BOT_TOKEN or not chat_id:
        return
    try:
        r = requests.post(
            f"{API}/sendMessage", data={"chat_id": chat_id, "text": msg}, timeout=15
        )
        # user blocked the bot -> drop them
        if r.status_code == 403:
            db_remove_subscriber(chat_id)
    except Exception as e:
        print("Telegram error:", e)


def broadcast(msg):
    targets = db_get_subscribers()
    for cid in targets:
        send(cid, msg)
        time.sleep(0.05)  # stay under Telegram rate limits


def is_admin(chat_id):
    return bool(ADMIN_CHAT_ID and str(chat_id) == str(ADMIN_CHAT_ID))


def _format_url_list(urls, include_status=False):
    """Format a list of URL rows for display."""
    if not urls:
        return "No URLs in watch list."
    lines = []
    for row in urls:
        if include_status:
            uid, url, label, alerted = row
            status = "✅ Alerted" if alerted else "👀 Watching"
            tag = f" ({label})" if label else ""
            lines.append(f"  #{uid}{tag} [{status}]\n  {url}")
        else:
            uid, url, label = row
            tag = f" ({label})" if label else ""
            lines.append(f"  #{uid}{tag}\n  {url}")
    return "\n\n".join(lines)


def register_commands():
    """Register bot commands with Telegram so users see them in the menu."""
    if not BOT_TOKEN:
        return

    user_commands = [
        {"command": "start", "description": "Subscribe to booking alerts"},
        {"command": "stop", "description": "Unsubscribe from alerts"},
        {"command": "status", "description": "View bot status"},
        {"command": "urls", "description": "Show all watched URLs"},
        {"command": "help", "description": "Show available commands"},
    ]
    try:
        requests.post(
            f"{API}/setMyCommands",
            json={"commands": user_commands},
            timeout=15,
        )
        print("Registered default user commands with Telegram.")
    except Exception as e:
        print("Failed to register user commands:", e)

    if ADMIN_CHAT_ID:
        admin_commands = user_commands + [
            {"command": "addurl", "description": "Add a URL to monitor"},
            {"command": "removeurl", "description": "Remove a URL from watch list"},
            {"command": "listurls", "description": "List all URLs with status"},
            {
                "command": "broadcast",
                "description": "Send a message to all subscribers",
            },
            {"command": "subs", "description": "View subscriber list"},
            {"command": "pause", "description": "Pause monitoring checks"},
            {"command": "resume", "description": "Resume monitoring checks"},
            {"command": "setinterval", "description": "Change the check interval"},
            {"command": "check", "description": "Run one-off check on all URLs"},
            {"command": "clearalerts", "description": "Reset all alerted URLs"},
            {"command": "admin", "description": "Show admin command help"},
        ]
        try:
            requests.post(
                f"{API}/setMyCommands",
                json={
                    "commands": admin_commands,
                    "scope": {"type": "chat", "chat_id": int(ADMIN_CHAT_ID)},
                },
                timeout=15,
            )
            print("Registered admin commands for chat", ADMIN_CHAT_ID)
        except Exception as e:
            print("Failed to register admin commands:", e)


# ===================== Help Texts =====================
USER_HELP = (
    "📖 Available Commands:\n\n"
    "/start — Subscribe to booking alerts\n"
    "/stop — Unsubscribe from alerts\n"
    "/status — View bot status\n"
    "/urls — Show all watched URLs\n"
    "/help — Show this help message"
)

ADMIN_HELP = (
    "🔧 Admin Commands:\n\n"
    "/addurl <url> — Add a URL to monitor\n"
    "/addurl <label> <url> — Add with a label\n"
    "/removeurl <id> — Remove a URL by its #id\n"
    "/listurls — List all URLs with alert status\n"
    "/broadcast <msg> — Send a message to all subscribers\n"
    "/subs — View subscriber count and list\n"
    "/pause — Pause monitoring checks\n"
    "/resume — Resume monitoring checks\n"
    "/setinterval <seconds> — Change check interval\n"
    "/check — Run a one-off check on all active URLs\n"
    "/clearalerts — Reset all alerted URLs for recheck\n"
    "/admin — Show this admin help"
)


# ===================== Command Listener =====================
def listen_for_commands():
    """Long-polls Telegram for user and admin commands."""
    global check_interval, paused
    if not BOT_TOKEN:
        print("Warning: TELEGRAM_BOT_TOKEN is not set in environment or .env!")
        return

    offset = None
    while True:
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"timeout": 30, "offset": offset},
                timeout=40,
            )
            for u in r.json().get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                raw_text = (m.get("text") or "").strip()
                text = raw_text.lower()
                cid = str(m.get("chat", {}).get("id", ""))
                if not cid:
                    continue

                # ---- Subscriber commands ----
                if text.startswith("/start"):
                    db_add_subscriber(cid)
                    active = db_get_active_urls()
                    if active:
                        url_list = "\n".join(
                            f"  • {row[2] or row[1]}" for row in active
                        )
                        watch_msg = (
                            f"🔗 Currently watching {len(active)} URL(s):\n{url_list}"
                        )
                    else:
                        watch_msg = "🔗 No URLs being watched yet."
                    send(
                        cid,
                        f"✅ You're subscribed! I'll message you the moment "
                        f"bookings open.\n\n{watch_msg}\n\n"
                        "Send /help to see all commands.",
                    )

                elif text.startswith("/stop"):
                    db_remove_subscriber(cid)
                    send(cid, "👋 Unsubscribed. Send /start to subscribe again.")

                elif text.startswith("/help"):
                    help_msg = USER_HELP
                    if is_admin(cid):
                        help_msg += "\n\n" + ADMIN_HELP
                    send(cid, help_msg)

                elif text.startswith("/urls"):
                    active = db_get_active_urls()
                    if not active:
                        send(cid, "📋 No URLs being watched right now.")
                    else:
                        formatted = _format_url_list(active)
                        send(cid, f"👀 Watching {len(active)} URL(s):\n\n{formatted}")

                elif text.startswith("/status"):
                    with lock:
                        curr_interval = check_interval
                        is_paused = paused
                    sub_count = db_subscriber_count()
                    url_count = db_url_count()
                    active_count = len(db_get_active_urls())
                    state = "⏸ Paused" if is_paused else "▶️ Running"
                    status_msg = (
                        f"📊 Bot Status:\n"
                        f"• State: {state}\n"
                        f"• URLs Watched: {active_count} active / {url_count} total\n"
                        f"• Subscribers: {sub_count}\n"
                        f"• Check Interval: {curr_interval}s"
                    )
                    send(cid, status_msg)

                # ---- Admin commands ----
                elif text.startswith("/addurl"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue

                    parts = raw_text.split(maxsplit=2)
                    # /addurl <url>  OR  /addurl <label> <url>
                    if len(parts) == 2:
                        url = parts[1].strip()
                        label = ""
                    elif len(parts) == 3 and parts[2].strip().startswith("http"):
                        label = parts[1].strip()
                        url = parts[2].strip()
                    elif len(parts) == 3 and parts[1].strip().startswith("http"):
                        url = parts[1].strip()
                        label = ""
                    else:
                        send(
                            cid,
                            "⚠️ Usage:\n"
                            "/addurl <url>\n"
                            "/addurl <label> <url>\n\n"
                            "Example:\n/addurl AAA-Oct3 https://in.bookmyshow.com/.../20261003",
                        )
                        continue

                    if not url.startswith("http"):
                        send(cid, "⚠️ URL must start with http:// or https://")
                        continue

                    ok, result = db_add_url(url, label)
                    if ok:
                        tag = f" ({label})" if label else ""
                        send(cid, f"✅ Added URL #{result}{tag}:\n{url}")
                        broadcast(f"📢 New URL added to watch list{tag}:\n{url}")
                    else:
                        send(cid, f"⚠️ Could not add URL: {result}")

                elif text.startswith("/removeurl"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    parts = raw_text.split(maxsplit=1)
                    if len(parts) < 2:
                        send(cid, "⚠️ Usage: /removeurl <id>\nExample: /removeurl 3")
                        continue
                    id_str = parts[1].strip().lstrip("#")
                    if not id_str.isdigit():
                        send(cid, "⚠️ ID must be a number. Use /listurls to see IDs.")
                        continue
                    if db_remove_url(int(id_str)):
                        send(cid, f"✅ Removed URL #{id_str} from watch list.")
                    else:
                        send(cid, f"⚠️ No URL found with ID #{id_str}.")

                elif text.startswith("/listurls"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    all_urls = db_get_all_urls()
                    if not all_urls:
                        send(cid, "📋 No URLs in watch list.")
                    else:
                        formatted = _format_url_list(all_urls, include_status=True)
                        send(cid, f"📋 All URLs ({len(all_urls)}):\n\n{formatted}")

                elif text.startswith("/broadcast"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    parts = raw_text.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        send(cid, "⚠️ Usage: /broadcast <message>")
                        continue
                    msg_to_send = parts[1].strip()
                    sub_count = db_subscriber_count()
                    broadcast(f"📢 {msg_to_send}")
                    send(cid, f"✅ Broadcast sent to {sub_count} subscriber(s).")

                elif text.startswith("/subs"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    sub_list = db_get_subscribers()
                    if not sub_list:
                        send(cid, "📋 No subscribers yet.")
                    else:
                        ids = "\n".join(f"  • {s}" for s in sub_list)
                        send(cid, f"📋 Subscribers ({len(sub_list)}):\n{ids}")

                elif text.startswith("/pause"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    with lock:
                        paused = True
                    db_set_setting("paused", "true")
                    send(cid, "⏸ Monitoring paused. Send /resume to restart checks.")

                elif text.startswith("/resume"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    with lock:
                        paused = False
                    db_set_setting("paused", "false")
                    send(cid, "▶️ Monitoring resumed.")

                elif text.startswith("/setinterval"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    parts = raw_text.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip().isdigit():
                        send(
                            cid,
                            "⚠️ Usage: /setinterval <seconds>\nExample: /setinterval 30",
                        )
                        continue
                    new_interval = max(10, int(parts[1].strip()))
                    with lock:
                        check_interval = new_interval
                    db_set_setting("check_interval", str(new_interval))
                    send(cid, f"✅ Check interval updated to {check_interval}s.")

                elif text.startswith("/check"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    active = db_get_active_urls()
                    if not active:
                        send(cid, "⚠️ No active URLs to check. Use /addurl to add one.")
                        continue
                    send(cid, f"🔍 Running one-off check on {len(active)} URL(s)...")
                    results = []
                    for uid, url, label in active:
                        opened, status = is_open(url)
                        tag = f" ({label})" if label else ""
                        icon = "🟢" if opened else "🔴"
                        results.append(f"{icon} #{uid}{tag}: {status}")
                        if opened:
                            db_mark_alerted(uid)
                            broadcast(f"🎉 Bookings are OPEN!{tag}\n{url}")
                    send(cid, "Check results:\n" + "\n".join(results))

                elif text.startswith("/clearalerts"):
                    if not is_admin(cid):
                        send(cid, "⛔ Unauthorized.")
                        continue
                    count = db_reset_alerts()
                    send(cid, f"🔄 Reset {count} URL(s). All will be rechecked.")

                elif text.startswith("/admin"):
                    if is_admin(cid):
                        send(cid, ADMIN_HELP)
                    else:
                        send(cid, "⛔ Unauthorized.")

        except Exception as e:
            print("Polling error:", e)
            time.sleep(5)


# ===================== Availability Check =====================
def is_open(url):
    """BMS redirects to today's date when the requested date isn't open yet."""
    if not url:
        return False, "no URL set"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
        if r.status_code in (403, 429):
            return False, f"blocked ({r.status_code})"
        if r.status_code != 200:
            return False, f"http {r.status_code}"

        target_clean = url.rstrip("/").split("?")[0]
        final_clean = r.url.rstrip("/").split("?")[0]
        return target_clean == final_clean or target_clean in final_clean, "ok"
    except Exception as e:
        return False, f"error ({e})"


def check_loop():
    blocked_notified = False
    while True:
        with lock:
            is_paused = paused
            curr_interval = check_interval

        if is_paused:
            time.sleep(curr_interval)
            continue

        active_urls = db_get_active_urls()
        for uid, url, label in active_urls:
            try:
                opened, status = is_open(url)
                tag = f" ({label})" if label else ""
                now_str = dt.datetime.now(IST).strftime("%H:%M:%S")
                print(f"[{now_str}] #{uid}{tag} | {url} | Open: {opened} | {status}")

                if opened:
                    db_mark_alerted(uid)
                    broadcast(f"🎉 Bookings are OPEN!{tag}\n{url}")
                elif (
                    status.startswith("blocked")
                    and not blocked_notified
                    and ADMIN_CHAT_ID
                ):
                    send(
                        ADMIN_CHAT_ID,
                        f"⚠️ Checks are being blocked ({status}).",
                    )
                    blocked_notified = True
            except Exception as e:
                print(f"Check failed for #{uid}:", e)

            # Small delay between URL checks to avoid rate limiting
            time.sleep(2)

        time.sleep(curr_interval + random.randint(0, 10))


# ===================== Entry Point =====================
if __name__ == "__main__":
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. Add it to your .env file.")
        exit(1)

    init_db()
    load_state()
    register_commands()

    sub_count = db_subscriber_count()
    url_count = db_url_count()
    print(f"Bot running. Subscribers: {sub_count} | Watched URLs: {url_count}")

    threading.Thread(target=listen_for_commands, daemon=True).start()
    check_loop()
