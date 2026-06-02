import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo
from typing import Optional

from dotenv import load_dotenv


load_dotenv()


@dataclass
class Config:
    # Kotak Neo credentials per official SDK
    consumer_key: str  # token from Neo app/web Trade API card
    access_token: str  # optional; session token if already generated
    neo_fin_key: str  # optional; usually None
    environment: str = "prod"  # prod or test (if available)
    mobile_number: str = ""  # for TOTP login flow (with country code)
    ucc: str = ""  # client code
    mpin: str = ""  # mpin for TOTP validate
    totp_secret: str = ""  # optional: seed to compute TOTP (if you automate)

    sensex_instrument_token: str = ""
    sensex_exchange_segment: str = "bse_cm"  # e.g., bse_cm or nse_cm; set per scrip master
    sensex_symbol: str = "SENSEX"  # trading_symbol/search term in scrip master
    scrip_master_path: str = ""  # optional local CSV/ZIP path; used to resolve tokens
    scrip_master_url: str = "https://developers.kotaksecurities.com/scrip-master"
    auto_refresh_token: bool = True  # download daily before 9am
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    sqlite_path: str = "storage.db"
    trading_mode: str = "paper"
    timezone: ZoneInfo = ZoneInfo("Asia/Kolkata")
    lots_default: int = 1


def load_config() -> Config:
    return Config(
        consumer_key=os.getenv("CONSUMER_KEY", ""),
        access_token=os.getenv("ACCESS_TOKEN", ""),
        neo_fin_key=os.getenv("NEO_FIN_KEY", ""),
        environment=os.getenv("ENVIRONMENT", "prod"),
        mobile_number=os.getenv("MOBILE_NUMBER", ""),
        ucc=os.getenv("UCC", ""),
        mpin=os.getenv("MPIN", ""),
        totp_secret=os.getenv("TOTP_SECRET", ""),
        sensex_instrument_token=os.getenv("SENSEX_INSTRUMENT_TOKEN", ""),
        sensex_exchange_segment=os.getenv("SENSEX_EXCHANGE_SEGMENT", "bse_cm"),
        sensex_symbol=os.getenv("SENSEX_SYMBOL", "SENSEX"),
        scrip_master_path=os.getenv("SCRIP_MASTER_PATH", ""),
        scrip_master_url=os.getenv("SCRIP_MASTER_URL", "https://developers.kotaksecurities.com/scrip-master"),
        auto_refresh_token=os.getenv("AUTO_REFRESH_TOKEN", "true").lower() == "true",
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        sqlite_path=os.getenv("SQLITE_PATH", "storage.db"),
        trading_mode=os.getenv("TRADING_MODE", "paper"),
        timezone=ZoneInfo(os.getenv("TZ", "Asia/Kolkata")),
        lots_default=int(os.getenv("LOTS_DEFAULT", "1")),
    )
