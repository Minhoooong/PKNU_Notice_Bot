import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
import json
import os
import subprocess

# 상수 정의
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# 봇 초기화: parse_mode를 DefaultBotProperties를 사용하여 설정
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
logging.basicConfig(level=logging.INFO)

def load_seen_announcements():
    """
    상태 파일(announcements_seen.json)에서 이전에 받은 공지사항 목록을 읽어옵니다.
    파일이 없거나 내용이 비어있으면 빈 집합을 반환합니다.
    각 공지사항은 (title, href, department, date) 튜플로 저장됩니다.
    """
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
    """
    전달받은 공지사항 집합을 상태 파일(announcements_seen.json)에 저장합니다.
    """
    with open("announcements_seen.json", "w", encoding="utf-8") as f:
        json.dump(list(seen), f, ensure_ascii=False)

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

def get_school_notices():
    """
    학교 공지사항 페이지에서 각 공지사항의 제목, 링크, 작성자(부서), 날짜를 크롤링합니다.
    각 공지사항은 (title, href, department, date) 튜플로 저장됩니다.
    """
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
    
    [부경대 {department} 공지사항 업데이트]
    
    <b>{title}</b>
    
    {date}
    <a href="{href}">자세히 보기</a>
    """
    for idx, notice in enumerate(notices, 1):
        title, href, department, date = notice
        header = f"[부경대 {department} 공지사항 업데이트]"
        message_text = f"{header}\n\n<b>{title}</b>\n\n{date}\n<a href=\"{href}\">자세히 보기</a>"
        await bot.send_message(chat_id=CHAT_ID, text=message_text)
        # 메시지 사이에 딜레이를 주려면 아래를 활성화하세요.
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
