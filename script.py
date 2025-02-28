import logging
import asyncio
import requests
import sys
import aiohttp
from bs4 import BeautifulSoup
from aiogram import Bot, Dispatcher, types, F
from aiogram.client.bot import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove, CallbackQuery
from aiogram.filters import Command
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from google.cloud import vision
import json
import os
import subprocess
import html
from datetime import datetime
import urllib.parse

# 환경 변수에서 JSON 파일 경로 가져오기
credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

# JSON 파일 검증
if not credentials_path or not os.path.exists(credentials_path):
    logging.error("❌ GOOGLE_APPLICATION_CREDENTIALS 파일이 존재하지 않습니다.")
    raise FileNotFoundError(f"환경 변수가 설정되지 않았거나 파일이 없습니다: {credentials_path}")

try:
    with open(credentials_path, "r", encoding="utf-8") as f:
        json.load(f)  # JSON 파싱 테스트
    logging.info("✅ GOOGLE_APPLICATION_CREDENTIALS JSON 파일이 정상적으로 로드되었습니다.")
except json.JSONDecodeError as e:
    logging.error(f"❌ JSON 파일 파싱 오류 발생: {e}")
    raise ValueError(f"JSON 파일 형식이 올바르지 않습니다: {credentials_path}")

# Google Vision API 클라이언트 초기화
client = vision.ImageAnnotatorClient()
logging.info("✅ Google Cloud Vision API 인증 성공!")

# 로깅 설정
logging.basicConfig(level=logging.INFO, 
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("logfile.log"),
                        logging.StreamHandler()
                    ])
# Initialize the Vision API client
client = vision.ImageAnnotatorClient()

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

# 봇 및 Dispatcher 초기화
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(bot=bot)

# 환경 변수 설정
credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
if credentials_path is None:
    logging.error("GOOGLE_APPLICATION_CREDENTIALS 환경 변수가 설정되지 않았습니다.")
else:
    with open("announcements_seen.json", "w") as f:
        f.write(credentials_path)

# 기존 코드 실행
credentials_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
if credentials_path:
    try:
        with open(credentials_path, "r", encoding="utf-8") as f:
            credentials_data = f.read()
        with open("announcements_seen.json", "w", encoding="utf-8") as f:
            f.write(credentials_data)
    except Exception as e:
        logging.error(f"❌ Failed to read credentials file: {e}")
else:
    logging.error("❌ GOOGLE_APPLICATION_CREDENTIALS 환경 변수가 설정되지 않았습니다.")

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

# JSON 파일 로드 (유연한 데이터 구조 처리)
def load_seen_announcements():
    try:
        with open("announcements_seen.json", "r", encoding="utf-8") as f:
            seen_data = json.load(f)
            return {(item[0], item[1]) if len(item) == 2 else tuple(item) for item in seen_data}
    except (FileNotFoundError, json.JSONDecodeError):
        return set()

# JSON 파일 저장 (중복 제거 후 리스트 변환)
def save_seen_announcements(seen):
    try:
        with open("announcements_seen.json", "w", encoding="utf-8") as f:
            json.dump([list(item) for item in seen], f, ensure_ascii=False, indent=4)
        push_changes()
    except Exception as e:
        logging.error(f"❌ Failed to save announcements_seen.json and push to GitHub: {e}")

# 공지사항 크롤링 (URL 처리 개선)
async def fetch_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as response:
            return await response.text()

async def get_school_notices(category=""):
    try:
        category_url = f"{URL}?cd={category}" if category else URL
        html_content = await fetch_url(category_url)  # ✅ 비동기 요청
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

# URL내 이미지 추출
async def extract_content(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                html_content = await response.text()

        soup = BeautifulSoup(html_content, 'html.parser')

        # Extract text
        paragraphs = soup.find_all('p')
        text = ' '.join([para.get_text() for para in paragraphs])

        # Extract images
        images = soup.find_all('img')
        image_urls = [img['src'] for img in images if 'src' in img.attrs]

        return text, image_urls
    except Exception as e:
        logging.error(f"❌ Failed to fetch content from {url}: {e}")
        return "", []

# 이미지 분석 처리
async def analyze_image(image_url):
    if not image_url.startswith(('http://', 'https://')):
        image_url = 'https://' + image_url.lstrip('/')

    logging.info(f"Analyzing image URL: {image_url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(image_url, timeout=10) as response:
                image_content = await response.read()  # ✅ 비동기적으로 이미지 다운로드

        image = vision.Image(content=image_content)

        # Text detection
        response = client.text_detection(image=image)
        texts = response.text_annotations
        text_analysis = [text.description for text in texts]

        # Label detection
        response = client.label_detection(image=image)
        labels = response.label_annotations
        label_analysis = [label.description for label in labels]

        return text_analysis, label_analysis
    except Exception as e:
        logging.error(f"❌ Failed to fetch image: {e}")
        return [], []
  
# 새로운 공지사항 확인 및 알림 전송
async def check_for_new_notices():
    logging.info("Checking for new notices...")
    
    seen_announcements = load_seen_announcements()
    logging.info(f"Loaded seen announcements: {seen_announcements}")

    current_notices = await get_school_notices()  # ✅ 비동기 함수 호출
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
        
# GitHub Push (PAT 예외 처리 추가)
def push_changes():
    try:
        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ GitHub PAT가 설정되지 않았습니다. Push를 생략합니다.")
            return
        
        os.environ["GIT_ASKPASS"] = "echo"
        os.environ["GIT_PASSWORD"] = pat

        subprocess.run(["git", "config", "--global", "credential.helper", "store"], check=True)
        subprocess.run(["git", "add", "announcements_seen.json"], check=True)
        subprocess.run(["git", "commit", "-m", "Update announcements_seen.json"], check=True)
        subprocess.run(["git", "push", "origin", "main"], check=True)

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
    
    # Extract text and images
    text, image_urls = extract_content(href)
    
    # Prepare message
    message_text = f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
    message_text += f"<b>{html.escape(title)}</b>\n\n{html.escape(date)}\n\n{text}"
    
    # Analyze images and append to summary
    for image_url in image_urls:
        text_analysis, label_analysis = analyze_image(image_url)
        if label_analysis:
            message_text += f"\n\n이미지 분석 결과: {', '.join(label_analysis)}"
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

# 메시지 ID 저장을 위한 전역 변수

# /start 명령어 처리
@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅날짜 입력", callback_data="filter_date"), InlineKeyboardButton(text="📢전체 공지사항", callback_data="all_notices")]
    ])
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

# 날짜 입력 요청 처리
@dp.callback_query(F.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("MM/DD 형식으로 날짜를 입력해 주세요. (예: 01/31)")
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

    notices = [n for n in get_school_notices() if parse_date(n[3]) == filter_date]

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
    """
    10분(600초) 동안만 봇을 실행한 후 자동 종료하는 함수.
    """
    try:
        logging.info("🚀 Starting bot polling for 10 minutes...")
        polling_task = asyncio.create_task(dp.start_polling(bot))  # 폴링을 별도 태스크로 실행
        await asyncio.sleep(600)  # 10분 대기
        logging.info("🛑 Stopping bot polling after 10 minutes...")
        polling_task.cancel()  # 폴링 태스크 취소
        await dp.stop_polling()  # Dispatcher 종료
    except Exception as e:
        logging.error(f"❌ Bot error: {e}")
    finally:
        await bot.session.close()  # 봇 세션 닫기
        logging.info("✅ Bot session closed.")

if __name__ == '__main__':
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        asyncio.run(run_bot())
    except RuntimeError:
        logging.error("❌ asyncio 이벤트 루프 실행 중 오류 발생.")
