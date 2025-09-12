import asyncio
from playwright.async_api import async_playwright
from aiogram import Dispatcher, types
from aiogram.filters import Command

from app.core.config import settings, selectors
from app.core.logging import setup_logging
from app.adapters.pknu_ai_2025 import PKNUAI2025
from app.notifier import Notifier

# 로거 및 aiogram 디스패처 설정
logger = setup_logging()
dp = Dispatcher()

@dp.message(Command("start", "help"))
async def handle_start(message: types.Message):
    """봇 시작 및 도움말 명령어 처리"""
    await message.answer(
        "안녕하세요! PKNU-AI 비교과 프로그램 검색 봇입니다.\n\n"
        "<b>사용법:</b> `/search [검색어]`\n"
        "<b>예시:</b> `/search 인공지능`"
    )

@dp.message(Command("search"))
async def handle_search(message: types.Message):
    """검색 명령어 처리"""
    keyword = message.text.replace("/search", "").strip()
    if not keyword:
        await message.answer("검색어를 입력해주세요. (예: `/search 인공지능`)")
        return

    await message.answer(f"🔎 '{keyword}' 키워드로 검색을 시작합니다. 잠시만 기다려주세요...")
    
    notifier = Notifier()
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=settings.HEADLESS)
            page = await browser.new_page()
            try:
                adapter = PKNUAI2025(page, selectors)
                
                # 검색 기능도 로그인이 필요하므로 로그인 먼저 수행
                await adapter.login(settings.PKNU_USERNAME, settings.PKNU_PASSWORD)
                
                results = await adapter.search_programs(keyword)
                
                if not results:
                    await message.answer(f"'{keyword}'에 대한 검색 결과가 없습니다.")
                else:
                    await message.answer(f"총 {len(results)}개의 검색 결과를 찾았습니다.")
                    # 메시지를 하나로 묶어 전송하여 API 호출 최소화
                    messages = [notifier.format_search_message(p) for p in results]
                    # 텔레그램 메시지 길이 제한(4096자)을 고려하여 분할 전송
                    response_text = ""
                    for msg in messages:
                        if len(response_text) + len(msg) > 4000:
                            await notifier.send(chat_id=str(message.chat.id), text=response_text)
                            response_text = msg
                        else:
                            response_text += "\n\n" + msg
                    
                    if response_text:
                        await notifier.send(chat_id=str(message.chat.id), text=response_text)
            finally:
                await browser.close()
                
    except Exception as e:
        logger.exception(f"검색 중 오류 발생: {e}")
        await message.answer(f"오류가 발생했습니다. 잠시 후 다시 시도해주세요.")
    finally:
        await notifier.aclose()

async def main():
    """봇의 메인 실행 함수"""
    logger.info("대화형 봇을 시작합니다...")
    # Notifier를 통해 생성된 aiogram 봇 객체를 폴링합니다.
    notifier = Notifier()
    await dp.start_polling(notifier.bot)

if __name__ == "__main__":
    asyncio.run(main())

