import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
import json
import os
import subprocess
import html
from datetime import datetime

# 상수 정의
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 봇 초기화 (HTML 포맷 메시지 사용)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()  # aiogram v3에서는 인자 없이 생성
logging.basicConfig(level=logging.INFO)

def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return [tuple(item) for item in data]
            except json.JSONDecodeError:
                return []
    except FileNotFoundError:
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
    except ValueError:
        return None

def get_school_notices():
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

async def send_notification(notice):
    """
    개별 공지사항을 텔레그램 메시지로 전송합니다.
    메시지 형식:
    
    [부경대 <b>{department}</b> 공지사항 업데이트]
    
    <b>{title}</b>
    
    {date}
    
    (아래 "자세히 보기" 버튼을 누르면 해당 공지로 이동)
    """
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

# 스케줄링 작업: 자동 업데이트 및 새로운 공지 알림
async def scheduled_updates():
    previous_notices = load_seen_announcements()
    current_notices = get_school_notices()
    # 새로운 공지사항 추출 (중복 제거)
    new_notices = [n for n in current_notices if n not in previous_notices]
    if new_notices:
        # 날짜 기준 내림차순(최신순) 정렬
        sorted_new = sorted(new_notices, key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        for notice in sorted_new:
            await send_notification(notice)
    else:
        logging.info("새로운 공지사항이 없습니다.")
    updated_state = list(set(previous_notices) | set(current_notices))
    save_seen_announcements(updated_state)
    commit_state_changes()

# 명령어 핸들러: /filter YYYY-MM-DD
@dp.message(Command(commands=["filter"]))
async def filter_announcements(message: types.Message):
    try:
        args = message.get_args().strip()
        logging.info(f"/filter 명령어 수신: {args}")
        if not args:
            await message.reply("날짜 형식(YYYY-MM-DD)을 입력해 주세요. 예: /filter 2025-02-27")
            return
        
        filter_date = parse_date(args)
        if not filter_date:
            await message.reply("올바른 날짜 형식(YYYY-MM-DD)을 입력해 주세요.")
            return
        
        all_notices = load_seen_announcements() + get_school_notices()
        # 중복 제거 후 날짜 필터링
        filtered = [n for n in set(all_notices) if parse_date(n[3]) == filter_date]
        
        if not filtered:
            await message.reply(f"{args} 날짜의 공지사항이 없습니다.")
            return
        
        # 정렬 (최신순)
        sorted_filtered = sorted(filtered, key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        for notice in sorted_filtered:
            await send_notification(notice)
        await message.reply(f"{args} 날짜의 공지사항을 전송했습니다.")
    except Exception as e:
        logging.exception("필터링 중 에러 발생")
        await message.reply("공지사항 필터링 중 에러가 발생했습니다.")

# /start 명령어 핸들러
@dp.message(Command(commands=["start"]))
async def start_command(message: types.Message):
    reply_text = (
        "안녕하세요! 공지사항 봇입니다.\n\n"
        "사용 가능한 명령어:\n"
        "/filter YYYY-MM-DD  -  지정 날짜의 공지사항 필터링\n"
    )
    await message.reply(reply_text)

async def main():
    # 스케줄링 작업을 별도 태스크로 실행한 후, 봇 명령어 처리를 위해 폴링 시작
    asyncio.create_task(scheduled_updates())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
