# TikTok New Video Monitor

Monitors the TikTok account **@bekimoon0043** and sends a Telegram notification with the video link as soon as a new post is detected.

## Features
- Checks every 3 minutes
- On first deployment: immediately sends the current latest video link
- After that: only notifies when a **new** video is posted
- Free to run on Render

## Telegram Configuration
- Bot Token: already set in the code (or use environment variable `TELEGRAM_BOT_TOKEN`)
- Chat ID: `6546621672`

## Deploy on Render (Free)

1. Go to [https://dashboard.render.com](https://dashboard.render.com)
2. Click **New +** → **Web Service**
3. Connect the repository: `Bekimoon0043/tiktok-monitor-beki`
4. Configure:
   - **Name**: `tiktok-monitor-beki` (or any name)
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Instance Type**: Free
5. (Optional but recommended) Add Environment Variables:
   - `TELEGRAM_BOT_TOKEN` = your bot token
   - `TELEGRAM_CHAT_ID` = `6546621672`
6. Click **Create Web Service**

### Keep it awake (important)
Free services on Render sleep after 15 minutes of inactivity.  
To keep it running 24/7 for free:

1. Create a free account on [https://uptimerobot.com](https://uptimerobot.com)
2. Add a new **HTTP(s) Monitor**
3. Monitor URL: your Render service URL (example: `https://tiktok-monitor-beki.onrender.com`)
4. Monitoring Interval: **5 minutes**

This will ping your service every 5 minutes and prevent it from sleeping.

## Local Testing

```bash
pip install -r requirements.txt
python main.py
```

## Notes
- **First run**: Sends the current latest video link to Telegram, then saves it.
- **Later runs**: Only sends a message when a newer video appears.
- yt-dlp may occasionally fail if TikTok changes something. The script will retry on the next cycle.

---
Made for monitoring @bekimoon0043
