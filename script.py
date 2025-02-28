import logging
import asyncio
import sys
import aiohttp
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

# 한국어 문장 분할을 위한 kss 라이브러리
import kss

# 한국어 요약을 위해 transformers의 pipeline과 tokenizer 불러오기
from transformers import pipeline, AutoTokenizer, AutoModelForSeq2SeqLM

# SKT/Korean-T5 모델 (또는 파인튜닝된 모델) 사용
MODEL_NAME = "SKT/Korean-T5-base"  # 필요시 파인튜닝 모델 이름으로 변경
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME)
summarizer = pipeline("summarization", model=model, tokenizer=tokenizer)

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

# --- 날짜 파싱 함수 ---
def parse_date(date_str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}")
        return None

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
        push_changes()
    except Exception as e:
        logging.error(f"❌ Failed to save announcements_seen.json and push to GitHub: {e}")

# --- 공지사항 크롤링 ---
async def fetch_url(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as response:
            return await response.text()

async def get_school_notices(category=""):
    try:
        category_url = f"{URL}?cd={category}" if category else URL
        html_content = await fetch_url(category_url)
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

# --- 푸터 제거: 불필요한 정보 필터링 ---
def filter_footer(text):
    footer = ("대연캠퍼스(48513) 부산광역시 남구 용소로 45 TEL : 051-629-4114 FAX : 051-629-4114 " 
              "FAX : 051-629-5119 용당캠퍼스(48547) 부산광역시 남구 신선로 365 TEL : 051-629-4114 "
              "FAX : 051-629-6040 COPYRIGHT(C) 2021 PUKYONG NATIONAL UNIVERSITY. ALL RIGHTS RESERVED.")
    return text.replace(footer, "").strip()

# --- 텍스트 요약: SKT/Korean-T5 모델을 사용하여 문장 단위 청크로 분할 후 요약 ---
def summarize_text(text):
    try:
        if len(text.split()) < 50:
            return text
        
        text = filter_footer(text)
        sentences = kss.split_sentences(text)
        
        chunks = []
        current_chunk = ""
        for sentence in sentences:
            new_chunk = current_chunk + " " + sentence if current_chunk else sentence
            tokens = tokenizer.encode(new_chunk, truncation=False)
            if len(tokens) > 1024:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = sentence
            else:
                current_chunk = new_chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        
        logging.info(f"생성된 청크 개수: {len(chunks)}")
        
        summaries = []
        for chunk in chunks:
            result = summarizer(chunk, max_length=70, min_length=30, do_sample=False)
            summary_text = result[0].get('summary_text', '').strip()
            if not summary_text:
                summary_text = chunk
            summaries.append(summary_text)
        combined_summary = " ".join(summaries).strip()
        
        if not combined_summary:
            return text
        
        final_tokens = tokenizer.encode(combined_summary, truncation=False)
        if len(final_tokens) > 1024:
            final_result = summarizer(combined_summary, max_length=70, min_length=30, do_sample=False)
            final_summary = final_result[0].get('summary_text', '').strip()
            return final_summary if final_summary else combined_summary
        else:
            return combined_summary
    except Exception as e:
        logging.error(f"Summarization error: {e}")
        return text

async def extract_content(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                html_content = await response.text()

        soup = BeautifulSoup(html_content, 'html.parser')
        paragraphs = soup.find_all('p')
        raw_text = ' '.join([para.get_text() for para in paragraphs])
        summary_text = summarize_text(raw_text)

        # 이미지 URL 추출 (이미지 분석 없이 URL 그대로 반환)
        images = soup.find_all('img')
        image_urls = []
        for img in images:
            src = img.get('src')
            if src:
                if not src.startswith(('http://', 'https://')):
                    src = urllib.parse.urljoin(url, src)
                # is_valid_url 체크를 통해 유효한 이미지 URL만 사용
                if await is_valid_url(src):
                    image_urls.append(src)
        return summary_text, image_urls
    except Exception as e:
        logging.error(f"❌ Failed to fetch content from {url}: {e}")
        return "", []

async def is_valid_url(url):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.head(url, timeout=10) as response:
                return response.status == 200
    except Exception as e:
        logging.error(f"❌ Invalid image URL: {url}, error: {e}")
    return False

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
    message_text = f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
    message_text += f"<b>{html.escape(title)}</b>\n\n{html.escape(date)}\n\n"
    message_text += f"{html.escape(summary_text)}"
    # 이미지 URL이 있을 경우, 텍스트 뒤에 이미지 링크 추가 (여러 이미지일 경우 개행으로 구분)
    if image_urls:
        message_text += "\n\n[첨부 이미지]\n" + "\n".join(image_urls)
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=CHAT_ID, text=message_text, reply_markup=keyboard)

@dp.message(Command("start"))
async def start_command(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅날짜 입력", callback_data="filter_date"),
         InlineKeyboardButton(text="📢전체 공지사항", callback_data="all_notices")]
    ])
    await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)

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
    await callback.message.answer("원하는 카테고리를 선택하세요:", reply_markup=keyboard)
    await state.set_state(FilterState.selecting_category)
    await callback.answer()

@dp.callback_query(F.data.startswith("category_"))
async def callback_category_selection(callback: CallbackQuery, state: FSMContext):
    category_code = callback.data.split("_")[1]
    notices = await get_school_notices(category_code)
    if not notices:
        await callback.message.answer("해당 카테고리의 공지사항이 없습니다.")
    else:
        for notice in notices[:7]:
            await send_notification(notice)
    await state.clear()
    await callback.answer()

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
