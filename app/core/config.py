# app/core/config.py (수정)
from pydantic_settings import BaseSettings
from pathlib import Path
import yaml

class Settings(BaseSettings):
    PKNU_USERNAME: str
    PKNU_PASSWORD: str
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    HEADLESS: bool = True

    class Config:
        env_file = ".env" # 프로젝트 루트의 .env 파일을 사용

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
