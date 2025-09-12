# app/run_announcement_agent.py
import asyncio
import os
import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright
import httpx

# --- 설정값 ---
BASE_URL = "https://www.pknu.ac.kr"
TARGET_URL = "/main/163"
SEEN_DB_FILE = Path("announcements_seen.json")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram_message(text: str):
    """텔레그램으로 메시지를 비동기 전송합니다."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        print("경고: 텔레그램 환경변수가 설정되지 않아 알림을 보낼 수 없습니다.")
        return
    
    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    params = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True}
    
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(api_url, data=params)
        except httpx.RequestError as e:
            print(f"에러: 텔레그램 메시지 전송 실패 - {e}", flush=True)

async def scrape_announcements(page):
    """공지사항 목록 페이지를 파싱하여 데이터를 추출합니다."""
    print("페이지 로딩 및 파싱 시작...")
    await page.goto(urljoin(BASE_URL, TARGET_URL), wait_until="domcontentloaded")
    
    # 공지사항 목록의 각 행(tr)을 가져옵니다.
    # tr.notice 클래스는 고정 공지이므로 제외합니다.
    rows = await page.query_selector_all("div.brd_list tbody tr:not(.notice)")
    
    announcements = []
    for row in rows:
        # 각 행에서 제목, 작성자, 날짜, 링크 정보를 추출합니다.
        subject_el = await row.query_selector("td.subject a")
        if not subject_el:
            continue

        title = (await subject_el.text_content() or "").strip()
        relative_url = (await subject_el.get_attribute("href") or "").strip()
        absolute_url = urljoin(BASE_URL, relative_url)
        
        writer = (await (await row.query_selector("td.writer")).text_content() or "").strip()
        date = (await (await row.query_selector("td.date")).text_content() or "").strip()
        
        # URL 기반으로 고유 ID 생성
        uid = hashlib.md5(absolute_url.encode()).hexdigest()

        announcements.append({
            "id": uid,
            "title": title,
            "writer": writer,
            "date": date,
            "url": absolute_url,
        })
    print(f"{len(announcements)}개의 공지사항을 파싱했습니다.")
    return announcements

async def main():
    """메인 실행 함수"""
    # 이전에 확인한 공지사항 ID를 불러옵니다.
    seen_ids = set()
    if SEEN_DB_FILE.exists():
        try:
            with open(SEEN_DB_FILE, "r", encoding="utf-8") as f:
                # JSON 파일 형식을 {'id1': true, 'id2': true}로 가정
                seen_ids = set(json.load(f).keys())
        except (json.JSONDecodeError, IOError) as e:
            print(f"경고: {SEEN_DB_FILE} 파일 로드 실패 - {e}", flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        try:
            # 스크레이핑 실행
            all_items = await scrape_announcements(page)
            
            # 새로운 공지사항 필터링
            new_items = [item for item in all_items if item["id"] not in seen_ids]
            
            if not new_items:
                print("새로운 공지사항이 없습니다.", flush=True)
            else:
                print(f"{len(new_items)}개의 새로운 공지사항을 발견했습니다.", flush=True)
                # 최신 공지부터 보내기 위해 리스트를 역순으로 처리
                for item in reversed(new_items):
                    message = (
                        f"[신규 공지] {item['title']}\n"
                        f"작성일: {item['date']}\n"
                        f"링크: {item['url']}"
                    )
                    await send_telegram_message(message)
                    seen_ids.add(item['id'])
            
            # 확인한 ID 목록을 파일에 저장
            # JSON 포맷을 유지하기 위해 딕셔너리 형태로 변환하여 저장
            seen_data_to_save = {uid: True for uid in seen_ids}
            with open(SEEN_DB_FILE, "w", encoding="utf-8") as f:
                json.dump(seen_data_to_save, f, ensure_ascii=False, indent=4)

        except Exception as e:
            print(f"에러: 스크립트 실행 중 예외 발생 - {e}", flush=True)
        finally:
            await browser.close()
            print("스크립트 실행 완료.", flush=True)

if __name__ == "__main__":
    asyncio.run(main())
