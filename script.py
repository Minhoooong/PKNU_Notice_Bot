import aiohttp
import logging
import json
import os
import html
import traceback
import sys
import asyncio
import urllib.parse
from openai import AsyncOpenAI
from aiogram import F  # F 필터 사용을 위해 추가
from aiogram.types import ReplyKeyboardRemove  # ReplyKeyboardRemove 추가
from datetime import datetime
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

aclient = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logfile.log"),
        logging.StreamHandler()
    ]
)

# --- 상수 및 환경 변수 ---
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
CATEGORY_CODES = {
    "전체": "",
    "공지사항": "10001",
    "비교과 안내": "10002",
    "학사 안내": "10003",
    "등록/장학": "10004",
    "초빙/채용": "10007"
}
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')

# --- 봇 및 Dispatcher 초기화 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(bot=bot)

# --- FSM 상태 정의 ---
class FilterState(StatesGroup):
    waiting_for_date = State()
    selecting_category = State()

CACHE_FILE = "announcements_seen.json"

def truncate_text(text, max_length=3000):
    """
    본문이 너무 길 경우 앞부분과 뒷부분을 유지하고 중간을 생략하여 압축.
    """
    if len(text) <= max_length:
        return text  # 길이가 적당하면 그대로 반환

    half = max_length // 2
    return text[:half] + " ... (중략) ... " + text[-half:]  # 앞/뒤 유지, 중간 생략


def load_cache():
    """ 캐시 파일에서 기존 공지사항 로드 """
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_cache(data):
    """ 새로운 공지사항을 캐시에 저장 """
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def is_new_announcement(title, href):
    """ 새로운 공지사항인지 확인 """
    cache = load_cache()
    key = f"{title}::{href}"
    if key in cache:
        return False  # 이미 저장된 공지사항이면 False 반환
    cache[key] = True
    save_cache(cache)
    return True  # 새로운 공지사항이면 True 반환

# --- 날짜 파싱 함수 ---
def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

async def fetch_url(url):
    """ 비동기 HTTP 요청 함수 """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    logging.error(f"❌ HTTP 요청 실패 ({response.status}): {url}")
                    return None
                return await response.text()
    except Exception as e:
        logging.error(f"❌ URL 요청 오류: {url}, {e}")
        logging.error(traceback.format_exc())  # ✅ traceback 추가
        return None

# --- 공지사항 크롤링 ---
async def get_school_notices(category=""):
    try:
        category_url = f"{URL}?cd={category}" if category else URL
        html_content = await fetch_url(category_url)

        # ✅ URL 응답이 None이면 공지사항을 반환하지 않음
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

                # ✅ URL이 상대 경로일 경우 절대 경로로 변환
                if href.startswith("/"):
                    href = BASE_URL + href
                elif href.startswith("?"):
                    href = BASE_URL + "/main/163" + href
                elif not href.startswith("http"):
                    href = BASE_URL + "/" + href

                department = user_td.get_text(strip=True)
                date = date_td.get_text(strip=True)
                notices.append((title, href, department, date))

        notices.sort(key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        return notices

    except Exception as e:
        logging.exception("❌ Error in get_school_notices")
        return []

# --- 텍스트 요약 ---
async def summarize_text(text):
    """
    GPT-4o Mini를 사용하여 텍스트 요약.
    """
    if text is None or not text.strip():
        return "요약할 수 없는 공지입니다."

    prompt = (
        f"아래의 텍스트를 3~5 문장으로 간결하고 명확하게 요약해 주세요. "
        "요약문은 가독성이 뛰어나도록 각 핵심 사항을 별도의 문단이나 항목으로 구분하고, "
        "불필요한 중복은 제거하며, 중요한 내용은 <b>태그</b>를 사용하여 굵게 강조해 주세요.:\n\n"
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

# --- 콘텐츠 추출: bdvTxt_wrap 영역 내 텍스트와 /upload/ 이미지 크롤링 ---
async def extract_content(url):
    try:
        html_content = await fetch_url(url)
        if html_content is None or len(html_content.strip()) == 0:
            logging.error(f"❌ Failed to fetch content: {url}")
            return "페이지를 불러올 수 없습니다.", []

        soup = BeautifulSoup(html_content, 'html.parser')
        container = soup.find("div", class_="bdvTxt_wrap")
        if not container:
            container = soup

        paragraphs = container.find_all('p')
        if not paragraphs:
            logging.error(f"❌ No text content found in {url}")
            return "본문이 없습니다.", []

        raw_text = ' '.join([para.get_text(separator=" ", strip=True) for para in paragraphs])

        if raw_text.strip():
            summary_text = await summarize_text(raw_text)  # await 추가
        else:
            summary_text = "본문이 없습니다."


        images = [urllib.parse.urljoin(url, img['src']) for img in container.find_all('img') if "/upload/" in img['src']]
        return summary_text, images

    except Exception as e:
        logging.error(f"❌ Exception in extract_content for URL {url}: {e}")
        return "처리 중 오류가 발생했습니다.", []

# --- JSON 파일 처리 (공지사항 중복 체크) ---
def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            seen_data = json.load(f)
            return {(item[0], item[1]) if len(item) == 2 else tuple(item) for item in seen_data}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_seen_announcements(seen):
    try:
        with open("announcements_seen.json", "w", encoding="utf-8") as f:
            json.dump([list(item) for item in seen], f, ensure_ascii=False, indent=4)
        # push_changes()  # 제거 또는 주석 처리
    except Exception as e:
        logging.error(f"❌ Failed to save announcements_seen.json and push to GitHub: {e}")

async def check_for_new_notices():
    logging.info("Checking for new notices...")
    seen_announcements = load_seen_announcements()
    logging.info(f"Loaded seen announcements: {seen_announcements}")
    current_notices = await get_school_notices()
    logging.info(f"Fetched current notices: {current_notices}")
    seen_titles_urls = {(title, url) for title, url, *_ in seen_announcements}
    new_notices = [
        (title, href, department, date) for title, href, department, date in current_notices
        if (title, href) not in seen_titles_urls
    ]
    logging.info(f"DEBUG: New notices detected: {new_notices}")
    if new_notices:
        for notice in new_notices:
            await send_notification(notice)
        seen_announcements.update(new_notices)
        save_seen_announcements(seen_announcements)
        logging.info(f"DEBUG: Updated seen announcements (after update): {seen_announcements}")
    else:
        logging.info("✅ 새로운 공지사항이 없습니다.")

    return new_notices  # 새 공지사항 리스트 반환

@dp.message(Command("checknotices"))
async def manual_check_notices(message: types.Message):
    new_notices = await check_for_new_notices()
    if new_notices:
        await message.answer(f"📢 {len(new_notices)}개의 새로운 공지사항이 있습니다!")
    else:
        await message.answer("✅ 새로운 공지사항이 없습니다.")

async def send_notification(notice):
    title, href, department, date = notice
    summary_text, image_urls = await extract_content(href)

    # summary_text가 None이면 기본 메시지 사용
    if summary_text is None:
        summary_text = ""

    message_text = (
        f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
        f"<b>{html.escape(title)}</b>\n\n{html.escape(date)}\n\n"
        f"{html.escape(summary_text)}"
    )
    
    # 키보드 생성
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]]
    )
    
    # 텍스트 메시지와 키보드를 함께 전송 (한 번만 전송)
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

    # 이미지가 있으면 이미지 전송 (필요시)
    if image_urls:
        for url in image_urls:
            try:
                await bot.send_photo(chat_id=CHAT_ID, photo=url)
            except Exception as e:
                logging.error(f"❌ 이미지 전송 오류: {url}, {e}")

@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅날짜 입력", callback_data="filter_date"),
         InlineKeyboardButton(text="📢전체 공지사항", callback_data="all_notices")]
    ])
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

# 삭제: from aiogram.filters import Text

# F를 이용하여 필터링
@dp.callback_query(F.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요. (예: 01/31)")
    await state.set_state(FilterState.waiting_for_date)
    await callback.answer()

@dp.callback_query(F.data == "all_notices")
async def callback_all_notices(callback: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=category, callback_data=f"category_{code}")]
         for category, code in CATEGORY_CODES.items()
    ])
    try:
        await callback.message.answer("원하는 카테고리를 선택하세요:", reply_markup=keyboard)
    except Exception:
        pass
    await state.set_state(FilterState.selecting_category)
    try:
        await callback.answer()
    except Exception:
        pass

@dp.callback_query(F.data.startswith("category_"))
async def callback_category_selection(callback: CallbackQuery, state: FSMContext):
    category_code = callback.data.split("_")[1]
    notices = await get_school_notices(category_code)
    if not notices:
        try:
            await callback.message.answer("해당 카테고리의 공지사항이 없습니다.")
        except Exception:
            pass
    else:
        for notice in notices[:7]:
            await send_notification(notice)
    await state.clear()
    try:
        await callback.answer()
    except Exception:
        pass

@dp.message(F.text)
async def process_date_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    logging.info(f"Current FSM state raw: {current_state}")
    if current_state != FilterState.waiting_for_date.state:
        logging.warning("Received date input, but state is incorrect.")
        return
    input_text = message.text.strip()
    logging.info(f"Received date input: {input_text}")
    current_year = datetime.now().year
    full_date_str = f"{current_year}-{input_text.replace('/', '-')}"
    logging.info(f"Converted full date string: {full_date_str}")
    filter_date = parse_date(full_date_str)
    if filter_date is None:
        await message.answer("날짜 형식이 올바르지 않습니다. MM/DD 형식으로 입력해 주세요.")
        return
    notices = [n for n in await get_school_notices() if parse_date(n[3]) == filter_date]
    if not notices:
        logging.info(f"No notices found for {full_date_str}")
        await message.answer(f"📢 {input_text}의 공지사항이 없습니다.")
    else:
        await message.answer(f"📢 {input_text}의 공지사항입니다.", reply_markup=ReplyKeyboardRemove())
        for notice in notices:
            await send_notification(notice)
    logging.info("Clearing FSM state.")
    await state.clear()

async def run_bot():
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
