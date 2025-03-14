################################################################################
#                       필요한 라이브러리 Import                                #
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
from html import escape
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

# ▼ 추가: Playwright 헤드리스 브라우저
from playwright.async_api import async_playwright

################################################################################
#                       환경 변수 / 토큰 / 상수 설정                           #
################################################################################
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

# 비교과 프로그램의 기본 URL
PROGRAM_URL = "https://whalebe.pknu.ac.kr/main/65"

CATEGORY_CODES = {
    "전체": "",
    "공지사항": "10001",
    "비교과 안내": "10002",
    "학사 안내": "10003",
    "등록/장학": "10004",
    "초빙/채용": "10007"
}

################################################################################
#                       로깅 설정                                               #
################################################################################
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("logfile.log", encoding="utf-8"),
        logging.StreamHandler(),
        RotatingFileHandler("logfile.log", maxBytes=10**6, backupCount=3)  # Use the imported handler
    ]
)

################################################################################
#                       AIogram 설정                                           #
################################################################################
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(bot=bot)

################################################################################
#                       상태머신 정의                                           #
################################################################################
class FilterState(StatesGroup):
    waiting_for_date = State()
    selecting_category = State()

class KeywordSearchState(StatesGroup):
    waiting_for_keyword = State()  # 키워드 입력 상태 추가

################################################################################
#                       화이트리스트 관련 함수                                  #
################################################################################
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

ALLOWED_USERS = load_whitelist()
logging.info(f"현재 화이트리스트: {ALLOWED_USERS}")

################################################################################
#                       공지사항 / 프로그램 캐시 관련 함수                     #
################################################################################
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
            logging.error("❌ MY_PAT 환경 변수가 설정되어 있지 않습니다.")
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
            logging.error("❌ MY_PAT 환경 변수가 설정되어 있지 않습니다.")
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

################################################################################
#                       동적 로딩 페이지 가져오는 함수 (Playwright)             #
################################################################################
async def fetch_dynamic_html(url: str) -> str:
    logging.debug(f"Playwright로 동적 페이지 요청 시작: {url}")
    try:
        timeout_duration = 60000  # 60초로 늘림
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/114.0.0.0 Safari/537.36"
                )
            })
            await page.goto(url, timeout=timeout_duration)
            await page.wait_for_load_state("networkidle")
            content = await page.content()
            await browser.close()
            logging.debug(f"Playwright로 동적 페이지 요청 성공: {url} - 길이: {len(content)}")
            return content
    except Exception as e:
        logging.error(f"❌ Playwright dynamic fetch 오류: {url}, {e}", exc_info=True)
        return ""
        
################################################################################
#                       기타 공통 함수                                          #
################################################################################
# 단일 날짜를 파싱하는 함수 (MM/DD 형식 추가)
def parse_date(date_str: str):
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError as ve:
        logging.error(f"Date parsing error for {date_str}: {ve}", exc_info=True)
        return None

# 날짜 범위 (예: 2025.03.01 ~ 2025.03.31) 처리 함수 수정
def parse_date_range(date_str):
    """ 날짜 범위 (예: 03/01 ~ 03/31) 처리 함수 수정 """
    try:
        # 날짜 범위가 '~'를 포함하는지 확인
        if "~" in date_str:
            start_date_str, end_date_str = date_str.split("~")
            start_date = parse_date(start_date_str.strip())
            end_date = parse_date(end_date_str.strip())
            if start_date and end_date:
                return start_date, end_date
        else:
            # 범위가 아닌 경우, 단일 날짜를 처리
            return parse_date(date_str.strip()), None
    except Exception as e:
        print(f"❌ Error: {e} - 날짜 범위 파싱 실패: {date_str}")
        return None, None

################################################################################
#                       기존 aiohttp로 사용하는 fetch_url (공지사항 용)         #
################################################################################
async def fetch_url(url: str) -> str:
    """
    공지사항 페이지처럼 단순 정적 콘텐츠는 aiohttp로 처리.
    (JavaScript 없이도 내용 확인 가능)
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/114.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        logging.debug(f"요청 시작: {url}")
        timeout_duration = 30
        async with aiohttp.ClientSession(headers=headers) as session:
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

################################################################################
#                       공지사항 파싱 함수                                      #
################################################################################
async def get_school_notices(category: str = "") -> list:
    """
    부경대 공지사항 페이지(정적) 파싱: aiohttp + BeautifulSoup 사용
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
                
                # 상대경로일 경우 절대경로로 변환
                if href.startswith("/"):
                    href = BASE_URL + href
                elif href.startswith("?"):
                    href = BASE_URL + "/main/163" + href
                
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

################################################################################
#                       프로그램(비교과) 파싱 함수 (Playwright 사용)            #
################################################################################
def build_filter_url(user_filters: dict) -> str:
    """
    선택된 필터를 GET 파라미터로 구성해 URL 생성
    """
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
        "clsf": "",
        "type": [],
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
    """
    JavaScript 동적 로딩된 페이지에서 비교과 프로그램 목록 파싱 (세부 정보 추가)
    """
    if user_filters is None:
        url = PROGRAM_URL
    else:
        url = build_filter_url(user_filters)

    # Playwright로 최종 렌더링된 HTML 가져오기
    html_content = await fetch_dynamic_html(url)
    if not html_content:
        logging.error("❌ 필터 적용된 프로그램 페이지를 불러올 수 없습니다.")
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    programs = []

    # ul.flex-wrap > li 구조
    program_items = soup.select("ul.flex-wrap > li")
    if not program_items:
        logging.debug("No 'ul.flex-wrap > li' elements found. Trying alternative selectors...")
        program_items = soup.select(".program-item")
    if not program_items:
        snippet = html_content[:500]
        logging.debug(f"HTML snippet for filtered page: {snippet}")

    for item in program_items:
        card_body = item.select_one("div.card-body")
        if not card_body:
            continue
    
        # 제목
        title_elem = card_body.select_one("h4.card-title")
        title = title_elem.get_text(strip=True) if title_elem else "제목없음"
    
        # ----- (수정) 카테고리/학과/부서 정보 -----
        # sub_info 영역 안에 "첫 번째 div"는 학과/부서, "두 번째 div"가 실제 카테고리 역할
        sub_info_div = card_body.select_one("div.sub_info.mb-2")
        if sub_info_div:
            # 첫 번째
            dept_elem = sub_info_div.select_one("div.col-7.px-0.text-truncate")
            # 두 번째 (dept_elem 바로 다음 형제)
            category_elem = dept_elem.find_next_sibling("div") if dept_elem else None
    
            # 각각의 텍스트
            department = dept_elem.get_text(strip=True) if dept_elem else "부서 정보 없음"
            category = category_elem.get_text(strip=True) if category_elem else "카테고리 없음"
    
            # 예: 카테고리를 리스트 형태로 관리하고 싶다면
            categories = [department, category]
        else:
            categories = []
    
        # 설명 (프로그램 세부 내용)
        description_elem = card_body.select_one("p.card-text")
        description = description_elem.get_text(strip=True) if description_elem else "설명 없음"
    
        # 모집 기간
        recruitment_period = ""
        app_date_divs = card_body.select("div.row.app_date div.col-12")
        if app_date_divs:
            spans = app_date_divs[0].find_all("span")
            if len(spans) >= 2:
                recruitment_period = spans[1].get_text(strip=True)
    
        # 운영 기간
        operation_period = ""
        if len(app_date_divs) > 1:
            spans = app_date_divs[1].find_all("span")
            if len(spans) >= 2:
                operation_period = spans[1].get_text(strip=True)
    
        # 모집 인원 및 지원 인원 추출
        capacity_elem = card_body.select_one("span.total_member")
        applicants_elem = card_body.select_one("span.volun")
    
        # 숫자만 추출
        capacity_match = re.search(r"\d+", capacity_elem.get_text(strip=True) if capacity_elem else "")
        applicants_match = re.search(r"\d+", applicants_elem.get_text(strip=True) if applicants_elem else "")
    
        capacity = capacity_match.group() if capacity_match else "정보 없음"
        applicants = applicants_match.group() if applicants_match else "정보 없음"
    
        # 링크 (onclick 속성)
        link = ""
        onclick_attr = card_body.get("onclick")
        if onclick_attr:
            parts = onclick_attr.split("'")
            if len(parts) >= 2:
                link = parts[1]
                if link.startswith("/"):
                    link = "https://whalebe.pknu.ac.kr" + link
    
        programs.append({
            "title": title,
            "categories": categories,   # 수정된 부분
            "description": description,
            "recruitment_period": recruitment_period,
            "operation_period": operation_period,
            "capacity": capacity,
            "applicants": applicants,
            "href": link
        })
    
    programs.sort(key=lambda x: parse_date_range(x["recruitment_period"]) or datetime.min, reverse=True)
    return programs
################################################################################
#                       프로그램 알림 / 전송 함수                               #
################################################################################
async def send_program_notification(program: dict, target_chat_id: str) -> None:
    """비교과 프로그램 정보를 원본 페이지 구조에 가깝게 전송하는 함수"""

    # 프로그램 정보 추출 및 HTML escape 처리
    title = html.escape(program.get("title", "제목 없음"))
    categories = " > ".join(map(html.escape, program.get("categories", [])))  # 카테고리 (리스트)
    description = html.escape(program.get("description", "설명이 없습니다."))
    recruitment_period = html.escape(program.get("recruitment_period", "모집 기간 정보 없음"))
    operation_period = html.escape(program.get("operation_period", "운영 기간 정보 없음"))
    capacity_info = html.escape(program.get("capacity", "모집 인원 정보 없음"))
    applicants = html.escape(program.get("applicants", "지원자 정보 없음"))
    href = html.escape(program.get("href", "#"))

    # 모집 인원과 지원자 수 변환 (숫자만 추출)
    try:
        capacity_num = int(re.search(r"\d+", capacity_info).group()) if capacity_info.isdigit() else capacity_info
        applicants_num = int(re.search(r"\d+", applicants).group()) if applicants.isdigit() else applicants
        capacity_text = f"{capacity_num}명 / {applicants_num}명 지원"
    except Exception:
        capacity_text = "모집 인원 정보 없음"

    # 메시지 텍스트 구성
    message_text = (
        f"<b>{title}</b>\n"
        f"<i>{categories}</i>\n"
        "______________________________________________\n"
        f"{description}\n\n"
        f"📅 <b>모집 기간:</b> {recruitment_period}\n"
        f"📅 <b>운영 기간:</b> {operation_period}\n"
        f"👥 <b>{capacity_text}</b>\n"
    )

    # 인라인 버튼 생성
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔎 자세히 보기", url=href)]]
    )

    # 메시지 전송
    await bot.send_message(chat_id=target_chat_id, text=message_text, reply_markup=keyboard, parse_mode="HTML")

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

################################################################################
#                      명령어: /start, /register, /checknotices                 #
################################################################################
@dp.message(Command("start"))
async def start_command(message: types.Message) -> None:
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        await message.answer("죄송합니다. 이 봇은 사용 권한이 없습니다.\n등록하려면 /register [숫자 코드]를 입력해 주세요.")
        return
    if message.chat.type == "private":
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="공지사항", callback_data="notice_menu"),
                 InlineKeyboardButton(text="프로그램", callback_data="compare_programs")]
            ]
        )
        await message.answer("안녕하세요! 공지사항 봇입니다.\n\n아래 버튼을 선택해 주세요:", reply_markup=keyboard)
    else:
        await message.answer("이 그룹 채팅은 자동 알림용입니다.")

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

################################################################################
#                      인라인 콜백: 공지사항 메뉴, 날짜 필터 등                 #
################################################################################
@dp.callback_query(lambda c: c.data == "notice_menu")
async def notice_menu_handler(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅날짜 입력", callback_data="filter_date"),
         InlineKeyboardButton(text="📢전체 공지사항", callback_data="all_notices")]
    ])
    await callback.message.edit_text("공지사항 옵션을 선택하세요:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await callback.message.edit_text("MM/DD 형식으로 날짜를 입력해 주세요. (예: 01/31)")
    await state.set_state(FilterState.waiting_for_date)

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
    notices = await get_school_notices(category_code)
    
    if not notices:
        await callback.message.edit_text("해당 카테고리의 공지사항이 없습니다.")
    else:
        # 각 공지사항을 개별 메시지로 전송
        for notice in notices[:7]:  # 최대 7개 공지사항
            title = html.escape(notice[0])  # 제목 HTML 이스케이프
            department = html.escape(notice[2])  # 부서 HTML 이스케이프
            date_ = html.escape(notice[3])  # 날짜 HTML 이스케이프
            href = notice[1]  # 공지사항 링크

            # 공지사항 세부 내용 추출
            summary_text, image_urls = await extract_content(href)
            safe_summary = summary_text or ""

            # 메시지 텍스트 구성
            message_text = (
                f"[부경대 <b>{department}</b> 공지사항 업데이트]\n\n"
                f"<b>{title}</b>\n\n"
                f"{date_}\n\n"
                "______________________________________________\n"
                f"{safe_summary}\n\n"
            )

            # 이미지가 있을 경우 추가
            if image_urls:
                message_text += "\n".join(image_urls) + "\n\n"

            # 인라인 버튼 추가
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]]
            )

            # 개별 메시지 전송
            await callback.message.answer(message_text, reply_markup=keyboard, parse_mode="HTML")

    await state.clear()

################################################################################
#                      비교과(프로그램) 메뉴: 나만의 프로그램, 키워드 검색 등    #
################################################################################
@dp.callback_query(lambda c: c.data == "compare_programs")
async def compare_programs_handler(callback: CallbackQuery):
    await callback.answer()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="나만의 프로그램", callback_data="my_programs"),
         InlineKeyboardButton(text="키워드 검색", callback_data="keyword_search")]
    ])
    await callback.message.edit_text("비교과 프로그램 옵션을 선택하세요.", reply_markup=keyboard)

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

@dp.callback_query(lambda c: c.data == "my_programs")
async def my_programs_handler(callback: CallbackQuery):
    """필터에 맞는 프로그램을 개별 메시지로 기존 그룹 채팅 형식으로 전송"""
    
    await callback.answer()
    chat_id = callback.message.chat.id
    user_id_str = str(chat_id)
    
    # 허용된 사용자 확인
    if user_id_str not in ALLOWED_USERS:
        await callback.message.edit_text("등록된 사용자가 아닙니다. /register 명령어로 등록해 주세요.")
        return
    
    # 사용자 필터 확인
    user_filter = ALLOWED_USERS[user_id_str].get("filters", {})
    if not any(user_filter.values()):
        keyboard = get_program_filter_keyboard(chat_id)
        await callback.message.edit_text("현재 필터가 설정되어 있지 않습니다. 아래에서 필터를 설정해 주세요:", reply_markup=keyboard)
        return
    
    # 프로그램 가져오기
    programs = await get_programs(user_filter)
    
    # 필터에 맞는 프로그램이 없는 경우
    if not programs:
        await callback.message.edit_text("선택하신 필터에 해당하는 프로그램이 없습니다.")
        return
    
    # 프로그램 개별 메시지 전송 (그룹 채팅 형식 그대로)
    for program in programs:
        await send_program_notification(program, chat_id)  # 기존 그룹 채팅 형식 유지

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
    # 그룹1
    row1 = [
        InlineKeyboardButton(
            text=f"{'✅' if current.get(opt, False) else ''} {opt}".strip(),
            callback_data=f"toggle_program_{opt}"
        ) for opt in group1
    ]
    rows.append(row1)
    # 그룹2
    row2 = [
        InlineKeyboardButton(
            text=f"{'✅' if current.get(opt, False) else ''} {opt}".strip(),
            callback_data=f"toggle_program_{opt}"
        ) for opt in group2
    ]
    rows.append(row2)
    # 그룹3 (3개씩)
    group3_buttons = [
        InlineKeyboardButton(
            text=f"{'✅' if current.get(opt, False) else ''} {opt}".strip(),
            callback_data=f"toggle_program_{opt}"
        ) for opt in group3
    ]
    for i in range(0, len(group3_buttons), 3):
        rows.append(group3_buttons[i:i+3])

    rows.append([InlineKeyboardButton(text="선택 완료", callback_data="filter_done_program")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@dp.callback_query(lambda c: c.data.startswith("toggle_program_"))
async def toggle_program_filter(callback: CallbackQuery):
    await callback.answer()
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

@dp.callback_query(lambda c: c.data == "filter_done_program")
async def filter_done_program_handler(callback: CallbackQuery):
    await callback.answer()
    chat_id = callback.message.chat.id
    user_id_str = str(chat_id)
    user_filter = ALLOWED_USERS[user_id_str].get("filters", {})
    selected = [opt for opt, chosen in user_filter.items() if chosen]
    await callback.message.edit_text(f"선택한 필터: {', '.join(selected) if selected else '없음'}")

################################################################################
#                      키워드 검색 URL 생성 함수                               #
################################################################################
@dp.callback_query(lambda c: c.data == "keyword_search")
async def keyword_search_handler(callback: CallbackQuery, state: FSMContext):
    """키워드 검색을 시작하는 핸들러"""
    await callback.answer()
    await callback.message.edit_text("🔎 검색할 키워드를 입력해 주세요:")
    await state.set_state(KeywordSearchState.waiting_for_keyword)  # 상태 설정

def build_keyword_search_url(keyword: str) -> str:
    """
    입력된 키워드를 검색한 결과 페이지의 URL을 반환합니다.
    """
    base_url = "https://whalebe.pknu.ac.kr/main/65"
    params = {
        "pageIndex": 1,
        "action": "",
        "order": 0,
        "filterOF": 0,
        "all": 0,
        "intr": 0,
        "ridx": 0,
        "newAppr": 0,
        "rstOk": 0,
        "recvYn": 0,
        "aIridx": 0,
        "clsf": "",
        "type": "",
        "diag": "",
        "oneYy": 0,
        "twoYy": 0,
        "trdYy": 0,
        "std1": 0,
        "std2": 0,
        "std3": 0,
        "std4": 0,
        "deptCd": "",
        "searchKeyword": keyword  # 검색어 추가
    }
    return base_url + "?" + urllib.parse.urlencode(params)

################################################################################
#                      키워드 검색을 통한 프로그램 크롤링                       #
################################################################################
async def get_programs_by_keyword(keyword: str) -> list:
    """
    특정 키워드를 사용하여 비교과 프로그램을 검색하고, 필터링된 결과를 반환합니다.
    """
    url = build_keyword_search_url(keyword)
    html_content = await fetch_dynamic_html(url)  # Playwright를 사용하여 동적 페이지 가져오기

    if not html_content:
        logging.error(f"❌ 키워드 검색 실패: {keyword}")
        return []

    soup = BeautifulSoup(html_content, 'html.parser')
    programs = []

    program_items = soup.select("ul.flex-wrap > li")
    if not program_items:
        logging.debug("🔍 검색된 프로그램이 없습니다.")
        return []

    for item in program_items:
        card_body = item.select_one("div.card-body")
        if not card_body:
            continue

        # 제목
        title_elem = card_body.select_one("h4.card-title")
        title = title_elem.get_text(strip=True) if title_elem else "제목 없음"

        # 모집 마감 여부 확인
        status_elem = card_body.select_one("span.label-danger")  # "모집종료" 등의 상태를 나타냄
        if status_elem and "모집종료" in status_elem.get_text(strip=True):
            continue  # 모집 종료된 프로그램은 제외

        # 카테고리 및 학과 정보
        sub_info_div = card_body.select_one("div.sub_info.mb-2")
        if sub_info_div:
            dept_elem = sub_info_div.select_one("div.col-7.px-0.text-truncate")
            category_elem = dept_elem.find_next_sibling("div") if dept_elem else None
            department = dept_elem.get_text(strip=True) if dept_elem else "부서 정보 없음"
            category = category_elem.get_text(strip=True) if category_elem else "카테고리 없음"
            categories = [department, category]
        else:
            categories = []

        # 설명
        description_elem = card_body.select_one("p.card-text")
        description = description_elem.get_text(strip=True) if description_elem else "설명 없음"

        # 모집 기간
        recruitment_period = ""
        app_date_divs = card_body.select("div.row.app_date div.col-12")
        if app_date_divs:
            spans = app_date_divs[0].find_all("span")
            if len(spans) >= 2:
                recruitment_period = spans[1].get_text(strip=True)

        # 운영 기간
        operation_period = ""
        if len(app_date_divs) > 1:
            spans = app_date_divs[1].find_all("span")
            if len(spans) >= 2:
                operation_period = spans[1].get_text(strip=True)

        # 모집 인원 및 지원 인원
        capacity_elem = card_body.select_one("span.total_member")
        applicants_elem = card_body.select_one("span.volun")
        capacity = re.search(r"\d+", capacity_elem.get_text(strip=True)).group() if capacity_elem else "정보 없음"
        applicants = re.search(r"\d+", applicants_elem.get_text(strip=True)).group() if applicants_elem else "정보 없음"

        # 프로그램 상세 페이지 링크
        link = ""
        onclick_attr = card_body.get("onclick")
        if onclick_attr:
            parts = onclick_attr.split("'")
            if len(parts) >= 2:
                link = "https://whalebe.pknu.ac.kr" + parts[1] if parts[1].startswith("/") else parts[1]

        programs.append({
            "title": title,
            "categories": categories,
            "description": description,
            "recruitment_period": recruitment_period,
            "operation_period": operation_period,
            "capacity": capacity,
            "applicants": applicants,
            "href": link
        })

    programs.sort(key=lambda x: parse_date_range(x["recruitment_period"]) or datetime.min, reverse=True)
    return programs

################################################################################
#                      키워드 검색 핸들러 수정                                 #
################################################################################
@dp.message(KeywordSearchState.waiting_for_keyword)
async def process_keyword_search(message: types.Message, state: FSMContext):
    """키워드 입력을 처리하고, 검색된 프로그램을 가져와 전송"""
    keyword = message.text.strip()
    await state.clear()  # 상태 초기화

    await message.answer(f"🔍 '{keyword}' 키워드에 해당하는 프로그램을 검색 중입니다...")

    # 키워드 검색을 통한 프로그램 목록 가져오기
    programs = await get_programs_by_keyword(keyword)

    if not programs:
        await message.answer(f"❌ '{keyword}' 키워드에 해당하는 프로그램이 없습니다.")
    else:
        for program in programs:
            await send_program_notification(program, message.chat.id)  # 개별 메시지 전송
            
################################################################################
#                      날짜 필터 / 공지사항 표시 로직                           #
################################################################################
@dp.callback_query(lambda c: c.data == "filter_date")
async def callback_filter_date(callback: CallbackQuery, state: FSMContext) -> None:
    """날짜 필터링 시작"""
    await callback.answer()
    await callback.message.edit_text("📅 MM/DD 형식으로 날짜를 입력해 주세요. (예: 01/31)")
    await state.set_state(FilterState.waiting_for_date)  # 날짜 입력 상태 설정

@dp.message(FilterState.waiting_for_date)
async def process_date_input(message: types.Message, state: FSMContext) -> None:
    """날짜 입력을 처리하는 핸들러"""
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        await message.answer("❌ 접근 권한이 없습니다.")
        return

    # 날짜 입력 처리
    input_text = message.text.strip()
    current_year = datetime.now().year  # 현재 연도
    full_date_str = f"{current_year}-{input_text.replace('/', '-')}"  # MM/DD -> YYYY-MM-DD 변환
    filter_date = parse_date(full_date_str)

    if filter_date is None:
        await message.answer("⚠️ 날짜 형식이 올바르지 않습니다. MM/DD 형식으로 다시 입력해 주세요.")
        return

    # 공지사항 필터링
    all_notices = await get_school_notices()
    filtered_notices = [n for n in all_notices if parse_date(n[3]) == filter_date]

    if not filtered_notices:
        await message.answer(f"📢 {input_text} 날짜에 해당하는 공지사항이 없습니다.")
    else:
        for notice in filtered_notices:
            title, href, department, date_ = notice

            # 공지사항 본문 요약 및 이미지 추출
            summary_text, image_urls = await extract_content(href)
            safe_summary = summary_text or ""

            # 메시지 텍스트 구성
            message_text = (
                f"[부경대 <b>{html.escape(department)}</b> 공지사항 업데이트]\n\n"
                f"<b>{html.escape(title)}</b>\n\n"
                f"{html.escape(date_)}\n\n"
                "______________________________________________\n"
                f"{safe_summary}\n\n"
            )

            # 이미지가 있으면 추가
            if image_urls:
                message_text += "\n".join(image_urls) + "\n\n"

            # 인라인 버튼 추가
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="🔎 자세히 보기", url=href)]]
            )

            # 개별 메시지 전송
            await message.answer(message_text, reply_markup=keyboard, parse_mode="HTML")

    await state.clear()  # 상태 초기화
    
################################################################################
#                      'catch_all' 핸들러 (기타 메시지)                          #
################################################################################
@dp.message()
async def catch_all(message: types.Message):
    """기타 메시지를 받는 핸들러 (충돌 방지)"""
    await message.answer("⚠️ 유효하지 않은 명령어입니다. 메뉴에서 선택해 주세요.")

################################################################################
#                     새 공지사항 / 프로그램 자동 전송 (그룹채팅)               #
################################################################################
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
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]]
    )
    await bot.send_message(chat_id=target_chat_id, text=message_text, reply_markup=keyboard)

################################################################################
#                              run_bot()                                       #
################################################################################
async def run_bot() -> None:
    # 시작 시점에 체크 (그룹채팅에 자동 전송)
    await check_for_new_notices()
    await check_for_new_programs(GROUP_CHAT_ID)

    try:
        logging.info("🚀 Starting bot polling for 10 minutes...")
        polling_task = asyncio.create_task(dp.start_polling(bot))
        await asyncio.sleep(600)  # 10분
        logging.info("🛑 Stopping bot polling after 10 minutes...")
        polling_task.cancel()
        await dp.stop_polling()
    except Exception as e:
        logging.error(f"❌ Bot error: {e}", exc_info=True)
    finally:
        await bot.session.close()
        logging.info("✅ Bot session closed.")

################################################################################
#                               메인 실행부                                     #
################################################################################
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
                await new_bot.send_message(
                    CHAT_ID,
                    f"봇이 오류로 종료되었습니다:\n{e}\n\n재실행 해주세요."
                )
                await new_bot.session.close()
            except Exception as notify_error:
                logging.error(f"❌ 알림 전송 실패: {notify_error}", exc_info=True)
        
        asyncio.run(notify_crash())
