import os
import time
import logging
import threading
from datetime import datetime, timezone
from typing import Optional, List

import yt_dlp
import requests
from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from supabase import create_client, Client

# ================== CONFIG ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8296896038:AAHhtevj18C1kqCHj9-x1MO-fkVqiqa-oTQ")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6546621672")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # use service_role key for full access

API_KEY = os.getenv("API_KEY", "tiktok-monitor-secret-change-me")  # for n8n / API access
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "180"))  # seconds
PORT = int(os.getenv("PORT", "10000"))
# ============================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="TikTok Monitor", version="2.0")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ---------- Supabase ----------
supabase: Optional[Client] = None

def get_supabase() -> Client:
    global supabase
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase


def init_db():
    """Ensure default account exists if table is empty (optional helper)."""
    try:
        sb = get_supabase()
        res = sb.table("monitored_accounts").select("id").limit(1).execute()
        if not res.data:
            # Seed with the original account
            sb.table("monitored_accounts").insert({
                "username": "bekimoon0042",
                "is_active": True
            }).execute()
            logger.info("Seeded default account: bekimoon0042")
    except Exception as e:
        logger.warning(f"Could not seed DB (table may not exist yet): {e}")


# ---------- Telegram ----------
def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            logger.info("Telegram message sent")
        else:
            logger.error(f"Telegram error: {r.text}")
    except Exception as e:
        logger.error(f"Failed to send Telegram: {e}")


# ---------- TikTok ----------
def get_latest_videos(username: str, max_videos: int = 5) -> List[dict]:
    url = f"https://www.tiktok.com/@{username}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": max_videos,
        "skip_download": True,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info or "entries" not in info:
                return []
            videos = []
            for entry in info.get("entries") or []:
                if not entry:
                    continue
                video_id = entry.get("id")
                title = entry.get("title") or entry.get("description") or "No caption"
                webpage_url = (
                    entry.get("url")
                    or entry.get("webpage_url")
                    or f"https://www.tiktok.com/@{username}/video/{video_id}"
                )
                videos.append({
                    "id": str(video_id),
                    "title": (title or "")[:200],
                    "url": webpage_url,
                })
            return videos
    except Exception as e:
        logger.error(f"yt-dlp error for @{username}: {e}")
        return []


# ---------- Core monitor logic ----------
def process_account(account: dict):
    username = account["username"]
    last_video_id = account.get("last_video_id")
    account_id = account["id"]

    logger.info(f"Checking @{username}...")
    videos = get_latest_videos(username)
    if not videos:
        logger.warning(f"No videos retrieved for @{username}")
        # still update last_checked
        try:
            get_supabase().table("monitored_accounts").update({
                "last_checked_at": datetime.now(timezone.utc).isoformat()
            }).eq("id", account_id).execute()
        except Exception:
            pass
        return

    latest = videos[0]
    now = datetime.now(timezone.utc).isoformat()

    if last_video_id is None:
        # First time seeing this account — send current latest video
        message = (
            f"🚀 <b>Monitor Started / First Check</b>\n\n"
            f"Account: <b>@{username}</b>\n\n"
            f"🎬 <b>Latest video:</b>\n"
            f"Caption: {latest['title']}\n\n"
            f"🔗 <a href=\"{latest['url']}\">Watch Video</a>\n\n"
            f"ID: <code>{latest['id']}</code>"
        )
        send_telegram(message)
        try:
            get_supabase().table("monitored_accounts").update({
                "last_video_id": latest["id"],
                "last_checked_at": now,
            }).eq("id", account_id).execute()
            # optional log
            get_supabase().table("notification_log").insert({
                "username": username,
                "video_id": latest["id"],
                "video_url": latest["url"],
                "caption": latest["title"],
            }).execute()
        except Exception as e:
            logger.error(f"DB update error: {e}")
        return

    if latest["id"] != last_video_id:
        message = (
            f"🎬 <b>New TikTok Video!</b>\n\n"
            f"Account: <b>@{username}</b>\n"
            f"Caption: {latest['title']}\n\n"
            f"🔗 <a href=\"{latest['url']}\">Watch Video</a>\n\n"
            f"ID: <code>{latest['id']}</code>"
        )
        send_telegram(message)
        try:
            get_supabase().table("monitored_accounts").update({
                "last_video_id": latest["id"],
                "last_checked_at": now,
            }).eq("id", account_id).execute()
            get_supabase().table("notification_log").insert({
                "username": username,
                "video_id": latest["id"],
                "video_url": latest["url"],
                "caption": latest["title"],
            }).execute()
        except Exception as e:
            logger.error(f"DB update error: {e}")
    else:
        logger.info(f"No new video for @{username}")
        try:
            get_supabase().table("monitored_accounts").update({
                "last_checked_at": now,
            }).eq("id", account_id).execute()
        except Exception:
            pass


def monitor_loop():
    logger.info("Background monitor started")
    # wait a few seconds for app to boot
    time.sleep(5)
    while True:
        try:
            sb = get_supabase()
            res = sb.table("monitored_accounts").select("*").eq("is_active", True).execute()
            accounts = res.data or []
            logger.info(f"Checking {len(accounts)} active account(s)")
            for acc in accounts:
                try:
                    process_account(acc)
                except Exception as e:
                    logger.error(f"Error processing @{acc.get('username')}: {e}")
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        logger.info(f"Sleeping {CHECK_INTERVAL}s...")
        time.sleep(CHECK_INTERVAL)


# ---------- Auth helper ----------
def verify_api_key(api_key: Optional[str] = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return api_key


# ---------- Pydantic models ----------
class AccountCreate(BaseModel):
    username: str
    is_active: bool = True


class AccountUpdate(BaseModel):
    is_active: Optional[bool] = None
    last_video_id: Optional[str] = None


# ---------- Web UI ----------
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TikTok Monitor</title>
  <style>
    :root { --bg: #0f0f12; --card: #1a1a22; --accent: #fe2c55; --text: #f0f0f5; --muted: #888; }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); min-height: 100vh; padding: 2rem; }
    h1 { font-size: 1.6rem; margin-bottom: 0.5rem; }
    .sub { color: var(--muted); margin-bottom: 2rem; font-size: 0.95rem; }
    .card { background: var(--card); border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 1.25rem; border: 1px solid #2a2a35; }
    table { width: 100%; border-collapse: collapse; }
    th, td { text-align: left; padding: 0.65rem 0.5rem; border-bottom: 1px solid #2a2a35; font-size: 0.9rem; }
    th { color: var(--muted); font-weight: 500; }
    .badge { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 999px; font-size: 0.75rem; }
    .badge-on { background: #14301f; color: #3dd68c; }
    .badge-off { background: #2a1a1a; color: #ff6b6b; }
    form.inline { display: flex; gap: 0.5rem; flex-wrap: wrap; align-items: center; }
    input[type=text] { background: #121218; border: 1px solid #333; color: var(--text); padding: 0.55rem 0.75rem; border-radius: 8px; min-width: 180px; }
    button, .btn { background: var(--accent); color: white; border: none; padding: 0.55rem 1rem; border-radius: 8px; cursor: pointer; font-weight: 600; font-size: 0.9rem; text-decoration: none; display: inline-block; }
    button.secondary { background: #333; }
    button.danger { background: #c0392b; }
    .api-box { font-family: ui-monospace, monospace; font-size: 0.8rem; background: #121218; padding: 1rem; border-radius: 8px; overflow-x: auto; white-space: pre-wrap; color: #a0d8ff; }
    a { color: #7eb8ff; }
  </style>
</head>
<body>
  <h1>🎬 TikTok Monitor</h1>
  <p class="sub">Manage accounts · Status persists in Supabase · API ready for n8n</p>

  <div class="card">
    <h3 style="margin-bottom: 0.75rem;">Add account</h3>
    <form class="inline" method="post" action="/ui/add">
      <input type="text" name="username" placeholder="username (without @)" required>
      <button type="submit">Add</button>
    </form>
  </div>

  <div class="card">
    <h3 style="margin-bottom: 0.75rem;">Monitored accounts</h3>
    {% if accounts %}
    <table>
      <thead>
        <tr>
          <th>Username</th>
          <th>Last video ID</th>
          <th>Last checked</th>
          <th>Status</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {% for a in accounts %}
        <tr>
          <td><a href="https://www.tiktok.com/@{{ a.username }}" target="_blank">@{{ a.username }}</a></td>
          <td style="font-family:monospace;font-size:0.8rem;">{{ a.last_video_id or '—' }}</td>
          <td>{{ a.last_checked_at or '—' }}</td>
          <td>
            {% if a.is_active %}
              <span class="badge badge-on">Active</span>
            {% else %}
              <span class="badge badge-off">Paused</span>
            {% endif %}
          </td>
          <td>
            <form class="inline" method="post" action="/ui/toggle/{{ a.id }}" style="display:inline;">
              <button type="submit" class="secondary">{% if a.is_active %}Pause{% else %}Resume{% endif %}</button>
            </form>
            <form class="inline" method="post" action="/ui/delete/{{ a.id }}" style="display:inline;" onsubmit="return confirm('Delete @{{ a.username }}?')">
              <button type="submit" class="danger">Delete</button>
            </form>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p style="color:var(--muted);">No accounts yet. Add one above.</p>
    {% endif %}
  </div>

  <div class="card">
    <h3 style="margin-bottom: 0.5rem;">API for n8n</h3>
    <p style="color:var(--muted);margin-bottom:0.75rem;font-size:0.9rem;">Send header <code>X-API-Key: {{ api_key }}</code></p>
    <div class="api-box">GET  /api/accounts          — list accounts
POST /api/accounts          — body: {"username": "name"}
DELETE /api/accounts/{username}
GET  /api/status            — health + counts
POST /api/check/{username}  — force check now</div>
  </div>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        sb = get_supabase()
        res = sb.table("monitored_accounts").select("*").order("created_at", desc=True).execute()
        accounts = res.data or []
    except Exception as e:
        accounts = []
        logger.error(f"Dashboard DB error: {e}")
    from jinja2 import Template
    html = Template(HTML_TEMPLATE).render(accounts=accounts, api_key=API_KEY)
    return HTMLResponse(html)


@app.post("/ui/add")
async def ui_add(username: str = Form(...)):
    username = username.strip().lstrip("@").lower()
    if not username:
        return RedirectResponse("/", status_code=303)
    try:
        sb = get_supabase()
        sb.table("monitored_accounts").upsert({
            "username": username,
            "is_active": True,
        }, on_conflict="username").execute()
    except Exception as e:
        logger.error(f"Add account error: {e}")
    return RedirectResponse("/", status_code=303)


@app.post("/ui/toggle/{account_id}")
async def ui_toggle(account_id: int):
    try:
        sb = get_supabase()
        res = sb.table("monitored_accounts").select("is_active").eq("id", account_id).single().execute()
        current = res.data.get("is_active", True)
        sb.table("monitored_accounts").update({"is_active": not current}).eq("id", account_id).execute()
    except Exception as e:
        logger.error(f"Toggle error: {e}")
    return RedirectResponse("/", status_code=303)


@app.post("/ui/delete/{account_id}")
async def ui_delete(account_id: int):
    try:
        get_supabase().table("monitored_accounts").delete().eq("id", account_id).execute()
    except Exception as e:
        logger.error(f"Delete error: {e}")
    return RedirectResponse("/", status_code=303)


# ---------- REST API (for n8n) ----------
@app.get("/api/status")
async def api_status(_: str = Depends(verify_api_key)):
    try:
        sb = get_supabase()
        res = sb.table("monitored_accounts").select("id, is_active").execute()
        total = len(res.data or [])
        active = sum(1 for a in (res.data or []) if a.get("is_active"))
        return {"ok": True, "total_accounts": total, "active_accounts": active}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@app.get("/api/accounts")
async def api_list_accounts(_: str = Depends(verify_api_key)):
    sb = get_supabase()
    res = sb.table("monitored_accounts").select("*").order("created_at", desc=True).execute()
    return {"accounts": res.data or []}


@app.post("/api/accounts")
async def api_add_account(body: AccountCreate, _: str = Depends(verify_api_key)):
    username = body.username.strip().lstrip("@").lower()
    if not username:
        raise HTTPException(400, "username required")
    sb = get_supabase()
    res = sb.table("monitored_accounts").upsert({
        "username": username,
        "is_active": body.is_active,
    }, on_conflict="username").execute()
    return {"ok": True, "account": (res.data or [None])[0]}


@app.delete("/api/accounts/{username}")
async def api_delete_account(username: str, _: str = Depends(verify_api_key)):
    username = username.strip().lstrip("@").lower()
    sb = get_supabase()
    sb.table("monitored_accounts").delete().eq("username", username).execute()
    return {"ok": True, "deleted": username}


@app.post("/api/check/{username}")
async def api_force_check(username: str, _: str = Depends(verify_api_key)):
    username = username.strip().lstrip("@").lower()
    sb = get_supabase()
    res = sb.table("monitored_accounts").select("*").eq("username", username).execute()
    if not res.data:
        raise HTTPException(404, f"Account @{username} not found")
    process_account(res.data[0])
    # return updated row
    res2 = sb.table("monitored_accounts").select("*").eq("username", username).single().execute()
    return {"ok": True, "account": res2.data}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------- Startup ----------
@app.on_event("startup")
def on_startup():
    logger.info("App starting...")
    try:
        init_db()
    except Exception as e:
        logger.warning(f"init_db: {e}")
    t = threading.Thread(target=monitor_loop, daemon=True)
    t.start()
    logger.info("Background monitor thread launched")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=False)
