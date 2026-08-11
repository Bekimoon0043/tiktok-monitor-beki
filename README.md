# TikTok Monitor v2.1

Monitors TikTok accounts every **30 minutes**, stores state in Supabase, web dashboard, HTTP API for n8n, and Telegram alerts on repeated failures.

## What's improved
- Check interval: **30 minutes** (safer against rate-limits)
- After **3 consecutive failures** → Telegram warning
- `failure_count` + `last_error` stored in DB and shown in UI
- yt-dlp bumped to a recent 2026 version
- 5s delay between accounts when checking multiple

---

## 1. Supabase tables

Run in **SQL Editor**:

```sql
create table if not exists monitored_accounts (
  id bigserial primary key,
  username text unique not null,
  last_video_id text,
  last_checked_at timestamptz,
  is_active boolean default true,
  failure_count int default 0,
  last_error text,
  created_at timestamptz default now()
);

create table if not exists notification_log (
  id bigserial primary key,
  username text,
  video_id text,
  video_url text,
  caption text,
  sent_at timestamptz default now()
);
```

If you already created the table earlier, add the new columns:

```sql
alter table monitored_accounts
  add column if not exists failure_count int default 0,
  add column if not exists last_error text;
```

---

## 2. Render environment variables

| Variable | Example | Notes |
|----------|---------|--------|
| `SUPABASE_URL` | `https://xxxx.supabase.co` | Required |
| `SUPABASE_KEY` | service_role key | Required |
| `TELEGRAM_BOT_TOKEN` | your token | Required |
| `TELEGRAM_CHAT_ID` | `6546621672` | Required |
| `API_KEY` | `my-n8n-secret` | For n8n API |
| `CHECK_INTERVAL` | `1800` | Seconds (default 30 min) |
| `FAIL_THRESHOLD` | `3` | Alert after N failures |

**Start command:** `python main.py`

---

## 3. UptimeRobot (keep Render awake)

Free Render services sleep after ~15 minutes with no traffic. UptimeRobot fixes that.

1. Go to [https://uptimerobot.com](https://uptimerobot.com) and log in
2. **Add New Monitor**
3. Settings:
   - **Monitor Type:** HTTP(s)
   - **Friendly Name:** TikTok Monitor
   - **URL:** `https://YOUR-SERVICE-NAME.onrender.com/health`
   - **Monitoring Interval:** **5 minutes**
4. Save

That pings `/health` every 5 minutes so the service stays online.

Optional: enable email/SMS alerts in UptimeRobot if the monitor goes **Down** (service offline).

---

## 4. API for n8n

Header on every request:
```
X-API-Key: your-api-key
```

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Health + counts |
| GET | `/api/accounts` | List accounts |
| POST | `/api/accounts` | `{"username": "name"}` |
| DELETE | `/api/accounts/{username}` | Remove |
| POST | `/api/check/{username}` | Force check now |
| GET | `/health` | Simple ping (no auth) |

---

## 5. Keeping yt-dlp updated

About once a month (or when checks start failing):

1. In `requirements.txt` set a newer version, e.g. `yt-dlp>=2026.8.1`
2. Commit + push
3. Redeploy on Render

Or on Render: **Manual Deploy** after editing the file on GitHub.

---

## Behavior summary

| Event | Action |
|-------|--------|
| First check for an account | Sends current latest video link |
| New video detected | Sends link to Telegram |
| Same video | Silent, updates `last_checked_at` |
| Fetch fails 1–2 times | Increments `failure_count`, no alert |
| Fetch fails 3 times in a row | Telegram warning + stores `last_error` |
| Next successful check | Resets `failure_count` to 0 |

---

Repo: https://github.com/Bekimoon0043/tiktok-monitor-beki
