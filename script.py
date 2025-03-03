import aiohttp
import logging
import json
import os
import html
import traceback
import sys
import asyncio
import urllib.parse
import hashlib
import subprocess
from aiogram.types import InputFile
from openai import AsyncOpenAI
from aiogram import F  # F 필터 사용을 위해 추가
from aiogram.types import ReplyKeyboardRemove  # ReplyKeyboardRemove 추가
from datetime import datetime
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
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

def generate_cache_key(title, href):
    normalized = f"{title.strip().lower()}::{href.strip()}"
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()
    
def load_cache():
    """ 캐시 파일에서 기존 공지사항 로드 (항상 딕셔너리 반환) """
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
                else:
                    return {}
        except Exception as e:
            logging.error(f"❌ 캐시 로드 오류: {e}")
            return {}
    return {}

def save_cache(data):
    """ 캐시 데이터를 JSON 파일에 저장 (data는 딕셔너리) """
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"❌ 캐시 저장 오류: {e}")

def push_cache_changes():
    try:
        # Git 사용자 설정
        subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "공지봇"], check=True)
        
        # 캐시 파일 변경 사항 추가
        subprocess.run(["git", "add", CACHE_FILE], check=True)
        
        commit_message = "Update announcements_seen.json with new notices"
        # 변경 사항이 없으면 커밋 오류가 발생할 수 있으므로, 오류를 무시하도록 처리
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        
        # 환경 변수 MY_PAT에 저장된 PAT 사용하여 원격 저장소에 푸시
        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ MY_PAT 환경 변수가 설정되어 있지 않습니다.")
            return
        # OWNER와 REPO를 실제 값으로 바꿔야 합니다.
        remote_url = f"https://{pat}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        
        logging.info("✅ 캐시 파일이 저장소에 커밋되었습니다.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 캐시 파일 커밋 오류: {e}")

async def is_new_announcement(title, href):
    cache = load_cache()
    key = generate_cache_key(title, href)
    if key in cache:
        return False
    cache[key] = True
    save_cache(cache)
    return True

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
        "불필요한 중복은 제거하며, 강조할 때 반드시 볼드체(<b> 태그)만 사용하고, 다른 HTML 태그는 사용하지 말아 주세요.:\n\n"
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
    seen_announcements = load_cache()  # 이제 딕셔너리
    logging.info(f"Loaded seen announcements: {seen_announcements}")
    current_notices = await get_school_notices()
    logging.info(f"Fetched current notices: {current_notices}")
    new_notices = []
    for title, href, department, date in current_notices:
        key = generate_cache_key(title, href)
        if key not in seen_announcements:
            new_notices.append((title, href, department, date))
    logging.info(f"DEBUG: New notices detected: {new_notices}")
    if new_notices:
        for notice in new_notices:
            await send_notification(notice)
            key = generate_cache_key(notice[0], notice[1])
            seen_announcements[key] = True
        save_cache(seen_announcements)
        push_cache_changes()  # 저장소에 커밋 및 푸시
        logging.info(f"DEBUG: Updated seen announcements (after update): {seen_announcements}")
    else:
        logging.info("✅ 새로운 공지사항이 없습니다.")
    return new_notices

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

    if summary_text is None:
        summary_text = ""
    safe_summary = summary_text
    # 요약 텍스트를 HTML 이스케이프 처리한 후 이탤릭체로 표시
    message_text = (
        f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
        f"<b>{html.escape(title)}</b>\n\n{html.escape(date)}\n\n"
        f"______________________________________________\n"
        f"{safe_summary}\n\n"
    )
    
    # 이미지 URL이 있으면 메시지 텍스트에 추가
    if image_urls:
        message_text += "이미지 링크:\n" + "\n".join(image_urls) + "\n\n"
    
    # 키보드 생성
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]]
    )
    
    # 텍스트 메시지와 키보드를 함께 전송 (HTML 모드 적용)
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

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
