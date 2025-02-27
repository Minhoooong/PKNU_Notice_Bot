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
    """
    상태 파일(announcements_seen.json)에서 이전에 받은 공지사항 목록을 읽어옵니다.
    파일이 없거나 내용이 비어있으면 빈 집합을 반환합니다.
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
    subprocess.run(["git", "config", "--global", "user.email", "you@example.com"], check=True)
    subprocess.run(["git", "config", "--global", "user.name", "Minhoooong"], check=True)
    
    token = os.environ.get("MY_PAT")
    if token:
        subprocess.run([
            "git", "remote", "set-url", "origin",
            f"https://Minhoooong:{token}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        ], check=True)
    
    # 디버그: 원격 URL 출력
    subprocess.run(["git", "remote", "-v"], check=True)
    
    subprocess.run(["git", "add", "announcements_seen.json"], check=True)
    subprocess.run(["git", "commit", "-m", "Update seen announcements"], check=False)
    subprocess.run(["git", "push"], check=True)

def get_school_notices():
    response = requests.get(URL)
    soup = BeautifulSoup(response.text, 'html.parser')
    notices = set()
    
    # 모든 <tr> 요소를 순회하면서 공지사항 정보를 추출합니다.
    for tr in soup.find_all("tr"):
        # 공지사항 제목이 있는 <td class="bdlTitle"> 요소 찾기
        title_td = tr.find("td", class_="bdlTitle")
        if title_td:
            a_tag = title_td.find("a")
            if a_tag:
                # <a> 태그의 텍스트(제목)와 href(링크) 추출
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href")
                # 상대 경로인 경우 BASE_URL을 덧붙여 절대 경로로 변환
                if href.startswith("?"):
                    href = BASE_URL + "/" + href
                notices.add((title, href))
    return notices


async def send_notification(notices):
    """
    새 공지사항 목록을 텔레그램 채팅으로 전송합니다.
    """
    if notices:
        message_text = "새로운 공지사항이 있습니다:\n"
        for idx, (title, href) in enumerate(notices, 1):
            message_text += f"{idx}. {title}\n{href}\n"
        await bot.send_message(chat_id=CHAT_ID, text=message_text)

async def main():
    """
    한 번 실행하여:
    1. 이전 상태 파일에서 공지사항 목록을 불러오고,
    2. 현재 페이지의 공지사항을 스크래핑한 후,
    3. 새로 추가된 공지만 텔레그램으로 전송합니다.
    4. 상태 파일을 업데이트하고 Git에 커밋합니다.
    """
    previous_notices = load_seen_announcements()
    current_notices = get_school_notices()
    new_notices = current_notices - previous_notices

    if new_notices:
        await send_notification(new_notices)
    else:
        logging.info("새로운 공지사항이 없습니다.")

    # 상태 업데이트 후 저장 및 커밋/푸시
    updated_state = previous_notices.union(current_notices)
    save_seen_announcements(updated_state)
    commit_state_changes()

if __name__ == '__main__':
    asyncio.run(main())
