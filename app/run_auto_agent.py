# app/run_auto_agent.py (수정)
import asyncio
from playwright.async_api import async_playwright
from app.core.config import settings, selectors
from app.core.logging import setup_logging
from app.adapters.pknu_ai_2025 import PKNUAI2025
from app.storage import get_seen_ids, save_all_programs, add_seen_ids, generate_id # 추가
from app.notifier import Notifier # 추가

logger = setup_logging()

async def main():
    notifier = Notifier()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=settings.HEADLESS)
        page = await browser.new_page()
        try:
            adapter = PKNUAI2025(page, selectors)
            await adapter.login(settings.PKNU_USERNAME, settings.PKNU_PASSWORD)
            
            all_programs, new_programs = [], []
            seen_ids = get_seen_ids()
            
            async for program in adapter.iter_all_terms():
                program['id'] = generate_id(program)
                all_programs.append(program)
                if program['id'] not in seen_ids:
                    new_programs.append(program)
                    seen_ids.add(program['id'])
            
            if new_programs:
                save_all_programs(all_programs)
                add_seen_ids(seen_ids)
                for program in new_programs:
                    await notifier.send(notifier.format_auto_message(program))
            
        except Exception as e:
            logger.exception("자동 에이전트 오류: %s", e)
        finally:
            await browser.close()
    await notifier.aclose()

if __name__ == "__main__":
    asyncio.run(main())
