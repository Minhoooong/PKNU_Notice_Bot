from pydantic_settings import BaseSettings
from pathlib import Path
import yaml


class Settings(BaseSettings):
    TELEGRAM_BOT_TOKEN: str | None = None
    TELEGRAM_CHAT_ID: str | None = None
    PKNU_USERNAME: str | None = None
    PKNU_PASSWORD: str | None = None

    BASE_DIR: Path = Path(__file__).resolve().parents[2]
    SELECTOR_FILE: str = str(BASE_DIR / "selectors.pknuai.yaml")
    POLL_INTERVAL_SEC: int = 300
    HEADLESS: bool = True


class Selectors:
    def __init__(self, path: str):
        with open(path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f)

    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            node = node.get(k, {}) if isinstance(node, dict) else {}
        return node or default


settings = Settings()
selectors = Selectors(settings.SELECTOR_FILE)
