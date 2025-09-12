

import asyncio, os, json, hashlib
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from app.core.config import selectors
from app.adapters.pknu_ai_2025 import PKNUAI2025
from app.utils_urlfilter import is_blocked_url


SAVE_JSON = Path("nonSbjt_all.json")
SEEN_DB   = Path("pknu_nonSbjt_seen.txt")

PKNU_USERNAME = os.getenv("PKNU_USERNAME")
PKNU_PASSWORD = os.getenv("PKNU_PASSWORD")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

async def tg_send(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        await client.post(api, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True})

def uid(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]

async def auto_login(page):
    # navigate to initial target
    target = urljoin(selectors.get("site","base_url"), selectors.get("site","target_url"))
    await page.goto(target, wait_until="domcontentloaded")
    # check for login page
    try:
        id_input = await page.query_selector("input[type='text'], input[name='id'], input[name='userId'], input[name='userid']")
        pw_input = await page.query_selector("input[type='password'], input[name='password'], input[name='pw'], input[name='userPw']")
        if id_input and pw_input and PKNU_USERNAME and PKNU_PASSWORD:
            await id_input.fill(PKNU_USERNAME)
            await pw_input.fill(PKNU_PASSWORD)
            try:
                await pw_input.press("Enter")
            except Exception:
                btn = await page.query_selector("button[type='submit'], input[type='submit']")
                if btn:
                    await btn.click()
            await page.wait_for_timeout(5000)
    except Exception:
        pass
    return

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        await auto_login(page)
        adapter = PKNUAI2025(page, selectors)
        all_terms = os.getenv("ALL_TERMS","true").lower() in ("1","true","yes")
        rows = []
        if all_terms:
            async for r in adapter.iter_all_terms():
                rows.append(r)
        else:
            async for r in adapter.iter_current():
                rows.append(r)
        seen = set()
        if SEEN_DB.exists():
            seen = set(SEEN_DB.read_text(encoding="utf-8").splitlines())
        new_rows = []
        for r in rows:
                    if is_blocked_url(r.get("url", "")):
                continue
            rid = r.get("id") or uid(r["url"])
            r["id"] = rid
            if rid not in seen:
                new_rows.append(r)
                seen.add(rid)
        SAVE_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        SEEN_DB.write_text("\n".join(sorted(seen)), encoding="utf-8")
        for r in new_rows:
            msg = (f"[비교과] {r.get('title','')}\n"
                   f"상태: {r.get('status','')}\n"
                   f"기간: {r.get('period','')}\n"
                   f"YY/SHTM: {r.get('yy','?')}/{r.get('shtm','?')}\n"
                   f"🔗 {r['url']}")
            try:
                await tg_send(msg)
            except Exception:
                pass
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
