import logging
import asyncio
import requests
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
import json
import os
import subprocess
import html
from datetime import datetime
import urllib.parse

# 로깅 설정
logging.basicConfig(level=logging.INFO)

# 상수 정의
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

# 봇 및 Dispatcher 초기화 (HTML 포맷 메시지 사용)
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# FSM 상태 정의
class FilterState(StatesGroup):
    waiting_for_date = State()
    selecting_category = State()

# 날짜 파싱 함수
def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

# announcements_seen.json 로드 및 저장
def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            seen_data = json.load(f)
            return set(tuple(item) for item in seen_data)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

def save_seen_announcements(seen):
    try:
        with open("announcements_seen.json", "w", encoding="utf-8") as f:
            json.dump(list(seen), f, ensure_ascii=False, indent=4)

        # Git 커밋 및 푸시 (GitHub Actions 실행 시 JSON 데이터 유지)
        subprocess.run(["git", "config", "--global", "user.email", "github-actions@github.com"], check=True)
        subprocess.run(["git", "config", "--global", "user.name", "github-actions"], check=True)
        subprocess.run(["git", "add", "announcements_seen.json"], check=True)
        subprocess.run(["git", "commit", "-m", "Update announcements_seen.json"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)

    except Exception as e:
        logging.error(f"Failed to save announcements_seen.json and push to GitHub: {e}")


# 공지사항 크롤링
def get_school_notices(category=""):
    try:
        category_url = f"{URL}?cd={category}" if category else URL
        response = requests.get(category_url, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'html.parser')
        notices = []
        for tr in soup.find_all("tr"):
            title_td = tr.find("td", class_="bdlTitle")
            user_td = tr.find("td", class_="bdlUser")
            date_td = tr.find("td", class_="bdlDate")
            if title_td and title_td.find("a") and user_td and date_td:
                a_tag = title_td.find("a")
                title = a_tag.get_text(strip=True)
                href = a_tag.get("href")
                if href and href.startswith("?"):
                    href = BASE_URL + href
                elif href and not href.startswith("http"):
                    href = BASE_URL + "/" + href
                department = user_td.get_text(strip=True)
                date = date_td.get_text(strip=True)
                notices.append((title, href, department, date))
        
        # 날짜 기준 최신순 정렬
        notices.sort(key=lambda x: parse_date(x[3]) or datetime.min, reverse=True)
        return notices
    except requests.RequestException as e:
        logging.error(f"Error fetching notices: {e}")
        return []
    except Exception as e:
        logging.exception("Error in get_school_notices")
        return []
        
# 새로운 공지사항 확인 및 알림 전송
async def check_for_new_notices():
    logging.info("Checking for new notices...")
    
    seen_announcements = load_seen_announcements()
    logging.info(f"Loaded seen announcements: {seen_announcements}")

    current_notices = get_school_notices()
    logging.info(f"Fetched current notices: {current_notices}")

    # URL 정규화 함수
    def normalize_url(url):
        parsed = urllib.parse.urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path}?{parsed.query}"

    # 기존 공지사항을 "제목 + 정규화된 URL" 기준으로 저장
    seen_titles_urls = { (title, normalize_url(url)) for title, url, _, _ in seen_announcements }

    # 새로운 공지사항 확인 (기존 데이터에 없는 것만 추가)
    new_notices = [
        notice for notice in current_notices
        if (notice[0], normalize_url(notice[1])) not in seen_titles_urls
    ]
    logging.info(f"DEBUG: New notices detected: {new_notices}")
    logging.info(f"New notices found: {new_notices}")

    if new_notices:
        for notice in new_notices:
            await send_notification(notice)
        seen_announcements.update(new_notices)
        save_seen_announcements(seen_announcements)
        logging.info(f"DEBUG: Updated seen announcements (after update): {seen_announcements}")
        return new_notices  # ✅ 올바른 위치로 이동

    else:
        logging.info("✅ 새로운 공지 없음")
        return []
        
# GitHub에 `announcements_seen.json` 푸시
def push_changes():
    try:
        subprocess.run(["git", "add", "announcements_seen.json"], check=True)
        subprocess.run(["git", "commit", "-m", "Update announcements_seen.json"], check=True)
        subprocess.run([
            "git", "push", "https://x-access-token:{}@github.com/Minhoooong/PKNU_Notice_Bot.git".format(os.environ["MY_PAT"])
        ], check=True)
        logging.info("✅ Successfully pushed changes to GitHub.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ ERROR: Failed to push changes to GitHub: {e}")

# 수동으로 새로운 공지사항 확인
@dp.message(Command("checknotices"))
async def manual_check_notices(message: types.Message):
    new_notices = await check_for_new_notices()
    if new_notices:
        await message.answer(f"📢 {len(new_notices)}개의 새로운 공지사항이 있습니다!")
    else:
        await message.answer("✅ 새로운 공지사항이 없습니다.")
        
# 알림 전송
async def send_notification(notice):
    title, href, department, date = notice
    message_text = f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
    message_text += f"<b>{html.escape(title)}</b>\n\n{html.escape(date)}"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

# 메시지 ID 저장을 위한 전역 변수

# /start 명령어 처리
@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="날짜 입력", callback_data="filter_date")],
        [InlineKeyboardButton(text="전체 공지사항", callback_data="all_notices")]
    ])
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

# 날짜 입력 요청 처리
@dp.callback_query(F.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요 (예: 01/31):")
    await state.set_state(FilterState.waiting_for_date)
    await callback.answer()

# 전체 공지사항 버튼 클릭 시 카테고리 선택 메뉴 표시
@dp.callback_query(F.data == "all_notices")
async def callback_all_notices(callback: CallbackQuery, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=category, callback_data=f"category_{code}")] for category, code in CATEGORY_CODES.items()
    ])
    await callback.message.answer("원하는 카테고리를 선택하세요:", reply_markup=keyboard)
    await state.set_state(FilterState.selecting_category)
    await callback.answer()

# 카테고리 선택 시 해당 공지사항 가져오기
@dp.callback_query(F.data.startswith("category_"))
async def callback_category_selection(callback: CallbackQuery, state: FSMContext):
    category_code = callback.data.split("_")[1]
    notices = get_school_notices(category_code)
    
    if not notices:
        await callback.message.answer("해당 카테고리의 공지사항이 없습니다.")
    else:
        for notice in notices[:7]:  # 최근 5개만 표시
            await send_notification(notice)
    
    await state.clear()
    await callback.answer()

# 날짜 입력 처리
@dp.message(F.text)
async def process_date_input(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    logging.info(f"Current FSM state raw: {current_state}")

    # 상태 비교 수정
    if current_state != "FilterState:waiting_for_date":
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

    notices = [n for n in get_school_notices() if parse_date(n[3]) == filter_date]

    if not notices:
        logging.info(f"No notices found for {full_date_str}")
        await message.answer(f"{input_text} 날짜의 공지사항이 없습니다.")
    else:
        for notice in notices:
            await send_notification(notice)
        await message.answer(f"{input_text} 날짜의 공지사항을 전송했습니다.", reply_markup=ReplyKeyboardRemove())

    logging.info("Clearing FSM state.")
    await state.clear()

async def main():
    logging.info("Starting bot polling...")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
