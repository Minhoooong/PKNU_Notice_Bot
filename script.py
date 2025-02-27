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

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# 상수 정의
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 봇 초기화 (HTML 포맷 메시지 사용)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()  # aiogram v3에서는 인자 없이 생성

def load_seen_announcements():
    """
    상태 파일(announcements_seen.json)에서 이전에 받은 공지사항 목록을 읽어옵니다.
    파일이 없거나 내용이 비어있으면 빈 리스트를 반환합니다.
    각 공지사항은 (title, href, department, date) 튜플로 저장됩니다.
    """
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
    """
    전달받은 공지사항 리스트를 상태 파일(announcements_seen.json)에 저장합니다.
    """
    with open("announcements_seen.json", "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False)

def commit_state_changes():
    """
    상태 파일의 변경사항을 Git에 커밋하고 푸시합니다.
    개인 액세스 토큰(MY_PAT)을 사용하여 원격 URL을 재설정합니다.
    """
    subprocess.run(["git", "config", "--global", "user.email", "you@example.com"], check=True)
    subprocess.run(["git", "config", "--global", "user.name", "Minhoooong"], check=True)
    
    token = os.environ.get("MY_PAT")
    if token:
        subprocess.run([
            "git", "remote", "set-url", "origin",
            f"https://Minhoooong:{token}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        ], check=True)
    
    # 디버그: 원격 URL 출력 (토큰은 마스킹됨)
    subprocess.run(["git", "remote", "-v"], check=True)
    
    subprocess.run(["git", "add", "announcements_seen.json"], check=True)
    subprocess.run(["git", "commit", "-m", "Update seen announcements"], check=False)
    subprocess.run(["git", "push"], check=True)

def parse_date(date_str):
    """
    "YYYY-MM-DD" 형식의 문자열을 datetime 객체로 변환합니다.
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

def get_school_notices():
    """
    학교 공지사항 페이지에서 각 공지사항의 제목, 링크, 작성자(부서), 날짜를 크롤링합니다.
    각 공지사항은 (title, href, department, date) 튜플로 저장됩니다.
    """
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

@dp.message(Command(commands=["start"]))
async def start_command(message: types.Message):
    """
    /start 명령어를 처리하여 인사 메시지와 사용 가능한 명령어 안내를 전송합니다.
    """
    reply_text = (
        "안녕하세요! 공지사항 봇입니다.\n\n"
        "사용 가능한 명령어:\n"
        "/filter YYYY-MM-DD  -  지정 날짜의 공지사항 필터링\n"
    )
    await message.reply(reply_text)

@dp.message(Command(commands=["filter"]))
async def filter_announcements(message: types.Message):
    """
    /filter 명령어를 처리하여 사용자가 입력한 날짜(YYYY-MM-DD)에 해당하는 공지사항을 필터링하여 전송합니다.
    """
    try:
        args = message.get_args().strip()
        logging.info(f"/filter command received with args: {args}")
        if not args:
            await message.reply("날짜 형식(YYYY-MM-DD)을 입력해 주세요. 예: /filter 2025-02-27")
            return
        
        filter_date = parse_date(args)
        if not filter_date:
            await message.reply("올바른 날짜 형식(YYYY-MM-DD)을 입력해 주세요.")
            return
        
        all_notices = load_seen_announcements() + get_school_notices()
        logging.info(f"Total notices loaded: {len(all_notices)}")
        for n in set(all_notices):
            logging.info(f"Notice: {n}")
        
        filtered = [n for n in set(all_notices) if parse_date(n[3]) == filter_date]
        if not filtered:
            await message.reply(f"{args} 날짜의 공지사항이 없습니다.")
            return
        
        sorted_filtered = sorted(filtered, key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        for notice in sorted_filtered:
            await send_notification(notice)
        await message.reply(f"{args} 날짜의 공지사항을 전송했습니다.")
    except Exception as e:
        logging.exception("Error during filtering")
        await message.reply("공지사항 필터링 중 에러가 발생했습니다.")

async def scheduled_updates():
    """
    자동 업데이트 작업: 공지사항을 크롤링하여 새로운 공지가 있으면 전송하고, 상태 파일을 업데이트합니다.
    """
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
    # 스케줄링 작업을 별도 태스크로 실행하고, 봇의 명령어 처리를 위해 폴링 시작
    asyncio.create_task(scheduled_updates())
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
