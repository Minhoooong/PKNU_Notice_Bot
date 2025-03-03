import asyncio
import hashlib
import html
import json
import logging
import os
import subprocess
import sys
import traceback
import urllib.parse
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup, InputFile, ReplyKeyboardRemove)
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

# --- 환경 변수 / 토큰 / 상수 ---
aclient = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
CACHE_FILE = "announcements_seen.json"

CATEGORY_CODES = {
    "전체": "",
    "공지사항": "10001",
    "비교과 안내": "10002",
    "학사 안내": "10003",
    "등록/장학": "10004",
    "초빙/채용": "10007"
}

# --- 로깅 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logfile.log"),
        logging.StreamHandler()
    ]
)

# --- 봇 및 Dispatcher 초기화 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(bot=bot)

# --- FSM 상태 정의 ---
class FilterState(StatesGroup):
    waiting_for_date = State()
    selecting_category = State()

# --- 캐시 키 생성 ---
def generate_cache_key(title: str, href: str) -> str:
    """
    제목과 링크를 결합하여 MD5 해시를 생성한다.
    """
    normalized = f"{title.strip().lower()}::{href.strip()}"
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

# --- 캐시 로드/저장 ---
def load_cache() -> dict:
    """
    캐시 파일에서 기존 공지사항을 딕셔너리 형태로 로드한다.
    존재하지 않거나 오류가 있으면 빈 딕셔너리를 반환한다.
    """
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logging.error(f"❌ 캐시 로드 오류: {e}")
            return {}
    return {}

def save_cache(data: dict) -> None:
    """
    캐시 데이터를 JSON 파일(CACHE_FILE)에 저장한다.
    """
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"❌ 캐시 저장 오류: {e}")

def push_cache_changes() -> None:
    """
    캐시 파일 변경 사항을 GitHub 저장소에 커밋 및 푸시한다.
    """
    try:
        subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "공지봇"], check=True)
        subprocess.run(["git", "add", CACHE_FILE], check=True)

        commit_message = "Update announcements_seen.json with new notices"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)

        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ MY_PAT 환경 변수가 설정되어 있지 않습니다.")
            return

        remote_url = f"https://{pat}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        logging.info("✅ 캐시 파일이 저장소에 커밋되었습니다.")

    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 캐시 파일 커밋 오류: {e}")

# --- 캐시 체크 ---
async def is_new_announcement(title: str, href: str) -> bool:
    """
    공지사항이 새로운지(캐시에 존재하지 않는지) 확인하고,
    새로 발견된 경우 캐시에 기록한다.
    """
    cache = load_cache()
    key = generate_cache_key(title, href)
    if key in cache:
        return False
    cache[key] = True
    save_cache(cache)
    return True

# --- 날짜 파싱 ---
def parse_date(date_str: str):
    """
    'YYYY-MM-DD' 포맷을 datetime 객체로 파싱한다.
    """
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

# --- 비동기 HTTP 요청 ---
async def fetch_url(url: str) -> str:
    """
    aiohttp로 비동기 GET 요청을 보내고, 텍스트를 반환한다.
    실패 시 None을 반환한다.
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    logging.error(f"❌ HTTP 요청 실패 ({response.status}): {url}")
                    return None
                return await response.text()
    except Exception as e:
        logging.error(f"❌ URL 요청 오류: {url}, {e}")
        logging.error(traceback.format_exc())
        return None

# --- 공지사항 크롤링 ---
async def get_school_notices(category: str = "") -> list:
    """
    해당 카테고리(또는 전체)의 공지사항을 가져온다.
    (title, href, department, date) 튜플 리스트로 반환.
    최신 날짜순(내림차순) 정렬.
    """
    try:
        category_url = f"{URL}?cd={category}" if category else URL
        html_content = await fetch_url(category_url)

        if html_content is None:
            logging.error(f"❌ 공지사항 페이지를 불러올 수 없습니다: {category_url}")
            return []

        soup = BeautifulSoup(html_content, 'html.parser')
        notices = []

        for tr in soup.find_all("tr"):
            title_td = tr.find("td", class_="bdlTitle")
            user_td = tr.find("td", class_="bdlUser")
            date_td = tr.find("td", class_="bdlDate")

            if title_td and title_td.find("a") and user_td and date_td:
                a_tag = title_td.find("a")
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href")

                # 상대 경로 처리
                if href.startswith("/"):
                    href = BASE_URL + href
                elif href.startswith("?"):
                    href = BASE_URL + "/main/163" + href
                elif not href.startswith("http"):
                    href = BASE_URL + "/" + href

                department = user_td.get_text(strip=True)
                date_ = date_td.get_text(strip=True)
                notices.append((title, href, department, date_))

        notices.sort(key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        return notices

    except Exception:
        logging.exception("❌ Error in get_school_notices")
        return []

# --- 텍스트 요약 (GPT-4o Mini) ---
async def summarize_text(text: str) -> str:
    """
    GPT-4o Mini를 사용하여 텍스트를 3~5문장으로 요약한다.
    불필요한 중복은 제거하고, <b> 태그만 사용해 강조한다.
    """
    if not text or not text.strip():
        return "요약할 수 없는 공지입니다."

    prompt = (
        f"아래의 텍스트를 3~5 문장으로 간결하고 명확하게 요약해 주세요. "
        "요약문은 가독성이 뛰어나도록 각 핵심 사항을 별도의 문단이나 항목으로 구분하고, "
        "불필요한 중복은 제거하며, 강조할 때 반드시 볼드체(<b> 태그)만 사용하고, "
        "다른 HTML 태그는 사용하지 말아 주세요.:\n\n"
        f"{text}\n\n요약:"
    )

    try:
        response = await aclient.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"❌ OpenAI API 요약 오류: {e}")
        return "요약할 수 없는 공지입니다."

# --- 본문(텍스트/이미지) 추출 ---
async def extract_content(url: str) -> tuple:
    """
    해당 공지 링크의 bdvTxt_wrap 영역에서 본문 텍스트를 추출 및 요약하고,
    '/upload/' 경로의 이미지를 모두 수집하여 함께 반환한다.
    """
    try:
        html_content = await fetch_url(url)
        if not html_content or not html_content.strip():
            logging.error(f"❌ Failed to fetch content: {url}")
            return ("페이지를 불러올 수 없습니다.", [])

        soup = BeautifulSoup(html_content, 'html.parser')
        container = soup.find("div", class_="bdvTxt_wrap")
        if not container:
            container = soup

        paragraphs = container.find_all('p')
        if not paragraphs:
            logging.error(f"❌ No text content found in {url}")
            return ("", [])

        raw_text = ' '.join(
            para.get_text(separator=" ", strip=True) for para in paragraphs
        )

        summary_text = await summarize_text(raw_text) if raw_text.strip() else ""
        images = [
            urllib.parse.urljoin(url, img['src'])
            for img in container.find_all('img')
            if "/upload/" in img.get('src', '')
        ]
        return (summary_text, images)

    except Exception as e:
        logging.error(f"❌ Exception in extract_content for URL {url}: {e}")
        return ("처리 중 오류가 발생했습니다.", [])

# --- 새 공지 확인 ---
async def check_for_new_notices() -> list:
    """
    모든 공지사항을 읽은 뒤, 캐시에 없는(새로운) 공지사항만 찾아서
    알림을 전송하고 캐시를 갱신한다.
    """
    logging.info("Checking for new notices...")
    seen_announcements = load_cache()
    logging.info(f"Loaded seen announcements: {seen_announcements}")

    current_notices = await get_school_notices()
    logging.info(f"Fetched current notices: {current_notices}")

    new_notices = []
    for title, href, department, date_ in current_notices:
        key = generate_cache_key(title, href)
        if key not in seen_announcements:
            new_notices.append((title, href, department, date_))

    logging.info(f"DEBUG: New notices detected: {new_notices}")

    if new_notices:
        for notice in new_notices:
            await send_notification(notice)
            key = generate_cache_key(notice[0], notice[1])
            seen_announcements[key] = True

        save_cache(seen_announcements)
        push_cache_changes()
        logging.info(f"DEBUG: Updated seen announcements (after update): {seen_announcements}")
    else:
        logging.info("✅ 새로운 공지사항이 없습니다.")

    return new_notices

# --- 새 공지 메시지 전송 ---
async def send_notification(notice: tuple) -> None:
    """
    (title, href, department, date)를 받아 텍스트 요약 및 이미지를 포함해
    Telegram으로 메시지를 전송한다.
    """
    title, href, department, date_ = notice
    summary_text, image_urls = await extract_content(href)

    safe_summary = summary_text or ""
    message_text = (
        f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"{html.escape(date_)}\n\n"
        "______________________________________________\n"
        f"{safe_summary}\n\n"
    )

    if image_urls:
        message_text += "\n".join(image_urls) + "\n\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]]
    )
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

# --- 명령어 / 핸들러 ---
@dp.message(Command("checknotices"))
async def manual_check_notices(message: types.Message) -> None:
    """
    사용자가 /checknotices 명령어를 입력하면,
    강제로 새 공지사항을 확인하고 결과를 알려준다.
    """
    new_notices = await check_for_new_notices()
    if new_notices:
        await message.answer(f"📢 {len(new_notices)}개의 새로운 공지사항이 있습니다!")
    else:
        await message.answer("✅ 새로운 공지사항이 없습니다.")

@dp.message(Command("start"))
async def start_command(message: types.Message) -> None:
    """
    사용자가 /start를 입력하면,
    날짜 입력 또는 전체 공지사항 카테고리를 보게 하는 메뉴를 전송한다.
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📅날짜 입력", callback_data="filter_date"),
                InlineKeyboardButton(text="📢전체 공지사항", callback_data="all_notices")
            ]
        ]
    )
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext) -> None:
    """
    '날짜 입력' 버튼을 눌렀을 때,
    MM/DD 형식의 날짜를 입력받을 수 있도록 상태를 설정한다.
    """
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요. (예: 01/31)")
    await state.set_state(FilterState.waiting_for_date)
    await callback.answer()

@dp.callback_query(lambda c: c.data == "all_notices")
async def callback_all_notices(callback: CallbackQuery, state: FSMContext) -> None:
    """
    '전체 공지사항' 버튼을 눌렀을 때,
    원하는 공지사항 카테고리를 선택할 수 있는 버튼 목록을 표시한다.
    """
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=category, callback_data=f"category_{code}")]
            for category, code in CATEGORY_CODES.items()
        ]
    )
    await callback.message.answer("원하는 카테고리를 선택하세요:", reply_markup=keyboard)
    await state.set_state(FilterState.selecting_category)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("category_"))
async def callback_category_selection(callback: CallbackQuery, state: FSMContext) -> None:
    """
    카테고리를 고른 뒤 해당 카테고리의 공지사항을 최대 7개까지 알림으로 전송한다.
    """
    category_code = callback.data.split("_")[1]
    notices = await get_school_notices(category_code)

    if not notices:
        await callback.message.answer("해당 카테고리의 공지사항이 없습니다.")
    else:
        for notice in notices[:7]:
            await send_notification(notice)

    await state.clear()
    await callback.answer()

@dp.message()
async def process_date_input(message: types.Message, state: FSMContext) -> None:
    """
    날짜가 입력되어야 하는 상태에서 사용자가 MM/DD 형식으로 입력하면,
    해당 날짜의 공지사항만 필터링하여 전송한다.
    """
    current_state = await state.get_state()
    if current_state != FilterState.waiting_for_date.state:
        return  # 날짜 대기 상태가 아니면 무시

    input_text = message.text.strip()
    current_year = datetime.now().year
    full_date_str = f"{current_year}-{input_text.replace('/', '-')}"
    filter_date = parse_date(full_date_str)

    if filter_date is None:
        await message.answer("날짜 형식이 올바르지 않습니다. MM/DD 형식으로 다시 입력해 주세요.")
        return

    all_notices = await get_school_notices()
    filtered_notices = [
        n for n in all_notices if parse_date(n[3]) == filter_date
    ]

    if not filtered_notices:
        await message.answer(f"📢 {input_text} 날짜에 해당하는 공지사항이 없습니다.")
    else:
        await message.answer(f"📢 {input_text}의 공지사항을 불러옵니다.", reply_markup=ReplyKeyboardRemove())
        for notice in filtered_notices:
            await send_notification(notice)

    await state.clear()

# --- 메인 실행 ---
async def run_bot() -> None:
    """
    봇을 실행하고, 10분 후 종료(테스트 / 임시 목적)하도록 설정한다.
    """
    await check_for_new_notices()
    try:
        logging.info("🚀 Starting bot polling for 10 minutes...")
        polling_task = asyncio.create_task(dp.start_polling(bot))
        await asyncio.sleep(600)
        logging.info("🛑 Stopping bot polling after 10 minutes...")
        polling_task.cancel()
        await dp.stop_polling()
    except Exception as e:
        logging.error(f"❌ Bot error: {e}")
    finally:
        await bot.session.close()
        logging.info("✅ Bot session closed.")

if __name__ == '__main__':
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run_bot())
    except RuntimeError as e:
        logging.error(f"❌ asyncio 이벤트 루프 실행 중 오류 발생: {e}")
