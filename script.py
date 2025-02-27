import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import json
import os
import subprocess
import html  # HTML 이스케이프 처리를 위해 추가

# 상수 정의
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 봇 초기화: HTML 포맷 메시지 사용
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
logging.basicConfig(level=logging.INFO)

def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return set(tuple(item) for item in data)
            except json.JSONDecodeError:
                return set()
    except FileNotFoundError:
        return set()

def save_seen_announcements(seen):
    with open("announcements_seen.json", "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False)

def commit_state_changes():
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

def get_school_notices():
    response = requests.get(URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    notices = set()
    
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
            notices.add((title, href, department, date))
    return notices

async def send_notification(notices):
    """
    각 공지사항을 개별 텔레그램 메시지로 전송합니다.
    메시지 형식:
    
    [부경대 <b>{department}</b> 공지사항 업데이트]
    
    <b>{title}</b>
    
    {date}
    
    (아래 "자세히 보기" 버튼을 누르면 해당 공지로 이동)
    """
    for idx, notice in enumerate(notices, 1):
        title, href, department, date = notice
        # 동적 텍스트를 HTML 이스케이프 처리
        escaped_title = html.escape(title)
        escaped_department = html.escape(department)
        escaped_date = html.escape(date)
        
        header = f"[부경대 <b>{escaped_department}</b> 공지사항 업데이트]"
        message_text = f"{header}\n\n<b>{escaped_title}</b>\n\n{escaped_date}"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="자세히 보기", url=href)]
        ])
        await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)
        # 필요 시 메시지 사이에 딜레이 추가
        # await asyncio.sleep(1)

async def main():
    previous_notices = load_seen_announcements()
    current_notices = get_school_notices()
    new_notices = current_notices - previous_notices

    if new_notices:
        await send_notification(new_notices)
    else:
        logging.info("새로운 공지사항이 없습니다.")

    updated_state = previous_notices.union(current_notices)
    save_seen_announcements(updated_state)
    commit_state_changes()

if __name__ == '__main__':
    asyncio.run(main())
