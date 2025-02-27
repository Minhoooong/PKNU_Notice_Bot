import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import json
import os
import subprocess
import html
from datetime import datetime

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# 상수 정의
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 봇 및 Dispatcher 초기화 (HTML 포맷 메시지 사용)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# FSM 상태 정의
class FilterState(StatesGroup):
    waiting_for_date = State()

# 공지사항 확인 JSON 로드
def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logging.warning("No previous announcements found or JSON error.")
        return []

# 공지사항 저장
def save_seen_announcements(seen):
    with open("announcements_seen.json", "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)

# Git 변경사항 커밋
def commit_state_changes():
    try:
        subprocess.run(["git", "config", "--global", "user.email", "you@example.com"], check=True)
        subprocess.run(["git", "config", "--global", "user.name", "Minhoooong"], check=True)
        
        token = os.environ.get("MY_PAT")
        if token:
            subprocess.run([
                "git", "remote", "set-url", "origin",
                f"https://Minhoooong:{token
