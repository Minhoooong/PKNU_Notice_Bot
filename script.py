import asyncio
import hashlib
import html
import json
import logging
import os
import subprocess
import sys
import urllib.parse
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

# 환경 변수 / 토큰 / 상수
aclient = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GROUP_CHAT_ID = os.environ.get('GROUP_CHAT_ID')
REGISTRATION_CODE = os.environ.get('REGISTRATION_CODE')
URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
CACHE_FILE = "announcements_seen.json"
WHITELIST_FILE = "whitelist.json"
PROGRAM_CACHE_FILE = "programs_seen.json"  # 비교과 프로그램 캐시 파일

# 새 상수: 비교과 프로그램 페이지 URL (사이트의 필터 기능 활용)
PROGRAM_URL = "https://whalebe.pknu.ac.kr/main/65"

CATEGORY_CODES = {
    "전체": "",
    "공지사항": "10001",
    "비교과 안내": "10002",
    "학사 안내": "10003",
    "등록/장학": "10004",
    "초빙/채용": "10007"
}

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("logfile.log"), logging.StreamHandler()]
)

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(bot=bot)

class FilterState(StatesGroup):
    waiting_for_date = State()
    selecting_category = State()

# --------------------- 화이트리스트 관련 함수 ---------------------
def load_whitelist() -> dict:
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("users", {})
        except Exception as e:
            logging.error(f"Whitelist 로드 오류: {e}", exc_info=True)
    return {}

def save_whitelist(whitelist: dict) -> None:
    try:
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"users": whitelist}, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Whitelist 저장 오류: {e}", exc_info=True)

def push_whitelist_changes() -> None:
    try:
        subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "공지봇"], check=True)
        subprocess.run(["git", "add", WHITELIST_FILE], check=True)
        commit_message = "Update whitelist.json with new registrations or filter changes"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ MY_PAT 환경 변수가 설정되지 않았습니다.")
            return
        remote_url = f"https://{pat}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        logging.info("✅ whitelist.json 파일이 저장소에 커밋되었습니다.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ whitelist.json 커밋 오류: {e}", exc_info=True)

ALLOWED_USERS = load_whitelist()  # 형식: { "123456789": {"filters": {"옵션": bool, ...}}, ... }
logging.info(f"현재 화이트리스트: {ALLOWED_USERS}")

# --------------------- 캐시 관련 함수 ---------------------
def generate_cache_key(title: str, href: str) -> str:
    normalized = f"{title.strip().lower()}::{href.strip()}"
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

def load_cache() -> dict:
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logging.error(f"❌ 캐시 로드 오류: {e}", exc_info=True)
            return {}
    return {}

def save_cache(data: dict) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"❌ 캐시 저장 오류: {e}", exc_info=True)

def push_cache_changes() -> None:
    try:
        subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "공지봇"], check=True)
        subprocess.run(["git", "add", CACHE_FILE], check=True)
        commit_message = "Update announcements_seen.json with new notices"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ MY_PAT 환경 변수가 설정되지 않았습니다.")
            return
        remote_url = f"https://{pat}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        logging.info("✅ 캐시 파일이 저장소에 커밋되었습니다.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 캐시 파일 커밋 오류: {e}", exc_info=True)

async def is_new_announcement(title: str, href: str) -> bool:
    cache = load_cache()
    key = generate_cache_key(title, href)
    if key in cache:
        return False
    cache[key] = True
    save_cache(cache)
    return True

def load_program_cache() -> dict:
    if os.path.exists(PROGRAM_CACHE_FILE):
        try:
            with open(PROGRAM_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logging.error(f"❌ 프로그램 캐시 로드 오류: {e}", exc_info=True)
            return {}
    return {}

def save_program_cache(data: dict) -> None:
    try:
        with open(PROGRAM_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"❌ 프로그램 캐시 저장 오류: {e}", exc_info=True)

def push_program_cache_changes() -> None:
    try:
        subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "공지봇"], check=True)
        subprocess.run(["git", "add", PROGRAM_CACHE_FILE], check=True)
        commit_message = "Update programs_seen.json with new programs"
        subprocess.run(["git", "commit", "-m", commit_message], check=True)
        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ MY_PAT 환경 변수가 설정되지 않았습니다.")
            return
        remote_url = f"https://{pat}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        logging.info("✅ 프로그램 캐시 파일이 저장소에 커밋되었습니다.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ 프로그램 캐시 파일 커밋 오류: {e}", exc_info=True)

def is_new_program(title: str, href: str) -> bool:
    cache = load_program_cache()
    key = generate_cache_key(title, href)
    if key in cache:
        return False
    cache[key] = True
    save_program_cache(cache)
    return True

# --------------------- 공통 함수 ---------------------
def parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}", exc_info=True)
        return None

async def fetch_url(url: str) -> str:
    try:
        logging.debug(f"요청 시작: {url}")
        timeout_duration = 30
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout_duration) as response:
                if response.status != 200:
                    logging.error(f"❌ HTTP 요청 실패 ({response.status}): {url}")
                    return None
                text = await response.text()
                logging.debug(f"요청 성공: {url} - 응답 길이: {len(text)}")
                return text
    except asyncio.TimeoutError:
        logging.error(f"❌ 타임아웃 오류 발생 (타임아웃: {timeout_duration}초): {url}")
        return None
    except Exception as e:
        logging.error(f"❌ URL 요청 오류: {url}, {e}", exc_info=True)
        return None

async def get_school_notices(category: str = "") -> list:
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

async def summarize_text(text: str) -> str:
    if not text or not text.strip():
        return "요약할 수 없는 공지입니다."
    prompt = (
        f"아래의 텍스트를 3~5 문장으로 간결하고 명확하게 요약해 주세요. "
        "각 핵심 사항은 별도의 문단이나 항목으로 구분하며, 불필요한 중복은 제거하고, "
        "강조 시 <b> 태그만 사용하세요.:\n\n"
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
        logging.error(f"❌ OpenAI API 요약 오류: {e}", exc_info=True)
        return "요약할 수 없는 공지입니다."

async def extract_content(url: str) -> tuple:
    try:
        html_content = await fetch_url(url)
        if not html_content or not html_content.strip():
            logging.error(f"❌ Failed to fetch content: {url}")
            return ("페이지를 불러올 수 없습니다.", [])
        soup = BeautifulSoup(html_content, 'html.parser')
        container = soup.find("div", class_="bdvTxt_wrap") or soup
        paragraphs = container.find_all('p')
        if not paragraphs:
            logging.error(f"❌ No text content found in {url}")
            return ("", [])
        raw_text = ' '.join(para.get_text(separator=" ", strip=True) for para in paragraphs)
        summary_text = await summarize_text(raw_text) if raw_text.strip() else ""
        images = [urllib.parse.urljoin(url, img['src'])
                  for img in container.find_all('img')
                  if "/upload/" in img.get('src', '')]
        return (summary_text, images)
    except Exception as e:
        logging.error(f"❌ Exception in extract_content for URL {url}: {e}", exc_info=True)
        return ("처리 중 오류가 발생했습니다.", [])

# --------------------- 프로그램(비교과) 관련 함수 ---------------------
def build_filter_url(user_filters: dict) -> str:
    base_params = {
        "pageIndex": 1,
        "action": "",
        "order": 0,
        "filterOF": 1,
        "all": 0,
        "intr": 0,
        "ridx": 0,
        "newAppr": 0,
        "rstOk": 0,
        "recvYn": 0,
        "aIridx": 0,
        "clsf": "",    # 학생 학습역량 강화
        "type": [],    # 프로그램 유형 (다중값: list)
        "diag": "",
        "oneYy": 0,
        "twoYy": 0,
        "trdYy": 0,
        "std1": 0,
        "std2": 0,
        "std3": 0,
        "std4": 0,
        "deptCd": "",
        "searchKeyword": ""
    }
    filter_mapping = {
        "학생 학습역량 강화": ("clsf", "'A01'", False),
        "1학년": ("std1", 1, False),
        "2학년": ("std2", 1, False),
        "3학년": ("std3", 1, False),
        "4학년": ("std4", 1, False),
        "멘토링": ("type", "멘토링", True),
        "특강": ("type", "특강", True),
        "워크숍": ("type", "워크숍", True),
        "세미나": ("type", "세미나", True),
        "캠프": ("type", "캠프", True),
        "경진대회": ("type", "경진대회", True),
    }
    for key, selected in user_filters.items():
        if selected and key in filter_mapping:
            param_key, param_value, multi = filter_mapping[key]
            if multi:
                base_params[param_key].append(param_value)
            else:
                base_params[param_key] = param_value
    url = PROGRAM_URL + "?" + urllib.parse.urlencode(base_params, doseq=True)
    logging.info(f"생성된 필터 URL: {url}")
    return url

async def get_programs(user_filters: dict = None) -> list:
    if user_filters is None:
        url = PROGRAM_URL
    else:
        url = build_filter_url(user_filters)
    html_content = await fetch_url(url)
    if html_content is None:
        logging.error("❌ 필터 적용된 프로그램 페이지를 불러올 수 없습니다.")
        return []
    soup = BeautifulSoup(html_content, 'html.parser')
    programs = []
    # 'ul.list > li' 선택자로 프로그램 항목을 가져옵니다.
    program_items = soup.select("ul.list > li")
    if not program_items:
        logging.debug("ul.list > li 선택자로 항목을 찾지 못했습니다.")
    for item in program_items:
        # 제목: <div class="subject"> 또는 <span class="tit">
        title_elem = item.select_one("div.subject") or item.select_one("span.tit")
        # 날짜: <span class="date">
        date_elem = item.select_one("span.date")
        # 링크: <a> 태그
        link_elem = item.select_one("a[href]")
        if title_elem and date_elem and link_elem:
            title = title_elem.get_text(strip=True)
            date_str = date_elem.get_text(strip=True)
            href = link_elem.get("href")
            if href and href.startswith("/"):
                href = "https://whalebe.pknu.ac.kr" + href
            programs.append({
                "title": title,
                "href": href,
                "date": date_str
            })
    programs.sort(key=lambda x: parse_date(x["date"]) or datetime.min, reverse=True)
    return programs

async def send_program_notification(program: dict, target_chat_id: str) -> None:
    title = program["title"]
    href = program["href"]
    date_ = program["date"]
    summary_text, image_urls = await extract_content(href)
    safe_summary = summary_text or ""
    message_text = (
        f"[비교과 프로그램 업데이트]\n\n"
        f"<b>{html.escape(title)}</b>\n"
        f"날짜: {html.escape(date_)}\n"
        "______________________________________________\n"
        f"{safe_summary}\n\n"
    )
    if image_urls:
        message_text += "\n".join(image_urls) + "\n\n"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=target_chat_id, text=message_text, reply_markup=keyboard)

async def check_for_new_programs(target_chat_id: str) -> list:
    logging.info("Checking for new programs...")
    seen_programs = load_program_cache()
    current_programs = await get_programs()
    new_programs = []
    for program in current_programs:
        key = generate_cache_key(program["title"], program["href"])
        if key not in seen_programs:
            new_programs.append(program)
    if new_programs:
        for program in new_programs:
            await send_program_notification(program, target_chat_id=target_chat_id)
            key = generate_cache_key(program["title"], program["href"])
            seen_programs[key] = True
        save_program_cache(seen_programs)
        push_program_cache_changes()
    return new_programs

# --------------------- 개인 채팅: /start 명령어 ---------------------
@dp.message(Command("start"))
async def start_command(message: types.Message) -> None:
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        await message.answer("죄송합니다. 이 봇은 사용 권한이 없습니다.\n등록하려면 /register [숫자 코드]를 입력해 주세요.")
        return
    if message.chat.type == "private":
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="공지사항", callback_data="notice_menu"),
             InlineKeyboardButton(text="프로그램", callback_data="compare_programs")]
        ])
        await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)
    else:
        await message.answer("이 그룹 채팅은 자동 알림용입니다.")

# "공지사항" 버튼 클릭 시 옵션 제공
@dp.callback_query(lambda c: c.data == "notice_menu")
async def notice_menu_handler(callback: CallbackQuery, state: FSMContext):
    # 즉시 응답하여 오래된 쿼리 문제 방지
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅날짜 입력", callback_data="filter_date"),
         InlineKeyboardButton(text="📢전체 공지사항", callback_data="all_notices")]
    ])
    await callback.message.edit_text("공지사항 옵션을 선택하세요:", reply_markup=keyboard)

# --------------------- 비교과(프로그램) 옵션 버튼 ---------------------
@dp.callback_query(lambda c: c.data == "compare_programs")
async def compare_programs_handler(callback: CallbackQuery):
    await callback.answer()  # 즉시 응답
    # 두 버튼을 한 행에 배치
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="나만의 프로그램", callback_data="my_programs"),
         InlineKeyboardButton(text="키워드 검색", callback_data="keyword_search")]
    ])
    await callback.message.edit_text("비교과 프로그램 옵션을 선택하세요.", reply_markup=keyboard)

# "나만의 프로그램" 버튼 클릭 시 필터 선택 UI 또는 결과 업데이트
@dp.callback_query(lambda c: c.data == "my_programs")
async def my_programs_handler(callback: CallbackQuery):
    await callback.answer()  # 즉시 응답
    chat_id = callback.message.chat.id
    user_id_str = str(chat_id)
    if user_id_str not in ALLOWED_USERS:
        await callback.message.edit_text("등록된 사용자가 아닙니다. /register 명령어로 등록해 주세요.")
        return
    user_filter = ALLOWED_USERS[user_id_str].get("filters", {})
    if not any(user_filter.values()):
        keyboard = get_program_filter_keyboard(chat_id)
        await callback.message.edit_text("현재 필터가 설정되어 있지 않습니다. 아래에서 필터를 설정해 주세요:", reply_markup=keyboard)
        return
    programs = await get_programs(user_filter)
    if not programs:
        await callback.message.edit_text("선택하신 필터에 해당하는 프로그램이 없습니다.")
    else:
        text = "선택하신 필터에 해당하는 프로그램:\n"
        for program in programs:
            text += f"- {program['title']} ({program['date']})\n"
        await callback.message.edit_text(text)
        
# 프로그램 필터 설정 UI: 그룹화된 버튼 배열
def get_program_filter_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    group1 = ["학생 학습역량 강화"]
    group2 = ["1학년", "2학년", "3학년", "4학년"]
    group3 = ["멘토링", "특강", "워크숍", "세미나", "캠프", "경진대회"]
    user_id_str = str(chat_id)
    if user_id_str not in ALLOWED_USERS:
        ALLOWED_USERS[user_id_str] = {"filters": {}}
    default_options = group1 + group2 + group3
    if "filters" not in ALLOWED_USERS[user_id_str]:
        ALLOWED_USERS[user_id_str]["filters"] = {opt: False for opt in default_options}
    current = ALLOWED_USERS[user_id_str].get("filters", {opt: False for opt in default_options})
    rows = []
    # 그룹1 row
    row1 = [InlineKeyboardButton(text=f"{'✅' if current.get(opt, False) else ''} {opt}".strip(), callback_data=f"toggle_program_{opt}") for opt in group1]
    rows.append(row1)
    # 그룹2 row
    row2 = [InlineKeyboardButton(text=f"{'✅' if current.get(opt, False) else ''} {opt}".strip(), callback_data=f"toggle_program_{opt}") for opt in group2]
    rows.append(row2)
    # 그룹3: 3개씩
    group3_buttons = [InlineKeyboardButton(text=f"{'✅' if current.get(opt, False) else ''} {opt}".strip(), callback_data=f"toggle_program_{opt}") for opt in group3]
    for i in range(0, len(group3_buttons), 3):
        rows.append(group3_buttons[i:i+3])
    # 마지막 행: 선택 완료 버튼
    rows.append([InlineKeyboardButton(text="선택 완료", callback_data="filter_done_program")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# 필터 토글: 옵션 선택/해제 후 UI 업데이트
@dp.callback_query(lambda c: c.data.startswith("toggle_program_"))
async def toggle_program_filter(callback: CallbackQuery):
    await callback.answer()  # 즉시 응답
    chat_id = callback.message.chat.id
    user_id_str = str(chat_id)
    option = callback.data.split("toggle_program_")[1]
    if user_id_str not in ALLOWED_USERS:
        ALLOWED_USERS[user_id_str] = {"filters": {option: True}}
    else:
        filters = ALLOWED_USERS[user_id_str].get("filters", {})
        filters[option] = not filters.get(option, False)
        ALLOWED_USERS[user_id_str]["filters"] = filters
    save_whitelist(ALLOWED_USERS)
    push_whitelist_changes()
    keyboard = get_program_filter_keyboard(chat_id)
    await callback.message.edit_text("필터를 선택하세요:", reply_markup=keyboard)

# 필터 설정 완료: 선택한 필터 표시 및 메시지 업데이트
@dp.callback_query(lambda c: c.data == "filter_done_program")
async def filter_done_program_handler(callback: CallbackQuery):
    await callback.answer()  # 즉시 응답
    chat_id = callback.message.chat.id
    user_id_str = str(chat_id)
    user_filter = ALLOWED_USERS[user_id_str].get("filters", {})
    selected = [opt for opt, chosen in user_filter.items() if chosen]
    await callback.message.edit_text(f"선택한 필터: {', '.join(selected) if selected else '없음'}")
    
# 키워드 검색: 일반 메시지로 결과 업데이트 (사용자가 보낸 메시지는 편집할 수 없으므로 answer() 사용)
@dp.callback_query(lambda c: c.data == "keyword_search")
async def keyword_search_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()  # 즉시 응답
    await callback.message.edit_text("검색할 키워드를 입력해 주세요:")
    await state.set_state("keyword_search")

@dp.message(lambda message: bool(message.text) and not message.text.startswith("/"))
async def process_keyword_search(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state == "keyword_search":
        keyword = message.text.strip()
        await state.clear()
        await message.answer(f"'{keyword}' 키워드에 해당하는 프로그램을 검색 중입니다...")
        # 실제 키워드 검색 로직 추가 가능

# --------------------- /register 및 기타 명령어 ---------------------
@dp.message(Command("register"))
async def register_command(message: types.Message) -> None:
    logging.debug(f"Register command invoked by {message.chat.id}: {message.text}")
    if not message.text:
        await message.answer("등록하려면 '/register [숫자 코드]'를 입력해 주세요.")
        logging.debug("No text provided in registration command.")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("등록하려면 '/register [숫자 코드]'를 입력해 주세요.")
        logging.debug("Registration command missing code part.")
        return
    code = parts[1].strip()
    user_id_str = str(message.chat.id)
    
    if REGISTRATION_CODE is None:
        logging.error("REGISTRATION_CODE 환경 변수가 설정되지 않았습니다.")
        await message.answer("등록 시스템에 문제가 발생했습니다. 관리자에게 문의하세요.")
        return

    if code == REGISTRATION_CODE:
        if user_id_str in ALLOWED_USERS:
            await message.answer("이미 등록되어 있습니다.")
            logging.debug(f"User {user_id_str} attempted re-registration.")
        else:
            default_filters = {
                "학생 학습역량 강화": False, "1학년": False, "2학년": False, "3학년": False, "4학년": False,
                "멘토링": False, "특강": False, "워크숍": False, "세미나": False, "캠프": False, "경진대회": False
            }
            ALLOWED_USERS[user_id_str] = {"filters": default_filters}
            save_whitelist(ALLOWED_USERS)
            push_whitelist_changes()
            await message.answer("등록 성공! 이제 개인 채팅 기능을 이용할 수 있습니다.")
            logging.info(f"새 화이트리스트 등록: {user_id_str}")
    else:
        await message.answer("잘못된 코드입니다. '/register [숫자 코드]' 형식으로 정확히 입력해 주세요.")
        logging.debug(f"User {user_id_str} provided invalid registration code: {code}")

@dp.message(Command("checknotices"))
async def manual_check_notices(message: types.Message) -> None:
    user_id_str = str(message.chat.id)
    if message.chat.type != "private":
        return
    if user_id_str not in ALLOWED_USERS:
        await message.answer("접근 권한이 없습니다.")
        return
    new_notices = await check_for_new_notices(target_chat_id=message.chat.id)
    if new_notices:
        await message.answer(f"📢 {len(new_notices)}개의 새로운 공지사항이 전송되었습니다!")
    else:
        await message.answer("✅ 새로운 공지사항이 없습니다.")

@dp.callback_query(lambda c: c.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()  # 즉시 응답
    await callback.message.edit_text("MM/DD 형식으로 날짜를 입력해 주세요. (예: 01/31)")
    await state.set_state(FilterState.waiting_for_date)

@dp.callback_query(lambda c: c.data == "all_notices")
async def callback_all_notices(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()  # 즉시 응답
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=category, callback_data=f"category_{code}")]
        for category, code in CATEGORY_CODES.items()
    ])
    await callback.message.edit_text("원하는 카테고리를 선택하세요:", reply_markup=keyboard)
    await state.set_state(FilterState.selecting_category)

@dp.callback_query(lambda c: c.data.startswith("category_"))
async def callback_category_selection(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()  # 즉시 응답
    category_code = callback.data.split("_")[1]
    notices = await get_school_notices(category_code)
    if not notices:
        await callback.message.edit_text("해당 카테고리의 공지사항이 없습니다.")
    else:
        text = "해당 카테고리 공지사항:\n"
        for notice in notices[:7]:
            text += f"- {notice[0]} ({notice[3]})\n"
        await callback.message.edit_text(text)
    await state.clear()

@dp.message(lambda message: bool(message.text) and not message.text.startswith("/"))
async def process_date_input(message: types.Message, state: FSMContext) -> None:
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        await message.answer("접근 권한이 없습니다.")
        return
    current_state = await state.get_state()
    if current_state != FilterState.waiting_for_date.state:
        return
    input_text = message.text.strip()
    current_year = datetime.now().year
    full_date_str = f"{current_year}-{input_text.replace('/', '-')}"
    filter_date = parse_date(full_date_str)
    if filter_date is None:
        await message.answer("날짜 형식이 올바르지 않습니다. MM/DD 형식으로 다시 입력해 주세요.")
        return
    all_notices = await get_school_notices()
    filtered_notices = [n for n in all_notices if parse_date(n[3]) == filter_date]
    if not filtered_notices:
        await message.answer(f"📢 {input_text} 날짜에 해당하는 공지사항이 없습니다.")
    else:
        text = f"📢 {input_text}의 공지사항:\n"
        for notice in filtered_notices:
            text += f"- {notice[0]} ({notice[3]})\n"
        await message.answer(text, reply_markup=ReplyKeyboardRemove())
    await state.clear()

@dp.message()
async def catch_all(message: types.Message):
    logging.debug(f"Catch-all handler received message: {message.text}")

# --------------------- 그룹 채팅: 새 공지사항 및 프로그램 자동 전송 ---------------------
async def check_for_new_notices(target_chat_id: str = None) -> list:
    if target_chat_id is None:
        target_chat_id = GROUP_CHAT_ID
    logging.info("Checking for new notices...")
    seen_announcements = load_cache()
    current_notices = await get_school_notices()
    new_notices = []
    for title, href, department, date_ in current_notices:
        key = generate_cache_key(title, href)
        if key not in seen_announcements:
            new_notices.append((title, href, department, date_))
    if new_notices:
        for notice in new_notices:
            await send_notification(notice, target_chat_id=target_chat_id)
            key = generate_cache_key(notice[0], notice[1])
            seen_announcements[key] = True
        save_cache(seen_announcements)
        push_cache_changes()
    return new_notices

async def send_notification(notice: tuple, target_chat_id: str) -> None:
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
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=target_chat_id, text=message_text, reply_markup=keyboard)

async def run_bot() -> None:
    await check_for_new_notices()
    await check_for_new_programs(GROUP_CHAT_ID)
    try:
        logging.info("🚀 Starting bot polling for 10 minutes...")
        polling_task = asyncio.create_task(dp.start_polling(bot))
        await asyncio.sleep(600)
        logging.info("🛑 Stopping bot polling after 10 minutes...")
        polling_task.cancel()
        await dp.stop_polling()
    except Exception as e:
        logging.error(f"❌ Bot error: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logging.info("✅ Bot session closed.")

if __name__ == '__main__':
    if sys.platform.startswith("win"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(run_bot())
    except Exception as e:
        logging.error(f"❌ Bot terminated with error: {e}", exc_info=True)
        
        async def notify_crash():
            try:
                new_bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
                await new_bot.send_message(GROUP_CHAT_ID, f"봇이 오류로 종료되었습니다:\n{e}\n\n재실행 해주세요.")
                await new_bot.session.close()
            except Exception as notify_error:
                logging.error(f"❌ 알림 전송 실패: {notify_error}", exc_info=True)
        
        asyncio.run(notify_crash())
