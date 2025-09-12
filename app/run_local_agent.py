import asyncio, os, json, hashlib
from pathlib import Path
from urllib.parse import urljoin
from playwright.async_api import async_playwright
from app.core.config import selectors
from app.adapters.pknu_ai_2025 import PKNUAI2025
from app.utils_urlfilter import is_blocked_url


SAVE_JSON = Path("nonSbjt_all.json")
SEEN_DB   = Path("pknu_nonSbjt_seen.txt")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

async def tg_send(text: str):
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return
    import httpx
    async with httpx.AsyncClient(timeout=10) as client:
        api = f"https.api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        await client.post(api, data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "disable_web_page_preview": True})

def uid(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]

async def main():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page = await browser.new_page()
        await page.goto(urljoin(selectors.get("site","base_url"), selectors.get("site","target_url")))
        print("\n[!] '비교과 프로그램 목록'이 보이면 이 창으로 돌아와 Enter")
        input("[!] Enter → 전체 수집 시작... ")

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
            rid = r.get("id") or uid(r["url"])
            if is_blocked_url(r.get("url", "")):
                continue

            r["id"] = rid
            if rid not in seen:
                new_rows.append(r)
                seen.add(rid)

        SAVE_JSON.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        SEEN_DB.write_text("\n".join(sorted(seen)), encoding="utf-8")
        print(f"[+] 총 {len(rows)}개, 신규 {len(new_rows)}개")

        for r in new_rows:
            msg = (
                f"[비교과] {r.get('title','')}\n"
                f"상태: {r.get('status','')}\n"
                f"기간: {r.get('period','')}\n"
                f"YY/SHTM: {r.get('yy','?')}/{r.get('shtm','?')}\n"
                f"🔗 {r['url']}"
            )
            try:
                await tg_send(msg)
            except Exception:
                pass

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
