# app/run_auto_agent.py (파일 전체를 아래 코드로 교체)
import asyncio
import os
import json
import hashlib
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from app.core.config import selectors
from app.adapters.pknu_ai_2025 import PKNUAI2025
from app.utils_urlfilter import is_blocked_url

# --- 상수 정의 ---
SAVE_JSON = Path("nonSbjt_all.json")
SEEN_DB = Path("pknu_nonSbjt_seen.txt")

# --- 환경 변수 로드 ---
PKNU_USERNAME = os.getenv("PKNU_USERNAME")
PKNU_PASSWORD = os.getenv("PKNU_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
GROUP_CHAT_ID = os.getenv('GROUP_CHAT_ID')


async def tg_send(text: str, target_chat_id: str):
    """지정된 채팅 ID로 텔레그램 메시지를 보냅니다."""
    if not (TELEGRAM_BOT_TOKEN and target_chat_id):
        return
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        api_url = f"https.api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = {"chat_id": target_chat_id, "text": text, "disable_web_page_preview": True, "parse_mode": "HTML"}
        try:
            await client.post(api_url, data=data)
        except httpx.RequestError as e:
            logging.error(f"텔레그램 메시지 전송 실패: {e}")


async def auto_login(page):
    """자동으로 포털에 로그인합니다."""
    target = urljoin(selectors.get("site","base_url"), selectors.get("site","target_url"))
    await page.goto(target, wait_until="domcontentloaded")
    try:
        id_input = await page.query_selector("input[type='text'], input[name='id']")
        pw_input = await page.query_selector("input[type='password'], input[name='password']")
        if id_input and pw_input and PKNU_USERNAME and PKNU_PASSWORD:
            await id_input.fill(PKNU_USERNAME)
            await pw_input.fill(PKNU_PASSWORD)
            await pw_input.press("Enter")
            await page.wait_for_timeout(5000)  # 로그인 후 리디렉션 대기
    except Exception as e:
        logging.warning(f"자동 로그인 중 오류 발생 (이미 로그인되었을 수 있음): {e}")


async def get_programs(keyword: str = None) -> list:
    """
    비교과 프로그램을 스크레이핑합니다.
    키워드가 제공되면 검색하고, 그렇지 않으면 전체 목록을 가져옵니다.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await auto_login(page)

        adapter = PKNUAI2025(page, selectors)
        
        # 키워드 검색 기능 추가
        if keyword:
            # PKNUAI2025 어댑터에 검색 기능이 필요. 임시로 구현.
            # 실제로는 adapter.search(keyword)와 같은 메서드를 호출해야 함
            # 여기서는 모든 프로그램을 가져와서 필터링하는 방식으로 대체
            all_items = []
            async for r in adapter.iter_all_terms():
                all_items.append(r)
            
            # 제목 또는 내용에 키워드가 포함된 경우만 필터링
            results = [
                item for item in all_items
                if keyword.lower() in item.get('title', '').lower()
            ]

        else: # 기존 로직 (전체 가져오기)
            results = []
            async for r in adapter.iter_all_terms():
                results.append(r)
        
        await browser.close()
        return results

async def main_auto_check():
    """(자동 알림용) 새로운 비교과 프로그램을 확인하고 그룹 채팅에 알림을 보냅니다."""
    logging.info("자동 비교과 프로그램 확인 시작...")
    all_programs = await get_programs()
    
    seen_ids = set()
    if SEEN_DB.exists():
        seen_ids = set(SEEN_DB.read_text(encoding="utf-8").splitlines())

    new_programs = []
    for r in all_programs:
        rid = r.get("id") or hashlib.sha1(r["url"].encode()).hexdigest()[:16]
        r["id"] = rid
        if rid not in seen_ids:
            new_programs.append(r)
            seen_ids.add(rid)
    
    if new_programs:
        logging.info(f"{len(new_programs)}개의 새로운 비교과 프로그램을 발견했습니다.")
        SEEN_DB.write_text("\n".join(sorted(seen_ids)), encoding="utf-8")
        # push_changes(SEEN_DB.name, "Update seen programs")

        for r in reversed(new_programs): # 최신순으로 보내기 위해
            msg = (f"🎓 <b>{html.escape(r.get('title',''))}</b>\n"
                   f"상태: {html.escape(r.get('status',''))}\n"
                   f"기간: {html.escape(r.get('period',''))}\n"
                   f"🔗 <a href='{r.get('url')}'>자세히 보기</a>")
            await tg_send(msg, target_chat_id=GROUP_CHAT_ID)
    else:
        logging.info("새로운 비교과 프로그램이 없습니다.")


if __name__ == "__main__":
    asyncio.run(main_auto_check())
