################################################################################
#                               필요한 라이브러리 Import                             #
################################################################################
import asyncio
import hashlib
import html
import json
import logging
import os
import subprocess
import sys
import re
import urllib.parse
from datetime import datetime
from logging.handlers import RotatingFileHandler

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

################################################################################
#                               환경 변수 / 토큰 / 상수 설정                          #
################################################################################
aclient = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GROUP_CHAT_ID = os.environ.get('GROUP_CHAT_ID')
REGISTRATION_CODE = os.environ.get('REGISTRATION_CODE')

# ▼ 추가: PKNU AI 비교과 로그인을 위한 학번/비밀번호
PKNU_USERNAME = os.environ.get('PKNU_USERNAME')
PKNU_PASSWORD = os.environ.get('PKNU_PASSWORD')


URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
CACHE_FILE = "announcements_seen.json"
WHITELIST_FILE = "whitelist.json"

# ▼ 추가: PKNU AI 비교과 시스템
PKNUAI_PROGRAM_URL = "https://pknuai.pknu.ac.kr/web/nonSbjt/program.do?mId=216&order=3"
PKNUAI_BASE_URL = "https://pknuai.pknu.ac.kr"
PKNUAI_PROGRAM_CACHE_FILE = "programs_seen.json"


CATEGORY_CODES = {
    "전체": "", "공지사항": "10001", "비교과 안내": "10002", "학사 안내": "10003",
    "등록/장학": "10004", "초빙/채용": "10007"
}

################################################################################
#                                   로깅 설정                                  #
################################################################################
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logfile.log", encoding="utf-8"),
        logging.StreamHandler(),
        RotatingFileHandler("logfile.log", maxBytes=10**6, backupCount=3)
    ]
)

################################################################################
#                                 AIogram 설정                                #
################################################################################
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(bot=bot)

################################################################################
#                                  상태머신 정의                                 #
################################################################################
class FilterState(StatesGroup):
    waiting_for_date = State()
    selecting_category = State()

class KeywordSearchState(StatesGroup):
    waiting_for_keyword = State()

################################################################################
#                                화이트리스트 관련 함수                            #
################################################################################
def load_whitelist() -> dict:
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("users", {})
        except Exception as e:
            logging.error(f"Whitelist 로드 오류: {e}", exc_info=True)
    return {}

def save_whitelist(whitelist: dict) -> None:
    try:
        with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
            json.dump({"users": whitelist}, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"Whitelist 저장 오류: {e}", exc_info=True)

def push_file_changes(file_path: str, commit_message: str) -> None:
    """Git 저장소에 지정된 파일을 추가, 커밋, 푸시하는 범용 함수"""
    try:
        subprocess.run(["git", "config", "user.email", "bot@example.com"], check=True)
        subprocess.run(["git", "config", "user.name", "공지봇"], check=True)
        subprocess.run(["git", "add", file_path], check=True)
        
        result = subprocess.run(["git", "commit", "--allow-empty", "-m", commit_message], capture_output=True, text=True)
        if "nothing to commit" in result.stdout:
            logging.info(f"변경 사항이 없어 {file_path} 파일을 커밋하지 않았습니다.")
            return

        pat = os.environ.get("MY_PAT")
        if not pat:
            logging.error("❌ MY_PAT 환경 변수가 설정되지 않았습니다.")
            return
            
        remote_url = f"https://{pat}@github.com/Minhoooong/PKNU_Notice_Bot.git"
        subprocess.run(["git", "push", remote_url, "HEAD:main"], check=True)
        logging.info(f"✅ {file_path} 파일이 저장소에 커밋되었습니다.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ {file_path} 파일 커밋 오류: {e.stderr}", exc_info=True)
    except Exception as e:
        logging.error(f"❌ 파일 푸시 중 알 수 없는 오류 발생: {e}", exc_info=True)


ALLOWED_USERS = load_whitelist()
logging.info(f"현재 화이트리스트: {list(ALLOWED_USERS.keys())}")

################################################################################
#                             공지사항 / 프로그램 캐시 관련 함수                        #
################################################################################
def generate_cache_key(title: str, href: str) -> str:
    normalized = f"{title.strip().lower()}::{href.strip()}"
    return hashlib.md5(normalized.encode('utf-8')).hexdigest()

def load_json_file(file_path: str) -> dict:
    """범용 JSON 로더"""
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logging.error(f"❌ {file_path} 파일 로드 오류: {e}", exc_info=True)
    return {}

def save_json_file(data: dict, file_path: str) -> None:
    """범용 JSON 저장"""
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"❌ {file_path} 파일 저장 오류: {e}", exc_info=True)

# 각 캐시 파일에 대한 별도의 로드/저장/푸시 함수
load_cache = lambda: load_json_file(CACHE_FILE)
save_cache = lambda data: save_json_file(data, CACHE_FILE)
push_cache_changes = lambda: push_file_changes(CACHE_FILE, "Update announcements_seen.json")

load_program_cache = lambda: load_json_file(PKNUAI_PROGRAM_CACHE_FILE)
save_program_cache = lambda data: save_pknuai_program_cache(data)
push_program_cache_changes = lambda: push_pknuai_program_cache_changes()

# ▼ 추가: PKNU AI 프로그램 캐시 함수
load_pknuai_program_cache = lambda: load_json_file(PKNUAI_PROGRAM_CACHE_FILE)
save_pknuai_program_cache = lambda data: save_json_file(data, PKNUAI_PROGRAM_CACHE_FILE)
push_pknuai_program_cache_changes = lambda: push_file_changes(PKNUAI_PROGRAM_CACHE_FILE, "Update pknuai_programs_seen.json")

################################################################################
#                         웹페이지 크롤링 함수 (Playwright / aiohttp)                    #
################################################################################

# 기존 fetch_program_html 함수를 지우고 아래 코드로 교체하세요.
async def fetch_program_html(keyword: str = None, filters: dict = None) -> str:
    """PKNU AI 비교과 페이지를 로그인, 검색, 필터링하여 HTML을 가져오는 함수 (로그인 로직 수정)**"""
    if not PKNU_USERNAME or not PKNU_PASSWORD:
        logging.error("❌ PKNU_USERNAME 또는 PKNU_PASSWORD 환경 변수가 설정되지 않았습니다.")
        return ""

    page = None
    logging.info(f"🚀 Playwright 작업 시작 (검색어: {keyword}, 필터: {filters})")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page()

            # 1. 통합로그인(SSO) 페이지로 직접 이동
            sso_url = "https://sso.pknu.ac.kr/login?service=https%3A%2F%2Fpknuai.pknu.ac.kr%2Fsso%2Findex.jsp"
            await page.goto(sso_url, wait_until="domcontentloaded", timeout=30000)
            logging.info(f"1. SSO 로그인 페이지 접속 완료: {page.url}")

            # 2. 아이디와 비밀번호 입력 후 로그인
            await page.fill("input#userId", PKNU_USERNAME)
            await page.fill("input#userpw", PKNU_PASSWORD)
            await page.screenshot(path="debug_sso_login_page.png")
            await page.click('button[type="submit"]')
            logging.info("2. 로그인 정보 입력 및 클릭 완료.")

            # 3. 로그인이 완료되고 최종 목적지인 비교과 페이지로 이동할 때까지 기다림
            await page.wait_for_url(PKNUAI_PROGRAM_URL, wait_until="networkidle", timeout=30000)
            logging.info(f"3. 비교과 프로그램 페이지로 성공적으로 이동: {page.url}")
            await page.screenshot(path="debug_final_page.png")

            # 6. 필터 적용
            if filters and any(filters.values()):
                logging.info(f"6. 필터를 적용합니다: {filters}")
                for filter_name, is_selected in filters.items():
                    if is_selected:
                        input_id = PROGRAM_FILTER_MAP.get(filter_name)
                        if input_id:
                            await page.click(f"label[for='{input_id}']")
                await page.wait_for_timeout(2000)
                await page.screenshot(path="debug_screenshot_4_after_filter.png")

            # 7. 키워드 검색
            if keyword:
                logging.info(f"7. 키워드 '{keyword}'로 검색합니다.")
                await page.fill("input#searchVal", keyword)
                await page.click("button.btn.btn-outline-primary.btn_search")
            
            # 검색/필터 후 결과 로딩 대기
            if keyword or (filters and any(filters.values())):
                 logging.info("8. 검색/필터 결과 로딩을 기다립니다.")
                 await page.wait_for_load_state("networkidle", timeout=20000)
                 await page.screenshot(path="debug_screenshot_5_after_search.png")

            content = await page.content()
            await browser.close()
            logging.info("✅ Playwright 크롤링 성공")
            return content

    except Exception as e:
        logging.error(f"❌ Playwright 크롤링 중 오류 발생: {e}", exc_info=True)
        if page and not page.is_closed():
            # 오류 발생 시 스크린샷과 HTML을 파일로 저장하여 원인 파악
            await page.screenshot(path="debug_error_screenshot.png")
            with open("debug_error_page.html", "w", encoding="utf-8") as f:
                f.write(await page.content())
            logging.error("오류 당시의 화면을 debug_error_screenshot.png 와 debug_error_page.html 로 저장했습니다.")
        return ""

async def fetch_url(url: str) -> str:
    """정적 페이지(학교 공지사항) 크롤링 함수"""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
            async with session.get(url) as response:
                response.raise_for_status()
                return await response.text()
    except Exception as e:
        logging.error(f"❌ URL 요청 오류: {url}, {e}", exc_info=True)
        return None

################################################################################
#                                 콘텐츠 파싱 및 요약 함수                           #
################################################################################
async def get_school_notices(category: str = "") -> list:
    # ... 기존 공지사항 파싱 코드 (변경 없음)
    try:
        category_url = f"{URL}?cd={category}" if category else URL
        html_content = await fetch_url(category_url)
        if not html_content: return []
        soup = BeautifulSoup(html_content, 'html.parser')
        notices = []
        for tr in soup.select("tbody > tr"):
            if "글이 없습니다" in tr.text: continue
            title_td = tr.select_one("td.bdlTitle a")
            if not title_td: continue
            title = title_td.get_text(strip=True)
            href = title_td.get("href")
            if href.startswith("/"): href = BASE_URL + href
            elif href.startswith("?"): href = f"{BASE_URL}/main/163{href}"
            department = tr.select_one("td.bdlUser").get_text(strip=True)
            date_ = tr.select_one("td.bdlDate").get_text(strip=True)
            notices.append((title, href, department, date_))
        notices.sort(key=lambda x: datetime.strptime(x[3], "%Y.%m.%d") if re.match(r'\d{4}\.\d{2}\.\d{2}', x[3]) else datetime.min, reverse=True)
        return notices
    except Exception as e:
        logging.exception(f"❌ 공지사항 파싱 중 오류 발생: {e}")
        return []

async def summarize_text(text: str) -> str:
    # ... 기존 요약 코드 (변경 없음)
    if not text or not text.strip(): return "요약할 수 없는 공지입니다."
    prompt = f"다음 텍스트를 한국어로 3~5문장의 간결한 요약으로 만들어줘. 핵심 내용을 명확하게 전달하고, 중요한 부분은 <b> 태그를 사용해서 강조해줘.\n\n원문:\n{text}\n\n요약:"
    try:
        response = await aclient.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt}], temperature=0.3, max_tokens=600)
        return response.choices[0].message.content.strip()
    except Exception as e:
        logging.error(f"❌ OpenAI API 요약 오류: {e}", exc_info=True)
        return "요약 중 오류가 발생했습니다."

async def extract_content(url: str) -> tuple:
    # ... 기존 본문 추출 코드 (변경 없음)
    try:
        html_content = await fetch_url(url)
        if not html_content: return ("페이지 내용을 불러올 수 없습니다.", [])
        soup = BeautifulSoup(html_content, 'html.parser')
        container = soup.find("div", class_="bdvTxt_wrap") or soup
        raw_text = ' '.join(container.get_text(separator=' ', strip=True).split())
        summary_text = await summarize_text(raw_text)
        images = [urllib.parse.urljoin(url, img['src']) for img in container.find_all('img') if img.get('src')]
        return (summary_text, images)
    except Exception as e:
        logging.error(f"❌ 본문 내용 추출 오류 {url}: {e}", exc_info=True)
        return ("내용 처리 중 오류가 발생했습니다.", [])

# ▼ 추가: PKNU AI 비교과 파싱 함수
def _parse_pknuai_page(soup: BeautifulSoup) -> list:
    """PKNU AI 시스템의 HTML을 파싱하여 프로그램 목록 반환 (수정된 버전)"""
    programs = []
    # 1. 각 프로그램 카드를 감싸는 li 태그를 선택합니다.
    items = soup.select("li.col-xl-3.col-lg-4.col-md-6")
    
    for li in items:
        # 2. 제목을 h5 태그 안의 a 태그에서 추출합니다.
        title_element = li.select_one("h5 a.ellip_2")
        title = title_element.get_text(strip=True) if title_element else "제목 없음"

        # 3. 상태를 .pin_area 안의 span 태그에서 추출합니다. (없을 경우 대비)
        status_element = li.select_one(".pin_area span")
        status = status_element.get_text(strip=True) if status_element else "모집 예정"

        # 4. 상세 정보에 필요한 고유 데이터는 .card-body 태그에서 추출합니다.
        meta_el = li.select_one(".card-body")
        if not meta_el:
            continue

        yy = meta_el.get("data-yy")
        shtm = meta_el.get("data-shtm")
        nonsubjcCd = meta_el.get("data-nonsubjc-cd")
        nonsubjcCrsCd = meta_el.get("data-nonsubjc-crs-cd")
        pageIndex = meta_el.get("data-page-index", "1")
        data_url = meta_el.get("data-url", "/web/nonSbjt/programDetail.do?mId=216&order=3")

        if not all([yy, shtm, nonsubjcCd, nonsubjcCrsCd]):
            continue

        detailUrl = (f"{PKNUAI_BASE_URL}{data_url}&pageIndex={pageIndex}&yy={yy}&shtm={shtm}"
                     f"&nonsubjcCd={nonsubjcCd}&nonsubjcCrsCd={nonsubjcCrsCd}")

        programs.append({
            "title": title, "status": status, "href": detailUrl,
            "yy": yy, "shtm": shtm, "nonsubjcCd": nonsubjcCd, "nonsubjcCrsCd": nonsubjcCrsCd
        })
    return programs
    
async def get_pknuai_programs() -> list:
    """PKNU AI 비교과 프로그램 목록을 가져옵니다 (로그인 포함)."""
    html_content = await fetch_program_html()
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, 'html.parser')
    return _parse_pknuai_page(soup)

################################################################################
#                                알림 전송 및 확인 함수                            #
################################################################################
async def send_notification(notice: tuple, target_chat_id: str):
    # ... 기존 공지사항 전송 함수 (변경 없음)
    title, href, department, date_ = notice
    summary, _ = await extract_content(href)
    message_text = (f"<b>[부경대 {html.escape(department)} 공지]</b>\n{html.escape(title)}\n\n"
                    f"<i>{html.escape(date_)}</i>\n______________________________________________\n{summary}")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]])
    await bot.send_message(chat_id=target_chat_id, text=message_text, reply_markup=keyboard, parse_mode="HTML")

# ▼ 추가: PKNU AI 프로그램 알림 전송 함수
async def send_pknuai_program_notification(program: dict, target_chat_id: str):
    title = html.escape(program.get("title", "제목 없음"))
    status = html.escape(program.get("status", ""))
    href = program.get("href", "#")
    
    message_text = (f"<b>[AI 비교과] {title}</b>\n"
                    f"<b>상태:</b> {status}\n")
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔎 자세히 보기", url=href)]])
    await bot.send_message(chat_id=target_chat_id, text=message_text, reply_markup=keyboard, parse_mode="HTML")


async def check_for_new_notices(target_chat_id: str):
    # ... 기존 공지사항 확인 함수 (변경 없음)
    logging.info("새로운 공지사항을 확인합니다...")
    seen = load_cache()
    current = await get_school_notices()
    found = False
    for notice in current:
        key = generate_cache_key(notice[0], notice[1])
        if key not in seen:
            logging.info(f"새 공지사항 발견: {notice[0]}")
            await send_notification(notice, target_chat_id)
            seen[key] = True
            found = True
    if found:
        save_cache(seen)
        push_cache_changes()

# ▼ 추가: PKNU AI 프로그램 확인 함수
async def check_for_new_pknuai_programs(target_chat_id: str):
    logging.info("새로운 AI 비교과 프로그램을 확인합니다...")
    seen = load_program_cache()
    current = await get_pknuai_programs()
    found = False
    for program in current:
        # AI 비교과는 고유 ID 조합으로 키 생성
        unique_id = f"{program['yy']}-{program['shtm']}-{program['nonsubjcCd']}-{program['nonsubjcCrsCd']}"
        key = generate_cache_key(program['title'], unique_id)
        if key not in seen:
            logging.info(f"새 AI 비교과 프로그램 발견: {program['title']}")
            await send_program_notification(program, target_chat_id)
            seen[key] = True
            found = True
    if found:
        save_program_cache(seen)
        push_program_cache_changes()

################################################################################
#                             명령어 및 기본 콜백 핸들러                            #
################################################################################
@dp.message(Command("start"))
async def start_command(message: types.Message):
    # ... 기존 코드 (변경 없음)
    if str(message.chat.id) not in ALLOWED_USERS:
        await message.answer("이 봇은 등록된 사용자만 이용할 수 있습니다.\n등록하려면 `/register [등록코드]`를 입력해 주세요.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="공지사항", callback_data="notice_menu"),
                InlineKeyboardButton(text="비교과 프로그램", callback_data="compare_programs")
            ]
        ]
    )
    await message.answer("안녕하세요! 부경대학교 알림 봇입니다.\n어떤 정보를 확인하시겠어요?", reply_markup=keyboard)

@dp.message(Command("register"))
async def register_command(message: types.Message):
    # ... 기존 코드 (변경 없음)
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("등록 코드를 함께 입력해주세요. 예: `/register 1234`")
        return
    code, user_id_str = parts[1].strip(), str(message.chat.id)
    if code == REGISTRATION_CODE:
        if user_id_str in ALLOWED_USERS:
            await message.answer("이미 등록된 사용자입니다.")
        else:
            default_filters = {"1학년": False, "2학년": False, "3학년": False, "4학년": False, "도전": False, "소통": False, "인성": False, "창의": False, "협업": False, "전문": False, "신청가능": False}
            ALLOWED_USERS[user_id_str] = {"filters": default_filters}
            save_whitelist(ALLOWED_USERS)
            push_file_changes(WHITELIST_FILE, "New user registration")
            await message.answer("✅ 등록이 완료되었습니다! 이제 모든 기능을 사용할 수 있습니다.")
            logging.info(f"새 사용자 등록: {user_id_str}")
    else:
        await message.answer("❌ 등록 코드가 올바르지 않습니다.")

@dp.callback_query(lambda c: c.data == "notice_menu")
async def notice_menu_handler(callback: CallbackQuery):
    # ... 기존 코드 (변경 없음)
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📅 날짜로 검색", callback_data="filter_date"), InlineKeyboardButton(text="🗂️ 카테고리별 보기", callback_data="all_notices")]])
    await callback.message.edit_text("공지사항 옵션을 선택하세요:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext) -> None:
    """날짜 필터링 시작"""
    await callback.answer()
    await callback.message.edit_text("📅 MM/DD 형식으로 날짜를 입력해 주세요. (예: 09/18)")
    await state.set_state(FilterState.waiting_for_date)
    
################################################################################
#                    ▼ 수정: 비교과 프로그램 메뉴 및 핸들러                          #
################################################################################
PROGRAM_FILTERS = [
    "1학년", "2학년", "3학년", "4학년", "도전", "소통", 
    "인성", "창의", "협업", "전문", "신청가능"
]
PROGRAM_FILTER_MAP = {
    "1학년": "searchGrade1", "2학년": "searchGrade2", 
    "3학년": "searchGrade3", "4학년": "searchGrade4",
    "도전": "searchIaq1", "소통": "searchIaq2", "인성": "searchIaq3",
    "창의": "searchIaq4", "협업": "searchIaq5", "전문": "searchIaq6",
    "신청가능": "searchApply"
}
@dp.callback_query(lambda c: c.data == "extracurricular_menu")
def get_program_filter_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    """AI 비교과 필터 메뉴 키보드를 생성합니다."""
    user_filters = ALLOWED_USERS.get(str(chat_id), {}).get("filters", {})
    buttons = []
    # PROGRAM_FILTERS는 코드 상단에 정의된 필터 목록
    for f in PROGRAM_FILTERS:
        text = f"{'✅' if user_filters.get(f) else ''} {f}".strip()
        buttons.append(InlineKeyboardButton(text=text, callback_data=f"toggle_program_{f}"))

    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text="✨ 필터로 검색하기 ✨", callback_data="my_programs")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.message(Command("filter"))
async def filter_command(message: types.Message) -> None:
    """/filter 명령어 핸들러"""
    keyboard = get_program_filter_keyboard(message.chat.id)
    await message.answer("🎯 AI 비교과 필터를 선택하세요:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("toggle_program_"))
async def toggle_program_filter(callback: CallbackQuery):
    """필터 버튼을 누를 때마다 상태를 변경하고 저장합니다."""
    filter_name = callback.data.replace("toggle_program_", "")
    user_id_str = str(callback.message.chat.id)
    user_data = ALLOWED_USERS.setdefault(user_id_str, {})
    filters = user_data.setdefault("filters", {f: False for f in PROGRAM_FILTERS})
    filters[filter_name] = not filters.get(filter_name, False)

    save_whitelist(ALLOWED_USERS) # 변경 즉시 저장
    push_file_changes(WHITELIST_FILE, f"Update filters for user {user_id_str}")

    await callback.answer(f"{filter_name} 필터 {'선택' if filters[filter_name] else '해제'}")
    keyboard = get_program_filter_keyboard(callback.message.chat.id)
    await callback.message.edit_reply_markup(reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "my_programs")
async def my_programs_handler(callback: CallbackQuery):
    """설정된 필터에 맞는 AI 비교과 프로그램을 검색하여 보여줍니다."""
    await callback.answer()
    user_id_str = str(callback.message.chat.id)
    user_filters = ALLOWED_USERS.get(user_id_str, {}).get("filters", {})

    if not any(user_filters.values()):
        # 필터가 설정되지 않았을 때 필터 설정 메뉴를 보여줍니다.
        keyboard = get_program_filter_keyboard(callback.message.chat.id)
        await callback.message.edit_text("🎯 먼저 필터를 선택해주세요:", reply_markup=keyboard)
        return

    status_msg = await callback.message.edit_text("📊 필터로 검색 중... (로그인 필요)")

    # ▼▼▼▼▼ 핵심 수정 부분 ▼▼▼▼▼
    # 1. 필터를 적용하여 HTML을 가져옵니다.
    html_content = await fetch_program_html(filters=user_filters)

    # 2. 가져온 HTML을 파싱합니다.
    programs = []
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        programs = _parse_pknuai_page(soup)
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    await status_msg.delete()

    if not programs:
        await callback.message.answer("조건에 맞는 프로그램이 없습니다.")
    else:
        for program in programs:
            await send_pknuai_program_notification(program, callback.message.chat.id)

@dp.callback_query(lambda c: c.data == "compare_programs")
async def compare_programs_handler(callback: CallbackQuery):
    """AI 비교과 프로그램의 메인 메뉴를 보여줍니다."""
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="나만의 프로그램 (필터)", callback_data="my_programs")],
        [InlineKeyboardButton(text="키워드로 검색", callback_data="keyword_search")]
    ])
    await callback.message.edit_text("AI 비교과 프로그램입니다. 원하시는 기능을 선택하세요:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "keyword_search")
async def keyword_search_handler(callback: CallbackQuery, state: FSMContext):
    """키워드 검색을 시작하는 핸들러"""
    await callback.answer()
    await callback.message.edit_text("🔎 검색할 키워드를 입력해 주세요:")
    await state.set_state(KeywordSearchState.waiting_for_keyword)

@dp.message(KeywordSearchState.waiting_for_keyword)
async def process_keyword_search(message: types.Message, state: FSMContext):
    """키워드 입력을 처리하고, 검색된 프로그램을 가져와 전송"""
    keyword = message.text.strip()
    await state.clear()

    await message.answer(f"🔍 '{keyword}' 키워드로 검색 중입니다... (로그인 필요)")
    html_content = await fetch_program_html(keyword=keyword)

    programs = []
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        programs = _parse_pknuai_page(soup)

    if not programs:
        await message.answer(f"❌ '{keyword}' 키워드에 해당하는 프로그램이 없습니다.")
    else:
        for program in programs:
            await send_pknuai_program_notification(program, message.chat.id)

################################################################################
#                            기타 상태 및 메시지 핸들러                            #
################################################################################
def parse_date(date_str: str):
    """다양한 날짜 형식을 처리하는 함수"""
    try:
        return datetime.strptime(date_str, "%Y.%m.%d")
    except ValueError:
        return None
        
# 기존 process_date_input 함수를 지우고 아래 디버깅 강화 버전으로 교체하세요.
@dp.message(FilterState.waiting_for_date)
async def process_date_input(message: types.Message, state: FSMContext) -> None:
    """날짜 입력을 처리하는 핸들러 (디버깅 강화 버전)"""
    # ... (이전 권한 확인 코드는 동일)
    
    input_text = message.text.strip()
    try:
        month, day = map(int, input_text.split('/'))
    except ValueError:
        # ... (이전 오류 처리는 동일)
        return

    await state.clear()
    await message.answer(f"📅 {month}월 {day}일 날짜의 공지사항을 검색합니다...")
    
    all_notices = await get_school_notices()
    
    filtered_notices = []
    logging.info(f"사용자 요청 날짜: Month={month}, Day={day}") # 디버깅 로그 추가

    for notice_tuple in all_notices:
        notice_date_str = notice_tuple[3]
        try:
            notice_date_obj = datetime.strptime(notice_date_str, "%Y.%m.%d")
            # ▼▼▼ 비교 직전에 로그를 남겨서 확인 ▼▼▼
            logging.info(f"  -> 공지사항 날짜 '{notice_date_str}'와 비교 중... (Month={notice_date_obj.month}, Day={notice_date_obj.day})")
            if notice_date_obj.month == month and notice_date_obj.day == day:
                filtered_notices.append(notice_tuple)
        except ValueError:
            continue

    if not filtered_notices:
        await message.answer(f"📢 {month}월 {day}일 날짜에 해당하는 공지사항이 없습니다.")
    else:
        for notice in filtered_notices:
            await send_notification(notice, message.chat.id)
            
@dp.callback_query(lambda c: c.data == "all_notices")
async def callback_all_notices(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=category, callback_data=f"category_{code}")]
            for category, code in CATEGORY_CODES.items()
        ]
    )
    await callback.message.edit_text("원하는 카테고리를 선택하세요:", reply_markup=keyboard)
    await state.set_state(FilterState.selecting_category)

@dp.callback_query(lambda c: c.data.startswith("category_"))
async def callback_category_selection(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    category_code = callback.data.split("_")[1]
    category_name = next((name for name, code in CATEGORY_CODES.items() if code == category_code), category_code)
    await callback.message.edit_text(f"카테고리 '{category_name}'의 공지사항을 검색합니다...")

    notices = await get_school_notices(category_code)
    if not notices:
        await callback.message.answer("해당 카테고리의 공지사항이 없습니다.")
    else:
        for notice in notices[:7]: # 최신 7개만 전송
            await send_notification(notice, callback.message.chat.id)
    await state.clear()

@dp.message()
async def catch_all(message: types.Message):
    await message.answer("⚠️ 유효하지 않은 명령어입니다. /start 를 입력하여 메뉴를 확인해주세요.")

################################################################################
#                                 메인 실행 및 스케줄러                            #
################################################################################
async def scheduled_tasks():
    """10분마다 새로운 공지사항과 프로그램을 확인하는 스케줄러"""
    while True:
        try:
            logging.info("스케줄링된 작업을 시작합니다.")
            await check_for_new_notices(GROUP_CHAT_ID)
            await check_for_new_pknuai_programs(GROUP_CHAT_ID)
            logging.info("스케줄링된 작업이 완료되었습니다.")
        except Exception as e:
            logging.error(f"스케줄링 작업 중 오류 발생: {e}", exc_info=True)
        await asyncio.sleep(600)

async def main() -> None:
    logging.info("봇을 시작합니다. 초기 데이터 확인 중...")
    try:
        await check_for_new_notices(GROUP_CHAT_ID)
        await check_for_new_pknuai_programs(GROUP_CHAT_ID)
    except Exception as e:
        logging.error(f"초기 데이터 확인 중 오류 발생: {e}", exc_info=True)

    scheduler_task = asyncio.create_task(scheduled_tasks())
    logging.info("🚀 봇 폴링을 시작합니다...")
    await dp.start_polling(bot)
    scheduler_task.cancel()

if __name__ == '__main__':
    if sys.platform.startswith("win"): asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("봇이 종료되었습니다.")
    except Exception as e:
        logging.critical(f"❌ 봇 실행 중 치명적인 오류 발생: {e}", exc_info=True)
        async def notify_crash():
            try:
                crash_bot = Bot(token=TOKEN)
                await crash_bot.send_message(CHAT_ID, f"🚨 봇 비정상 종료:\n\n`{e}`\n\n확인 및 재실행 필요.")
                await crash_bot.session.close()
            except Exception as notify_error:
                logging.error(f"❌ 크래시 알림 전송 실패: {notify_error}", exc_info=True)
        asyncio.run(notify_crash())

