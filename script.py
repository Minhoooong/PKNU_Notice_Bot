import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot
import json
import os
import subprocess

# 상수 정의
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 봇 초기화
bot = Bot(token=TOKEN)
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
        if title_td:
            a_tag = title_td.find("a")
            if a_tag:
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href")
                if href.startswith("?"):
                    href = BASE_URL + "/" + href
                notices.add((title, href))
    return notices

async def send_notification(notices):
    """
    각 공지사항을 개별 텔레그램 메시지로 전송합니다.
    """
    for idx, (title, href) in enumerate(notices, 1):
        message_text = f"{idx}. {title}\n{href}"
        await bot.send_message(chat_id=CHAT_ID, text=message_text)
        # 원한다면 각 메시지 사이에 딜레이를 줄 수 있습니다.
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
