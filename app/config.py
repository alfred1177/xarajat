"""Настройки приложения. Все значения берутся из переменных окружения (или файла .env)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_ids: frozenset[int]
    webapp_url: str
    db_path: str
    host: str
    port: int
    currency: str
    org_name: str
    dev_mode: bool


def _ids(raw: str) -> frozenset[int]:
    return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)


def load_settings() -> Settings:
    return Settings(
        bot_token=os.getenv("BOT_TOKEN", ""),
        owner_ids=_ids(os.getenv("OWNER_IDS", "")),
        webapp_url=os.getenv("WEBAPP_URL", "").rstrip("/"),
        db_path=os.getenv("DB_PATH", str(BASE_DIR / "data" / "zakupki.db")),
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8080")),
        currency=os.getenv("CURRENCY", "сум"),
        org_name=os.getenv("ORG_NAME", "Кухня"),
        # DEV_MODE=1 отключает проверку подписи Telegram (только для локальной отладки!)
        dev_mode=os.getenv("DEV_MODE", "0") == "1",
    )
