# TikTok Monitor

Monitors recent videos for a TikTok account that has authorized the application through TikTok's official Display API, stores state in Supabase, provides a dashboard and HTTP API for n8n, and sends Telegram alerts when a new video appears.

## Important access limitation

This project no longer scrapes TikTok pages or tries to imitate human browsing. Those techniques are fragile, can violate platform rules, and are the reason the previous implementation was blocked. The monitor now calls `POST https://open.tiktokapis.com/v2/video/list/` with a user access token granted the `video.list` scope.

The official Display API returns the recent videos of the TikTok user who authorized the app. It cannot be used to monitor arbitrary third-party accounts without their authorization. If arbitrary public-account research is your legitimate use case, apply for TikTok's Research API separately; it is restricted to approved research clients.

## What changed

- Removed `yt-dlp` and direct media URL extraction.
- Added TikTok Display API access with explicit handling for authorization failures and rate limits.
- Default polling interval is 15 minutes, with an optional small positive jitter for load smoothing. The jitter is not intended to bypass anti-bot controls.
- Removed hard-coded Telegram and API credentials. Any credentials previously committed to the repository should be revoked and replaced.
- The direct-download endpoint now returns `410`; consumers should use the official `share_url` or `embed_link` returned by the API.

## Supabase tables

Run this SQL in the Supabase SQL Editor:

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

alter table monitored_accounts
  add column if not exists failure_count int default 0,
  add column if not exists last_error text;
```

## TikTok authorization

Create a TikTok developer app with Login Kit and the TikTok API product enabled. Request the `user.info.basic` and `video.list` scopes, complete the OAuth authorization flow for the account to be monitored, and store the resulting access token in `TIKTOK_ACCESS_TOKEN`. TikTok access tokens expire and must be refreshed through the official refresh-token flow; do not commit tokens to Git.

The API client requests only metadata and links: `id`, `title`, `video_description`, `share_url`, `embed_link`, and `create_time`. It does not download or resolve TikTok media files.

## Environment variables

| Variable | Required | Description |
|---|---:|---|
| `SUPABASE_URL` | Yes | Supabase project URL |
| `SUPABASE_KEY` | Yes | Supabase service-role key; keep it server-side |
| `TIKTOK_ACCESS_TOKEN` | Yes | Access token authorized for `video.list` |
| `TELEGRAM_BOT_TOKEN` | Yes for alerts | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Yes for alerts | Destination chat ID |
| `API_KEY` | Yes for n8n | Secret for the `X-API-Key` header |
| `CHECK_INTERVAL` | No | Poll interval in seconds; minimum 300, default 900 |
| `CHECK_JITTER_SECONDS` | No | Extra random delay from 0 to this value for load smoothing; default 60 |
| `FAIL_THRESHOLD` | No | Alert after this many consecutive failures; default 3 |
| `PORT` | No | HTTP port; default 10000 |

## Run

```bash
pip install -r requirements.txt
python main.py
```

## API

Every protected request requires:

```text
X-API-Key: your-api-key
```

| Method | Path | Description |
|---|---|---|
| GET | `/api/status` | Health and account counts |
| GET | `/api/accounts` | List monitored accounts |
| POST | `/api/accounts` | Add or update an account, body `{"username":"name"}` |
| DELETE | `/api/accounts/{username}` | Remove an account |
| POST | `/api/check/{username}` | Force a permitted API check |
| GET | `/health` | Unauthenticated liveness check |
| GET | `/api/video/{video_id}/direct-url` | Returns `410`; direct downloading is unsupported |

## Behavior

| Event | Action |
|---|---|
| First successful check | Sends the current latest video link |
| New video detected | Sends the official share or embed link to Telegram |
| Same video | Updates `last_checked_at` without sending an alert |
| Authorization, rate-limit, or network failure | Increments `failure_count` and records the error |
| Repeated failures | Sends a Telegram warning after `FAIL_THRESHOLD` failures |
| Next successful check | Resets `failure_count` to zero |

## Deployment notes

Use a long-running service or a platform background worker for the monitor loop. Do not use a minute-level job runner that starts a new process for every check. Keep all credentials in the hosting provider's secret environment-variable store.

Repository: https://github.com/Bekimoon0043/tiktok-monitor-beki
