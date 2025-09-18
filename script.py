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

# 레인보우 비교과 시스템
PROGRAM_URL = "https://rainbow.pknu.ac.kr/main/CAP/C/C/A/list.do"
PROGRAM_BASE_URL = "https://rainbow.pknu.ac.kr"
PROGRAM_CACHE_FILE = "programs_seen.json"

# ▼ 추가: PKNU AI 비교과 시스템
PKNUAI_PROGRAM_URL = "https://pknuai.pknu.ac.kr/web/nonSbjt/program.do?mId=216&order=3"
PKNUAI_BASE_URL = "https://pknuai.pknu.ac.kr"
PKNUAI_PROGRAM_CACHE_FILE = "pknuai_programs_seen.json"


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

load_program_cache = lambda: load_json_file(PROGRAM_CACHE_FILE)
save_program_cache = lambda data: save_json_file(data, PROGRAM_CACHE_FILE)
push_program_cache_changes = lambda: push_file_changes(PROGRAM_CACHE_FILE, "Update programs_seen.json")

# ▼ 추가: PKNU AI 프로그램 캐시 함수
load_pknuai_program_cache = lambda: load_json_file(PKNUAI_PROGRAM_CACHE_FILE)
save_pknuai_program_cache = lambda data: save_json_file(data, PKNUAI_PROGRAM_CACHE_FILE)
push_pknuai_program_cache_changes = lambda: push_file_changes(PKNUAI_PROGRAM_CACHE_FILE, "Update pknuai_programs_seen.json")

################################################################################
#                         웹페이지 크롤링 함수 (Playwright / aiohttp)                    #
################################################################################
async def fetch_dynamic_html(url: str, actions: callable = None) -> str:
    """레인보우 시스템 크롤링 함수"""
    logging.info(f"🚀 Playwright로 페이지 로딩 시작: {url}")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page()
            await page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "stylesheet", "font", "media"] else route.continue_())
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if actions:
                await actions(page)
                await page.wait_for_selector("ul.program_list", timeout=15000)
            content = await page.content()
            await browser.close()
            logging.info(f"✅ Playwright 로딩 성공: {url}")
            return content
    except Exception as e:
        logging.error(f"❌ Playwright 크롤링 오류: {url}, {e}", exc_info=True)
        return ""

async def fetch_program_html(keyword: str = None, filters: dict = None) -> str:
    """PKNU AI 비교과 페이지를 로그인, 검색, 필터링하여 HTML을 가져오는 함수"""
    if not PKNU_USERNAME or not PKNU_PASSWORD:
        logging.error("❌ PKNU_USERNAME 또는 PKNU_PASSWORD 환경 변수가 설정되지 않았습니다.")
        return ""
    
    logging.info(f"🚀 Playwright 작업 시작 (검색어: {keyword}, 필터: {filters})")
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"])
            page = await browser.new_page()
            
            await page.goto(PROGRAM_URL, wait_until="domcontentloaded", timeout=30000)

            # sso.pknu.ac.kr 페이지로 리다이렉트 되었는지 확인
            if "sso.pknu.ac.kr" in page.url:
                logging.info("SSO 로그인 페이지로 리다이렉트됨. 로그인을 시도합니다.")
                
                # --- 여기가 핵심 수정 부분 ---
                # 1. 아이디 입력 (수정됨)
                await page.wait_for_selector("input#userId", timeout=15000)
                await page.fill("input#userId", PKNU_USERNAME)
                
                # 2. 비밀번호 입력 (수정됨)
                await page.fill("input#userpw", PKNU_PASSWORD)
                
                # 3. 로그인 버튼 클릭 (수정됨)
                await page.click('button[type="submit"]')
                # --- 여기까지 ---

            # 로그인 후 최종 목적지인 프로그램 목록 페이지 로딩을 기다림
            await page.wait_for_url(f"{PROGRAM_BASE_URL}/web/nonSbjt/program.do**", timeout=20000)
            await page.wait_for_selector("ul.row.flex-wrap.viewType", timeout=20000)
            logging.info("로그인 및 기본 페이지 로딩 성공.")

            # (이후 필터 및 검색 로직은 동일)
            if keyword:
                logging.info(f"키워드 '{keyword}'로 검색합니다.")
                await page.fill("#searchKeyword", keyword)
                await page.press("#searchKeyword", "Enter")
                await page.wait_for_load_state("networkidle", timeout=15000)

            if filters:
                logging.info(f"필터를 적용합니다: {filters}")
                for filter_name, is_selected in filters.items():
                    if is_selected:
                        input_id = PROGRAM_FILTER_MAP.get(filter_name)
                        if input_id: await page.click(f"label[for='{input_id}']")
                await page.wait_for_timeout(2000)

            content = await page.content()
            await browser.close()
            logging.info("✅ Playwright 크롤링 성공")
            return content
            
    except Exception as e:
        logging.error(f"❌ Playwright 크롤링 중 오류 발생: {e}", exc_info=True)
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
        
def _parse_rainbow_page(soup: BeautifulSoup) -> list:
    # ... 기존 레인보우 파싱 코드 (변경 없음)
    programs = []
    for item in soup.select("ul.program_list > li"):
        if item.find("span", class_="label", string="모집종료"): continue
        title = item.select_one("strong.tit").get_text(strip=True)
        department = item.select_one("div.department").get_text(strip=True)
        category = (item.select_one("li.point").get_text(strip=True).replace("역량", "").strip() if item.select_one("li.point") else "")
        rec_period, op_period = "", ""
        for p_item in item.select("li.period"):
            text = p_item.get_text(strip=True)
            if text.startswith("신청"): rec_period = text.replace("신청", "").strip()
            elif text.startswith("운영"): op_period = text.replace("운영", "").strip()
        applicants, capacity = "정보 없음", "정보 없음"
        if member_elem := item.select_one("li.member"):
            if "/" in (member_text := member_elem.get_text(strip=True).replace("정원", "").replace("명", "")):
                applicants, capacity = member_text.split('/')
        link = ""
        if onclick := item.get("onclick"):
            if match := re.search(r"fn_detail\('(\d+)'\)", onclick):
                link = f"{PROGRAM_BASE_URL}/main/CAP/C/C/A/view.do?prgSn={match.group(1)}"
        programs.append({"title": title, "categories": [department, category], "recruitment_period": rec_period, "operation_period": op_period, "capacity": capacity.strip(), "applicants": applicants.strip(), "href": link})
    programs.sort(key=lambda x: datetime.strptime(x["recruitment_period"].split('~')[0].strip(), "%Y.%m.%d") if '~' in x["recruitment_period"] else datetime.min, reverse=True)
    return programs

async def get_rainbow_programs(user_filters: dict = None) -> list:
    # ... 기존 get_programs 코드 (이름 변경)
    actions = None
    if user_filters and any(user_filters.values()):
        async def filter_actions(page):
            logging.info(f"레인보우 필터 적용: {user_filters}")
            grade_map = {"1학년": "1", "2학년": "2", "3학년": "3", "4학년": "4"}
            for grade, value in grade_map.items():
                if user_filters.get(grade): await page.click(f"label[for='searchGrade{value}']")
            comp_map = {"도전": "1", "소통": "2", "인성": "3", "창의": "4", "협업": "5", "전문": "6"}
            for comp, value in comp_map.items():
                if user_filters.get(comp): await page.click(f"label[for='searchIaq{value}']")
            if user_filters.get("신청가능"): await page.click("label[for='searchApply']")
            await page.click("div.search_box > button.btn_search")
        actions = filter_actions
    html_content = await fetch_dynamic_html(PROGRAM_URL, actions=actions)
    return _parse_rainbow_page(BeautifulSoup(html_content, 'html.parser')) if html_content else []

async def get_rainbow_programs_by_keyword(keyword: str) -> list:
    # ... 기존 get_programs_by_keyword 코드 (이름 변경)
    async def search_actions(page):
        logging.info(f"레인보우 키워드 검색: {keyword}")
        await page.fill("input#searchPrgNm", keyword)
        await page.click("div.search_box > button.btn_search")
    html_content = await fetch_dynamic_html(PROGRAM_URL, actions=search_actions)
    return _parse_rainbow_page(BeautifulSoup(html_content, 'html.parser')) if html_content else []
    
# ▼ 추가: PKNU AI 비교과 파싱 함수
def _parse_pknuai_page(soup: BeautifulSoup) -> list:
    """PKNU AI 시스템의 HTML을 파싱하여 프로그램 목록 반환"""
    programs = []
    items = soup.select("ul.row.flex-wrap.viewType > li")
    for li in items:
        title = (li.select_one("a[href='#']").get_text(strip=True) or "제목 없음")
        status = (li.select_one(".pin_area .pin_on2").get_text(strip=True) or "상태 미확인")
        
        # 상세 URL 구성에 필요한 데이터 추출
        meta_el = li.select_one(".like_btn, [data-url][data-yy][data-shtm]")
        if not meta_el: continue
        
        yy = meta_el.get("data-yy")
        shtm = meta_el.get("data-shtm")
        nonsubjcCd = meta_el.get("data-nonsubjc-cd")
        nonsubjcCrsCd = meta_el.get("data-nonsubjc-crs-cd")
        pageIndex = meta_el.get("data-page-index", "1")
        data_url = meta_el.get("data-url", "/web/nonSbjt/programDetail.do?mId=216&order=3")
        
        if not all([yy, shtm, nonsubjcCd, nonsubjcCrsCd]): continue

        detailUrl = (f"{PKNUAI_BASE_URL}{data_url}&pageIndex={pageIndex}&yy={yy}&shtm={shtm}"
                     f"&nonsubjcCd={nonsubjcCd}&nonsubjcCrsCd={nonsubjcCrsCd}")

        programs.append({
            "title": title, "status": status, "href": detailUrl,
            "yy": yy, "shtm": shtm, "nonsubjcCd": nonsubjcCd, "nonsubjcCrsCd": nonsubjcCrsCd
        })
    return programs

async def get_pknuai_programs() -> list:
    """PKNU AI 비교과 프로그램 목록을 가져옵니다 (로그인 포함)."""
    html_content = await fetch_pknuai_html()
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

async def send_rainbow_program_notification(program: dict, target_chat_id: str):
    # ... 기존 send_program_notification (이름 변경)
    title = html.escape(program.get("title", "제목 없음"))
    categories = " &gt; ".join(map(html.escape, program.get("categories", [])))
    rec_period = html.escape(program.get("recruitment_period", "정보 없음"))
    op_period = html.escape(program.get("operation_period", "정보 없음"))
    capacity_text = f"{program.get('applicants', '0')} / {program.get('capacity', '0')}명"
    message_text = (f"<b>[레인보우] {title}</b>\n<i>{categories}</i>\n"
                    "______________________________________________\n\n"
                    f"📅 <b>신청 기간:</b> {rec_period}\n📅 <b>운영 기간:</b> {op_period}\n"
                    f"👥 <b>신청 현황:</b> {capacity_text}\n")
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔎 자세히 보기", url=program.get("href", "#"))]])
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

async def check_for_new_rainbow_programs(target_chat_id: str):
    # ... 기존 check_for_new_programs (이름 변경)
    logging.info("새로운 레인보우 비교과 프로그램을 확인합니다...")
    seen = load_program_cache()
    current = await get_rainbow_programs()
    found = False
    for program in current:
        key = generate_cache_key(program["title"], program["href"])
        if key not in seen:
            logging.info(f"새 레인보우 프로그램 발견: {program['title']}")
            await send_rainbow_program_notification(program, target_chat_id)
            seen[key] = True
            found = True
    if found:
        save_program_cache(seen)
        push_program_cache_changes()

# ▼ 추가: PKNU AI 프로그램 확인 함수
async def check_for_new_pknuai_programs(target_chat_id: str):
    logging.info("새로운 AI 비교과 프로그램을 확인합니다...")
    seen = load_pknuai_program_cache()
    current = await get_pknuai_programs()
    found = False
    for program in current:
        # AI 비교과는 고유 ID 조합으로 키 생성
        unique_id = f"{program['yy']}-{program['shtm']}-{program['nonsubjcCd']}-{program['nonsubjcCrsCd']}"
        key = generate_cache_key(program['title'], unique_id)
        if key not in seen:
            logging.info(f"새 AI 비교과 프로그램 발견: {program['title']}")
            await send_pknuai_program_notification(program, target_chat_id)
            seen[key] = True
            found = True
    if found:
        save_pknuai_program_cache(seen)
        push_pknuai_program_cache_changes()

################################################################################
#                             명령어 및 기본 콜백 핸들러                            #
################################################################################
@dp.message(Command("start"))
async def start_command(message: types.Message):
    # ... 기존 코드 (변경 없음)
    if str(message.chat.id) not in ALLOWED_USERS:
        await message.answer("이 봇은 등록된 사용자만 이용할 수 있습니다.\n등록하려면 `/register [등록코드]`를 입력해 주세요.")
        return
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="공지사항", callback_data="notice_menu"), InlineKeyboardButton(text="비교과 프로그램", callback_data="extracurricular_menu")]])
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
    
################################################################################
#                    ▼ 수정: 비교과 프로그램 메뉴 및 핸들러                          #
################################################################################
@dp.callback_query(lambda c: c.data == "extracurricular_menu")
async def extracurricular_menu_handler(callback: CallbackQuery):
    """레인보우와 AI 비교과 시스템을 선택하는 메인 메뉴"""
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🌈 레인보우 비교과", callback_data="rainbow_menu")],
        [InlineKeyboardButton(text="🤖 AI 비교과 (로그인 필요)", callback_data="pknuai_programs")]
    ])
    await callback.message.edit_text("확인하고 싶은 비교과 시스템을 선택하세요:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "rainbow_menu")
async def rainbow_menu_handler(callback: CallbackQuery):
    """레인보우 비교과 프로그램의 세부 메뉴"""
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎯 맞춤 프로그램 찾기", callback_data="rainbow_my_programs")],
        [InlineKeyboardButton(text="⚙️ 필터 설정", callback_data="rainbow_set_filters")],
        [InlineKeyboardButton(text="🔎 키워드로 검색", callback_data="rainbow_keyword_search")]
    ])
    await callback.message.edit_text("레인보우 비교과 프로그램 옵션을 선택하세요:", reply_markup=keyboard)
    
@dp.callback_query(lambda c: c.data == "rainbow_my_programs")
async def rainbow_my_programs_handler(callback: CallbackQuery):
    await callback.answer()
    user_filter = ALLOWED_USERS.get(str(callback.message.chat.id), {}).get("filters", {})
    if not any(user_filter.values()):
        await callback.message.edit_text("설정된 필터가 없습니다. 우선, 현재 모집 중인 전체 프로그램을 보여드릴게요.")
    else:
        await callback.message.edit_text("필터에 맞는 프로그램을 검색 중입니다...")
    programs = await get_rainbow_programs(user_filter)
    if not programs:
        await callback.message.edit_text("조건에 맞는 프로그램이 없습니다.")
    else:
        for program in programs: await send_rainbow_program_notification(program, callback.message.chat.id)

# ... (기존 rainbow 필터 관련 핸들러들은 이름만 rainbow_ 접두사 붙여서 유지)
def get_rainbow_filter_keyboard(chat_id: int) -> InlineKeyboardMarkup:
    # ... 기존 get_program_filter_keyboard (이름 변경)
    current_filters = ALLOWED_USERS.get(str(chat_id), {}).get("filters", {})
    grades = ["1학년", "2학년", "3학년", "4학년"]
    comp1 = ["도전", "소통", "인성"]; comp2 = ["창의", "협업", "전문"]
    options = ["신청가능"]
    def create_button(opt): return InlineKeyboardButton(text=f"{'✅' if current_filters.get(opt) else ''} {opt}".strip(), callback_data=f"toggle_rainbow_{opt}")
    keyboard = [[create_button(g) for g in grades], [create_button(c) for c in comp1], [create_button(c) for c in comp2], [create_button(o) for o in options], [InlineKeyboardButton(text="💾 저장하고 돌아가기", callback_data="rainbow_filter_done")]]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.callback_query(lambda c: c.data == "rainbow_set_filters")
async def set_rainbow_filters_handler(callback: CallbackQuery):
    await callback.answer(); await callback.message.edit_text("원하는 필터를 선택하세요:", reply_markup=get_rainbow_filter_keyboard(callback.message.chat.id))

@dp.callback_query(lambda c: c.data.startswith("toggle_rainbow_"))
async def toggle_rainbow_filter(callback: CallbackQuery):
    user_id_str = str(callback.message.chat.id)
    option = callback.data.replace("toggle_rainbow_", "")
    filters = ALLOWED_USERS.setdefault(user_id_str, {"filters": {}}).setdefault("filters", {})
    filters[option] = not filters.get(option, False)
    save_whitelist(ALLOWED_USERS)
    await callback.message.edit_text("원하는 필터를 선택하세요:", reply_markup=get_rainbow_filter_keyboard(callback.message.chat.id))

@dp.callback_query(lambda c: c.data == "rainbow_filter_done")
async def filter_done_rainbow_handler(callback: CallbackQuery):
    await callback.answer()
    push_file_changes(WHITELIST_FILE, "Update user filters")
    user_filter = ALLOWED_USERS.get(str(callback.message.chat.id), {}).get("filters", {})
    selected = [opt for opt, chosen in user_filter.items() if chosen]
    await callback.message.edit_text(f"필터가 저장되었습니다.\n선택: {', '.join(selected) if selected else '없음'}")
    await rainbow_menu_handler(callback)

@dp.callback_query(lambda c: c.data == "rainbow_keyword_search")
async def rainbow_keyword_search_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer(); await callback.message.edit_text("🔎 레인보우 시스템에서 검색할 키워드를 입력해 주세요:"); await state.set_state(KeywordSearchState.waiting_for_keyword)

@dp.message(KeywordSearchState.waiting_for_keyword)
async def process_keyword_search(message: types.Message, state: FSMContext):
    keyword = message.text.strip()
    await state.clear()
    await message.answer(f"🔍 '{keyword}' 키워드로 레인보우 프로그램을 검색합니다...")
    programs = await get_rainbow_programs_by_keyword(keyword)
    if not programs: await message.answer(f"❌ '{keyword}'에 해당하는 프로그램이 없습니다.")
    else:
        for program in programs: await send_rainbow_program_notification(program, message.chat.id)

@dp.callback_query(lambda c: c.data == "pknuai_programs")
async def pknuai_programs_handler(callback: CallbackQuery):
    await callback.answer()
    if not PKNU_USERNAME or not PKNU_PASSWORD:
        await callback.message.edit_text("PKNU AI 비교과 정보를 보려면 봇 관리자가 먼저 로그인 정보를 설정해야 합니다.")
        return
    
    await callback.message.edit_text("🤖 AI 비교과 프로그램을 불러오는 중입니다. 로그인이 필요하여 시간이 조금 걸릴 수 있습니다...")
    programs = await get_pknuai_programs()

    if not programs:
        await callback.message.edit_text("현재 모집 중인 AI 비교과 프로그램이 없거나, 정보를 불러오는 데 실패했습니다.")
    else:
        for program in programs:
            await send_pknuai_program_notification(program, callback.message.chat.id)

################################################################################
#                            기타 상태 및 메시지 핸들러                            #
################################################################################
@dp.message(FilterState.waiting_for_date)
async def process_date_input(message: types.Message, state: FSMContext):
    # ... 기존 날짜 처리 핸들러 (변경 없음)
    try:
        month, day = map(int, message.text.strip().split('/'))
        filter_date = datetime(datetime.now().year, month, day)
    except ValueError:
        await message.answer("⚠️ 날짜 형식이 올바르지 않습니다. MM/DD 형식으로 다시 입력해 주세요."); return
    await state.clear()
    await message.answer(f"📅 {filter_date.strftime('%Y-%m-%d')} 날짜의 공지사항을 검색합니다...")
    filtered_notices = [n for n in await get_school_notices() if (d := parse_date(n[3])) and d.date() == filter_date.date()]
    if not filtered_notices: await message.answer(f"해당 날짜에 등록된 공지사항이 없습니다.")
    else:
        for notice in filtered_notices: await send_notification(notice, message.chat.id)

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
            await check_for_new_rainbow_programs(GROUP_CHAT_ID)
            await check_for_new_pknuai_programs(GROUP_CHAT_ID)
            logging.info("스케줄링된 작업이 완료되었습니다.")
        except Exception as e:
            logging.error(f"스케줄링 작업 중 오류 발생: {e}", exc_info=True)
        await asyncio.sleep(600)

async def main() -> None:
    logging.info("봇을 시작합니다. 초기 데이터 확인 중...")
    try:
        await check_for_new_notices(GROUP_CHAT_ID)
        await check_for_new_rainbow_programs(GROUP_CHAT_ID)
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

