import logging
import requests
from config import load_config

logger = logging.getLogger(__name__)


class TelegramClient:
    def __init__(self):
        self.cfg = load_config()
        self.base = f"https://api.telegram.org/bot{self.cfg.telegram_bot_token}"
        self.chat_id = self.cfg.telegram_chat_id

    def send_message(self, text: str):
        if not self.cfg.telegram_bot_token or not self.chat_id:
            logger.warning("telegram not configured")
            return
        url = f"{self.base}/sendMessage"
        resp = requests.post(url, json={"chat_id": self.chat_id, "text": text})
        if not resp.ok:
            logger.error("telegram send failed: %s", resp.text)
