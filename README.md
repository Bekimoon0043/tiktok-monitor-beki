# TikTok Monitor v2

Monitors TikTok accounts, stores state in **Supabase** (survives redeploys), has a **web dashboard**, and exposes an **HTTP API** for n8n.

## Features
- Multiple accounts (add/remove from UI or API)
- Last video ID stored in Supabase → no duplicate notifications after redeploy
- Web UI to manage accounts
- REST API for n8n (list / add / delete / force-check)
- Telegram notifications

---

## 1. Create Supabase tables

In your Supabase project → **SQL Editor** → run this:

```sql
-- Accounts to monitor
create table if not exists monitored_accounts (
  id bigserial primary key,
  username text unique not null,
  last_video_id text,
  last_checked_at timestamptz,
  is_active boolean default true,
  created_at timestamptz default now()
);

-- Optional: history of sent notifications
create table if not exists notification_log (
  id bigserial primary key,
  username text,
  video_id text,
  video_url text,
  caption text,
  sent_at timestamptz default now()
);

-- Allow the service role full access (default for service_role key)
-- If you use anon key, enable RLS policies as needed.
```

---

## 2. Environment variables (Render)

| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | Project URL (e.g. `https://xxxx.supabase.co`) |
| `SUPABASE_KEY` | **service_role** key (Settings → API) |
| `TELEGRAM_BOT_TOKEN` | Your bot token |
| `TELEGRAM_CHAT_ID` | `6546621672` |
| `API_KEY` | Secret for n8n (e.g. `my-n8n-secret`) |
| `CHECK_INTERVAL` | Seconds between checks (default `180`) |

---

## 3. Deploy on Render

1. **New** → **Web Service** → connect `Bekimoon0043/tiktok-monitor-beki`
2. **Build Command**: `pip install -r requirements.txt`
3. **Start Command**: `python main.py`  (or `uvicorn main:app --host 0.0.0.0 --port $PORT`)
4. **Instance**: Free
5. Add the environment variables above
6. Deploy

Open the Render URL → you should see the dashboard.

Keep it awake with [UptimeRobot](https://uptimerobot.com) (ping `/health` every 5 min).

---

## 4. Web UI

Visit the root URL of your service:
- Add / pause / resume / delete accounts
- See last video ID and last checked time

---

## 5. API for n8n

All API routes require header:

```
X-API-Key: your-api-key
```

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Health + account counts |
| GET | `/api/accounts` | List all accounts |
| POST | `/api/accounts` | Body: `{"username": "name"}` |
| DELETE | `/api/accounts/{username}` | Remove account |
| POST | `/api/check/{username}` | Force check now |
| GET | `/health` | Simple health (no auth) |

### n8n example
- **HTTP Request** node
- Method: `GET` or `POST`
- URL: `https://your-service.onrender.com/api/accounts`
- Header: `X-API-Key` = your secret

---

## Local run

```bash
export SUPABASE_URL=...
export SUPABASE_KEY=...
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=6546621672
export API_KEY=dev-secret
pip install -r requirements.txt
python main.py
```

---

Made for monitoring TikTok accounts with persistence + n8n integration.
