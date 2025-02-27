import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, CallbackQuery
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
dp = Dispatcher()  # storage가 필요한 경우 추가 가능

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
        response.raise_for_status()
        
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
    except requests.RequestException as e:
        logging.error(f"Error fetching notices: {e}")
        return []
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

@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="날짜 입력", callback_data="filter_date")],
        [InlineKeyboardButton(text="전체 공지사항", callback_data="all_notices")]
    ])
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

@dp.callback_query(F.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요 (예: 02/27):")
    await state.set_state(FilterState.waiting_for_date)
    await callback.answer()

@dp.callback_query(F.data == "all_notices")
async def callback_all_notices(callback: CallbackQuery):
    notices = get_school_notices()
    if not notices:
        await callback.message.answer("전체 공지사항이 없습니다.")
    else:
        for notice in notices:
            await send_notification(notice)
    await callback.answer()

@dp.message(F.text)
async def process_date_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    logging.info(f"Current FSM state raw: {current_state}")

    # 상태 비교 수정
    if current_state != "FilterState:waiting_for_date":
        logging.warning("Received date input, but state is incorrect.")
        return

    input_text = message.text.strip()
    logging.info(f"Received date input: {input_text}")

    current_year = datetime.now().year
    full_date_str = f"{current_year}-{input_text.replace('/', '-')}"
    logging.info(f"Converted full date string: {full_date_str}")

    filter_date = parse_date(full_date_str)

    if filter_date is None:
        await message.answer("날짜 형식이 올바르지 않습니다. MM/DD 형식으로 입력해 주세요.")
        return

    notices = [n for n in get_school_notices() if parse_date(n[3]) == filter_date]

    if not notices:
        logging.info(f"No notices found for {full_date_str}")
        await message.answer(f"{input_text} 날짜의 공지사항이 없습니다.")
    else:
        for notice in notices:
            await send_notification(notice)
        await message.answer(f"{input_text} 날짜의 공지사항을 전송했습니다.", reply_markup=ReplyKeyboardRemove())

    logging.info("Clearing FSM state.")
    await state.clear()

async def main():
    logging.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
