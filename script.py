import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove
from aiogram.filters import Command, Text
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
dp = Dispatcher()  # aiogram v3에서는 인자 없이 생성

# FSM 상태 정의: 날짜 입력 대기
class FilterState(StatesGroup):
    waiting_for_date = State()

def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return [tuple(item) for item in data]
            except json.JSONDecodeError:
                logging.error("JSONDecodeError in announcements_seen.json")
                return []
    except FileNotFoundError:
        logging.info("announcements_seen.json not found, returning empty list")
        return []

def save_seen_announcements(seen):
    with open("announcements_seen.json", "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)

def commit_state_changes():
    subprocess.run(["git", "config", "--global", "user.email", "you@example.com"], check=True)
    subprocess.run(["git", "config", "--global", "user.name", "Minhoooong"], check=True)
    
    token = os.environ.get("MY_PAT")
    if token:
        subprocess.run([
            "git", "remote", "set-url", "origin",
            f"https://Minhoooong:{token}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        ], check=True)
    
    subprocess.run(["git", "remote", "-v"], check=True)
    subprocess.run(["git", "add", "announcements_seen.json"], check=True)
    subprocess.run(["git", "commit", "-m", "Update seen announcements"], check=False)
    subprocess.run(["git", "push"], check=True)

def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

def get_school_notices():
    try:
        response = requests.get(URL)
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
                if href.startswith("?"):
                    href = BASE_URL + "/" + href
                department = user_td.get_text(strip=True)
                date = date_td.get_text(strip=True)
                notices.append((title, href, department, date))
        return notices
    except Exception as e:
        logging.exception("Error in get_school_notices")
        return []

async def send_notification(notice):
    title, href, department, date = notice
    escaped_title = html.escape(title)
    escaped_department = html.escape(department)
    escaped_date = html.escape(date)
    header = f"[부경대 <b>{escaped_department}</b> 공지사항 업데이트]"
    message_text = f"{header}\n\n<b>{escaped_title}</b>\n\n{escaped_date}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="자세히 보기", url=href)]
    ])
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

# /start 명령어 핸들러: 버튼 2개 ("날짜 입력", "전체 공지사항")
@dp.message(Command(commands=["start"]))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="날짜 입력", callback_data="filter_date")],
        [InlineKeyboardButton(text="전체 공지사항", callback_data="all_notices")]
    ])
    reply_text = "안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:"
    await message.reply(reply_text, reply_markup=keyboard)

# Callback Query 핸들러: "날짜 입력" 버튼
@dp.callback_query(Text(equals="filter_date"))
async def callback_filter_date(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요 (예: 02/27):")
    await state.set_state(FilterState.waiting_for_date)
    await callback.answer()

# Callback Query 핸들러: "전체 공지사항" 버튼
@dp.callback_query(Text(equals="all_notices"))
async def callback_all_notices(callback: types.CallbackQuery):
    await callback.message.edit_reply_markup(reply_markup=None)
    notices = get_school_notices()
    if not notices:
        await callback.message.answer("전체 공지사항이 없습니다.")
    else:
        sorted_notices = sorted(notices, key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        for notice in sorted_notices:
            await send_notification(notice)
    await callback.answer("전체 공지사항을 전송했습니다.")

# FSM 메시지 핸들러: MM/DD 형식 날짜 입력 처리
@dp.message(Text(), state=FilterState.waiting_for_date)
async def process_date_input(message: types.Message, state: FSMContext):
    input_text = message.text.strip()
    logging.info(f"Received date input: {input_text}")
    try:
        current_year = "2025"  # 또는 datetime.now().year 사용
        full_date_str = f"{current_year}-{input_text.replace('/', '-')}"
        logging.info(f"Converted full date: {full_date_str}")
        filter_date = parse_date(full_date_str)
        if not filter_date:
            await message.reply("날짜 변환에 실패했습니다. 올바른 MM/DD 형식으로 입력해 주세요.")
            return
        
        all_notices = load_seen_announcements() + get_school_notices()
        filtered = [n for n in set(all_notices) if parse_date(n[3]) == filter_date]
        if not filtered:
            await message.reply(f"{input_text} 날짜의 공지사항이 없습니다.")
        else:
            sorted_filtered = sorted(filtered, key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
            for notice in sorted_filtered:
                await send_notification(notice)
            await message.reply(f"{input_text} 날짜의 공지사항을 전송했습니다.", reply_markup=ReplyKeyboardRemove())
    except Exception as e:
        logging.exception("Error during date input processing")
        await message.reply("날짜 처리 중 에러가 발생했습니다.")
    finally:
        await state.clear()

# 스케줄링 작업: 자동 업데이트 및 새로운 공지 알림
async def scheduled_updates():
    previous_notices = load_seen_announcements()
    current_notices = get_school_notices()
    new_notices = [n for n in current_notices if n not in previous_notices]
    if new_notices:
        sorted_new = sorted(new_notices, key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        for notice in sorted_new:
            await send_notification(notice)
    else:
        logging.info("No new notices.")
    updated_state = list(set(previous_notices) | set(current_notices))
    save_seen_announcements(updated_state)
    commit_state_changes()

async def main():
    # 스케줄링 작업을 별도 태스크로 실행하고, 10분 후 폴링 종료 (예시)
    asyncio.create_task(scheduled_updates())
    try:
        await asyncio.wait_for(dp.start_polling(bot), timeout=600)
    except asyncio.TimeoutError:
        logging.info("Polling timed out after 10 minutes. Terminating this run.")
    finally:
        pass

if __name__ == '__main__':
    asyncio.run(main())
