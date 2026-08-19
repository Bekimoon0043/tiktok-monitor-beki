import os
import unittest
from unittest.mock import Mock, patch

os.environ["TIKTOK_ACCESS_TOKEN"] = "test-token"
os.environ["API_KEY"] = "test-api-key"

import main


class TikTokDisplayApiTests(unittest.TestCase):
    @patch("main.requests.post")
    def test_maps_display_api_videos(self, post):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "data": {
                "videos": [
                    {
                        "id": "123",
                        "title": "A title",
                        "share_url": "https://www.tiktok.com/@creator/video/123",
                        "embed_link": "https://www.tiktok.com/embed/123",
                    }
                ]
            },
            "error": {"code": "ok", "message": ""},
        }
        post.return_value = response

        result = main.get_latest_videos("creator")

        self.assertEqual(result[0]["id"], "123")
        self.assertEqual(result[0]["title"], "A title")
        self.assertEqual(result[0]["url"], "https://www.tiktok.com/@creator/video/123")
        post.assert_called_once()
        self.assertIn("Bearer test-token", post.call_args.kwargs["headers"]["Authorization"])

    @patch("main.requests.post")
    def test_rejects_unauthorized_token(self, post):
        response = Mock(status_code=401)
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "authorization failed"):
            main.get_latest_videos("creator")

    @patch("main.requests.post")
    def test_reports_rate_limit(self, post):
        response = Mock(status_code=429)
        post.return_value = response

        with self.assertRaisesRegex(RuntimeError, "rate limit"):
            main.get_latest_videos("creator")

    @patch("main.requests.post")
    def test_requires_configured_token(self, post):
        with patch.object(main, "TIKTOK_ACCESS_TOKEN", ""):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                main.get_latest_videos("creator")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
