import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command
from aiogram.filters.text import Text
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
                f"https://Minhoooong:{token}@github.com/Minhoooong/PKNU_Notice_Bot.git"
            ], check=True)
        
        subprocess.run(["git", "add", "announcements_seen.json"], check=True)
        subprocess.run(["git", "commit", "-m", "Update seen announcements"], check=True)
        subprocess.run(["git", "push"], check=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"Git operation failed: {e}")

# 날짜 파싱 함수
def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

# 공지사항 크롤링
def get_school_notices():
    try:
        response = requests.get(URL, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
        notices = []
        for tr in soup.find_all("tr"):
            title_td = tr.find("td", class_="bdlTitle")
            user_td = tr.find("td", class_="bdlUser")
            date_td = tr.find("td", class_="bdlDate")
            if title_td and title_td.find("a") and user_td and date_td:
                a_tag = title_td.find("a")
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href")
                if href and href.startswith("?"):
                    href = BASE_URL + href
                elif href and not href.startswith("http"):
                    href = BASE_URL + "/" + href
                department = user_td.get_text(strip=True)
                date = date_td.get_text(strip=True)
                notices.append((title, href, department, date))
        return notices
    except Exception as e:
        logging.exception("Error in get_school_notices")
        return []

# 알림 전송
async def send_notification(notice):
    title, href, department, date = notice
    message_text = f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
    message_text += f"<b>{html.escape(title)}</b>\n\n{html.escape(date)}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

# /start 명령어
@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="날짜 입력", callback_data="filter_date")],
        [InlineKeyboardButton(text="전체 공지사항", callback_data="all_notices")]
    ])
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

# Callback Query 핸들러
@dp.callback_query(Text("filter_date"))
async def callback_filter_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요 (예: 02/27):")
    await state.set_state(FilterState.waiting_for_date.state)
    await callback.answer()

@dp.callback_query(Text("all_notices"))
async def callback_all_notices(callback: types.CallbackQuery):
    notices = get_school_notices()
    if not notices:
        await callback.message.answer("전체 공지사항이 없습니다.")
    else:
        for notice in notices:
            await send_notification(notice)
    await callback.answer()

# 날짜 입력 처리
@dp.message(Text())
async def process_date_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state != str(FilterState.waiting_for_date):
        return
    
    try:
        input_text = message.text.strip()
        full_date_str = f"2025-{input_text.replace('/', '-')}"
        filter_date = parse_date(full_date_str)
        
        notices = [n for n in get_school_notices() if parse_date(n[3]) == filter_date]
        if not notices:
            await message.answer(f"{input_text} 날짜의 공지사항이 없습니다.")
        else:
            for notice in notices:
                await send_notification(notice)
            await message.answer(f"{input_text} 날짜의 공지사항을 전송했습니다.", reply_markup=ReplyKeyboardRemove())
    except Exception:
        logging.exception("Error processing date input")
        await message.answer("날짜 처리 중 오류 발생")
    finally:
        await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
