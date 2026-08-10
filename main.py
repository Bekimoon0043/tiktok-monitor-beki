import os
import time
import json
import logging
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

import requests
import yt_dlp

# ================== CONFIG ==================
TIKTOK_USERNAME = "b_e_k_i_"  # without @
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8296896038:AAHhtevj18C1kqCHj9-x1MO-fkVqiqa-oTQ")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "6546621672")

CHECK_INTERVAL = 180  # seconds (3 minutes)
STATE_FILE = "last_video.json"
PORT = int(os.getenv("PORT", 10000))
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"TikTok Monitor is running")

    def log_message(self, format, *args):
        # Silence default HTTP logs
        return


def start_http_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info(f"HTTP health server running on port {PORT}")
    server.serve_forever()


def send_telegram(message: str):
    """Send message to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(url, json=payload, timeout=15)
        if r.status_code == 200:
            logger.info("Telegram message sent successfully")
        else:
            logger.error(f"Telegram error: {r.text}")
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")


def load_last_video_id():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                data = json.load(f)
                return data.get("last_id")
        except Exception:
            return None
    return None


def save_last_video_id(video_id: str):
    with open(STATE_FILE, "w") as f:
        json.dump({"last_id": video_id, "updated_at": datetime.utcnow().isoformat()}, f)


def get_latest_videos(username: str, max_videos: int = 5):
    """Get latest videos from a TikTok user using yt-dlp"""
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
                logger.warning("No videos found or extraction failed")
                return []

            videos = []
            for entry in info.get("entries", []):
                if not entry:
                    continue
                video_id = entry.get("id")
                title = entry.get("title") or entry.get("description") or "No caption"
                webpage_url = entry.get("url") or entry.get("webpage_url") or f"https://www.tiktok.com/@{username}/video/{video_id}"

                videos.append({
                    "id": video_id,
                    "title": title[:200],
                    "url": webpage_url,
                    "timestamp": entry.get("timestamp")
                })

            return videos

    except Exception as e:
        logger.error(f"Error fetching videos: {e}")
        return []


def check_for_new_videos():
    logger.info(f"Checking @{TIKTOK_USERNAME} for new videos...")

    videos = get_latest_videos(TIKTOK_USERNAME)

    if not videos:
        logger.warning("Could not retrieve any videos")
        return

    latest = videos[0]
    last_id = load_last_video_id()

    if last_id is None:
        # First run - just save the current latest, don't notify about old video
        save_last_video_id(latest["id"])
        logger.info(f"First run. Saved latest video ID: {latest['id']}")
        send_telegram(
            f"🚀 <b>TikTok Monitor Started</b>\n\n"
            f"Now monitoring: <b>@{TIKTOK_USERNAME}</b>\n"
            f"Current latest video saved. You will be notified of new posts."
        )
        return

    if latest["id"] != last_id:
        logger.info(f"New video found: {latest['id']}")

        message = (
            f"🎬 <b>New TikTok Video!</b>\n\n"
            f"Account: <b>@{TIKTOK_USERNAME}</b>\n"
            f"Caption: {latest['title']}\n\n"
            f"🔗 <a href=\"{latest['url']}\">Watch Video</a>\n\n"
            f"ID: <code>{latest['id']}</code>"
        )

        send_telegram(message)
        save_last_video_id(latest["id"])
    else:
        logger.info("No new videos")


def monitor_loop():
    logger.info("TikTok Monitor started")
    send_telegram(
        f"✅ <b>Monitor is online</b>\n"
        f"Watching: @{TIKTOK_USERNAME}\n"
        f"Check interval: {CHECK_INTERVAL} seconds"
    )

    while True:
        try:
            check_for_new_videos()
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}")

        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    # Start HTTP server in background (needed for Render free tier)
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # Start monitoring
    monitor_loop()
