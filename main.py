import os
import os
import time
import logging
import threading
import random
from datetime import datetime, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, Request, HTTPException, Depends, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from supabase import create_client, Client

# ================== CONFIG ==================
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")  # service_role key

API_KEY = os.getenv("API_KEY", "")
# TikTok Display API: the token must belong to a user who authorized video.list.
TIKTOK_ACCESS_TOKEN = os.getenv("TIKTOK_ACCESS_TOKEN", "")
TIKTOK_API_BASE = "https://open.tiktokapis.com/v2"
CHECK_INTERVAL = max(int(os.getenv("CHECK_INTERVAL", "900")), 300)
# Optional small jitter spreads scheduled requests for load smoothing; it is not a bypass.
CHECK_JITTER_SECONDS = max(int(os.getenv("CHECK_JITTER_SECONDS", "60")), 0)
FAIL_THRESHOLD = int(os.getenv("FAIL_THRESHOLD", "3"))
PORT = int(os.getenv("PORT", "10000"))
# ============================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="TikTok Monitor", version="2.1")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

supabase: Optional[Client] = None


def get_supabase() -> Client:
    global supabase
    if supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_KEY must be set")
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return supabase


def init_db():
    try:
        sb = get_supabase()
        res = sb.table("monitored_accounts").select("id").limit(1).execute()
        if not res.data:
            sb.table("monitored_accounts").insert({
                "username": "bekimoon0042",
                "is_active": True,
                "failure_count": 0,
            }).execute()
            logger.info("Seeded default account: bekimoon0042")
    except Exception as e:
        logger.warning(f"Could not seed DB: {e}")


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


def get_latest_videos(username: str, max_videos: int = 5) -> List[dict]:
    """Fetch recent videos through TikTok's authorized Display API.

    Display API access is limited to the TikTok user who granted the app the
    ``video.list`` scope. It cannot be used to monitor arbitrary accounts.
    """
    if not TIKTOK_ACCESS_TOKEN:
        raise RuntimeError("TIKTOK_ACCESS_TOKEN is not configured")

    response = requests.post(
        f"{TIKTOK_API_BASE}/video/list/",
        params={
            "fields": "id,title,video_description,share_url,embed_link,create_time",
        },
        headers={
            "Authorization": f"Bearer {TIKTOK_ACCESS_TOKEN}",
            "Content-Type": "application/json",
        },
        json={"max_count": min(max(max_videos, 1), 20)},
        timeout=20,
    )
    if response.status_code in (401, 403):
        raise RuntimeError("TikTok authorization failed; re-authorize video.list access")
    if response.status_code == 429:
        raise RuntimeError("TikTok API rate limit reached; retry later")
    response.raise_for_status()

    payload = response.json()
    api_error = payload.get("error") or {}
    if api_error.get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok API error: {api_error.get('message', api_error.get('code'))}")

    videos = []
    for entry in (payload.get("data") or {}).get("videos") or []:
        video_id = entry.get("id")
        if not video_id:
            continue
        videos.append({
            "id": str(video_id),
            "title": (entry.get("title") or entry.get("video_description") or "No caption")[:200],
            "url": entry.get("share_url") or entry.get("embed_link") or f"https://www.tiktok.com/@{username}/video/{video_id}",
        })
    return videos


def process_account(account: dict):
    username = account["username"]
    last_video_id = account.get("last_video_id")
    account_id = account["id"]
    failure_count = int(account.get("failure_count") or 0)
    now = datetime.now(timezone.utc).isoformat()

    logger.info(f"Checking @{username}...")

    try:
        videos = get_latest_videos(username)
    except Exception as e:
        # Explicit exception from the authorized TikTok API client
        failure_count += 1
        err_msg = str(e)[:300]
        logger.warning(f"@{username} fetch failed ({failure_count}/{FAIL_THRESHOLD}): {err_msg}")
        try:
            get_supabase().table("monitored_accounts").update({
                "failure_count": failure_count,
                "last_error": err_msg,
                "last_checked_at": now,
            }).eq("id", account_id).execute()
        except Exception as db_e:
            logger.error(f"DB update on failure: {db_e}")

        if failure_count == FAIL_THRESHOLD:
            send_telegram(
                f"⚠️ <b>Monitor Alert</b>\n\n"
                f"Account: <b>@{username}</b>\n"
                f"Failed <b>{failure_count}</b> times in a row.\n\n"
                f"Error: <code>{err_msg}</code>\n\n"
                f"Possible causes: TikTok authorization, API rate-limit, token expiry, or account configuration."
            )
        return

    if not videos:
        # Empty result counts as a soft failure
        failure_count += 1
        err_msg = "No videos returned (empty result)"
        logger.warning(f"@{username}: {err_msg} ({failure_count}/{FAIL_THRESHOLD})")
        try:
            get_supabase().table("monitored_accounts").update({
                "failure_count": failure_count,
                "last_error": err_msg,
                "last_checked_at": now,
            }).eq("id", account_id).execute()
        except Exception:
            pass

        if failure_count == FAIL_THRESHOLD:
            send_telegram(
                f"⚠️ <b>Monitor Alert</b>\n\n"
                f"Account: <b>@{username}</b>\n"
                f"Failed <b>{failure_count}</b> times in a row (empty results).\n\n"
                f"Check if the account exists and is public."
            )
        return

    # Success — reset failure counter
    latest = videos[0]

    if last_video_id is None:
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
                "failure_count": 0,
                "last_error": None,
            }).eq("id", account_id).execute()
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
                "failure_count": 0,
                "last_error": None,
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
                "failure_count": 0,
                "last_error": None,
            }).eq("id", account_id).execute()
        except Exception:
            pass


def monitor_loop():
    logger.info(f"Background monitor started (interval={CHECK_INTERVAL}s, fail_threshold={FAIL_THRESHOLD})")
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
                # small delay between accounts to be gentle
                time.sleep(5)
        except Exception as e:
            logger.error(f"Monitor loop error: {e}")
        delay = CHECK_INTERVAL + (random.randint(0, CHECK_JITTER_SECONDS) if CHECK_JITTER_SECONDS else 0)
        logger.info(f"Sleeping {delay}s before the next permitted API poll...")
        time.sleep(delay)


def verify_api_key(api_key: Optional[str] = Depends(api_key_header)):
    if api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
    return api_key


class AccountCreate(BaseModel):
    username: str
    is_active: bool = True


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
    .err { color: #ff8a8a; font-size: 0.8rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
  </style>
</head>
<body>
  <h1>🎬 TikTok Monitor</h1>
  <p class="sub">Check every 30 min · Failure alerts after {{ fail_threshold }} tries · API for n8n</p>

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
          <th>Last video</th>
          <th>Last checked</th>
          <th>Fails</th>
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
          <td>{{ a.failure_count or 0 }}{% if a.last_error %}<div class="err" title="{{ a.last_error }}">{{ a.last_error }}</div>{% endif %}</td>
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
    <p style="color:var(--muted);margin-bottom:0.75rem;font-size:0.9rem;">Header <code>X-API-Key: {{ api_key }}</code></p>
    <div class="api-box">GET  /api/accounts
POST /api/accounts   body: {"username": "name"}
DELETE /api/accounts/{username}
GET  /api/status
POST /api/check/{username}
GET  /health</div>
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
    html = Template(HTML_TEMPLATE).render(
        accounts=accounts, api_key=API_KEY, fail_threshold=FAIL_THRESHOLD
    )
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
            "failure_count": 0,
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


@app.get("/api/status")
async def api_status(_: str = Depends(verify_api_key)):
    try:
        sb = get_supabase()
        res = sb.table("monitored_accounts").select("id, is_active, failure_count").execute()
        total = len(res.data or [])
        active = sum(1 for a in (res.data or []) if a.get("is_active"))
        return {
            "ok": True,
            "total_accounts": total,
            "active_accounts": active,
            "check_interval_seconds": CHECK_INTERVAL,
            "fail_threshold": FAIL_THRESHOLD,
        }
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
        "failure_count": 0,
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
    res2 = sb.table("monitored_accounts").select("*").eq("username", username).single().execute()
    return {"ok": True, "account": res2.data}



@app.get("/api/video/{video_id}/direct-url")
async def api_direct_video_url(
    video_id: str,
    username: str,
    _: str = Depends(verify_api_key),
):
    """Direct media URL resolution is intentionally unsupported.

    TikTok's authorized Display API provides share/embed links rather than a
    downloader endpoint. Consumers should use the returned share or embed URL.
    """
    raise HTTPException(
        status_code=410,
        detail="Direct media downloading is not supported. Use the TikTok share/embed URL from the monitor response.",
    )


@app.get("/health")
async def health():
    return {"status": "ok", "interval_seconds": CHECK_INTERVAL}


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
