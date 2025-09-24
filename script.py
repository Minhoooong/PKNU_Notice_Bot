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
import easyocr
import io
from datetime import datetime
from logging.handlers import RotatingFileHandler

import aiohttp
from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
from urllib.parse import quote

################################################################################
#                               환경 변수 / 토큰 / 상수 설정                   #
################################################################################
aclient = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
TOKEN = os.environ.get('TELEGRAM_TOKEN')
CHAT_ID = os.environ.get('CHAT_ID')
GROUP_CHAT_ID = os.environ.get('GROUP_CHAT_ID')
REGISTRATION_CODE = os.environ.get('REGISTRATION_CODE')

# ▼ PKNU AI 비교과 로그인을 위한 학번
PKNU_USERNAME = os.environ.get('PKNU_USERNAME')

URL = 'https://www.pknu.ac.kr/main/163'
BASE_URL = 'https://www.pknu.ac.kr'
CACHE_FILE = "announcements_seen.json"
WHITELIST_FILE = "whitelist.json"

# ▼ PKNU AI 비교과 시스템
PKNUAI_BASE_URL = "https://pknuai.pknu.ac.kr"
PKNUAI_PROGRAM_CACHE_FILE = "programs_seen.json"

logging.info("EasyOCR 리더를 로딩합니다... (최초 실행 시 시간이 걸릴 수 있습니다)")
try:
    # verbose=False 옵션을 추가하여 불필요한 로그 출력을 비활성화합니다.
    ocr_reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    logging.info("✅ EasyOCR 로딩 완료!")
except Exception as e:
    logging.error(f"❌ EasyOCR 로딩 실패: {e}", exc_info=True)
    ocr_reader = None  # 로딩 실패 시 ocr_reader를 None으로 설정

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

async def fetch_program_html(keyword: str = None, filters: dict = None) -> str:
    """
    PKNU AI 비교과 페이지 HTML 수집 (URL 직접 구성 방식, 안정성 강화):
      1) 학번이 포함된 URL로 직접 접속하여 로그인 세션을 생성.
      2) 키워드나 필터가 있으면, 이를 포함한 최종 URL을 직접 구성.
      3) 구성된 URL로 바로 이동하여 HTML을 한 번에 가져옴.
      4) 브라우저 리소스를 안정적으로 관리 및 종료.
    """
    if not PKNU_USERNAME:
        logging.error("❌ PKNU_USERNAME 환경 변수가 설정되지 않았습니다.")
        return ""

    logging.info(f"🚀 Playwright 작업 시작 (검색어: {keyword}, 필터: {filters})")

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"),
                locale="ko-KR",
            )
            page = await context.new_page()

            login_bridge_url = f"https://pknuai.pknu.ac.kr/web/login/pknuLoginProc.do?mId=3&userId={PKNU_USERNAME}"
            await page.goto(login_bridge_url, wait_until="networkidle", timeout=60000)

            target_url = "https://pknuai.pknu.ac.kr/web/nonSbjt/program.do?mId=216&order=3"
            if keyword:
                encoded_keyword = quote(keyword)
                target_url += f"&searchKeyword={encoded_keyword}"

            await page.goto(target_url, wait_until="networkidle", timeout=60000)

            if filters and any(filters.values()):
                logging.info(f"필터를 적용합니다: {filters}")
                for filter_name, is_selected in filters.items():
                    if is_selected:
                        input_id = PROGRAM_FILTER_MAP.get(filter_name)
                        if input_id:
                            # 올바른 CSS 선택자 label[for='...'] 를 사용합니다.
                            await page.click(f"label[for='{input_id}']")
                await page.wait_for_load_state("networkidle", timeout=30000)
                logging.info("필터 적용 완료.")

            content = await page.content()
            logging.info("✅ Playwright 크롤링 성공")
            return content

        except Exception as e:
            logging.error(f"❌ Playwright 크롤링 중 오류 발생: {e}", exc_info=True)
            return ""
        finally:
            if browser:
                await browser.close()
            
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

# script.py

# script.py

async def summarize_text(text: str) -> str:
    """
    사용자의 상황에 맞춰 공지사항의 유용성을 분석하고 실행을 돕는 AI 요약 함수 (고도화 버전).
    """
    if not text or not text.strip():
        return "요약할 수 없는 공지입니다."

    # 민홍 님의 저장된 프로필을 기반으로 동적으로 페르소나를 구성합니다.
    user_profile = """
    - <b>분석 대상:</b> 김민홍 (부경대학교 기계공학과 2학년)
    - <b>주요 관심사:</b> 신기술 동향, 생산성 향상(Obsidian 등), 철학
    - <b>핵심 목표:</b> 스펙 향상에 도움이 되는 의미 있는 경험, 장학금 등 실질적 혜택
    - <b>고려사항:</b> 새로운 것을 시도하는 데 있어 '실천'의 장벽을 낮추는 것을 선호함. 명확하고 간단한 절차를 중요하게 생각함.
    """

    prompt = f"""
당신은 부경대학교 기계공학과 선배로서, 후배인 김민홍 학생에게 공지사항을 설명해주는 역할을 합니다. 
아래 '후배 프로필'과 '분석 프레임워크'에 따라, 주어진 '공지사항 원문'을 분석하고 지정된 '출력 형식'에 맞춰 친절하게 요약해주세요.

### 후배 프로필
{user_profile}

### 분석 프레임워크 (Chain-of-Thought)
1.  **주제 파악:** 이 공지의 핵심이 무엇인가? (예: 장학금 신청, 공모전, 특강 안내 등)
2.  **관련성 평가:** 이 내용이 기계공학과 2학년의 전공 지식이나 관심사(신기술, 생산성)와 관련이 있는가? 지금 당장 참여할 만한 가치가 있는가?
3.  **가성비 분석:** 투입해야 할 시간과 노력 대비 얻을 수 있는 혜택(경험, 장학금, 마일리지 등)이 합리적인가?
4.  **실행 가능성 검토:** 신청 절차가 복잡한가, 간단한가? 마감 기한이 촉박한가? 후배가 실천에 옮기기 쉬운 편인가?
5.  **최종 종합:** 위의 분석 내용을 바탕으로, 지정된 출력 형식에 맞춰 핵심 정보를 간결하고 명확하게 정리한다.

### 출력 형식 (반드시 이 형식을 따라주세요)
<b>1. 🔑 핵심 요약</b>
- 이 공지를 한 문장으로 요약하면?

<b>2. 🤔 이 공지가 민홍님께 왜 중요할까요?</b>
- <b>전공/관심사 관련도:</b> (기계공학 전공, 신기술, 생산성 향상 등 민홍님의 관심사와 얼마나 관련 있는지 분석)
- <b>가성비 (노력 대비 보상):</b> (예상되는 노력과 시간을 고려했을 때, 얻게 될 혜택이 얼마나 매력적인지 분석)
- <b>실천 용이성:</b> (신청 절차의 복잡성, 준비 서류, 마감 기한 등을 고려하여 얼마나 쉽게 도전할 수 있는지 분석)

<b>3. 📋 상세 정보</b>
- <b>지원 자격:</b>
- <b>주요 혜택:</b>
- <b>모집/운영 기간:</b>
- <b>신청 방법:</b>
- <b>문의처:</b>

<b>4. 🚀 추천 액션</b>
- (분석 결과를 바탕으로 민홍님이 지금 당장 할 수 있는 가장 간단하고 명확한 다음 행동을 제시. 예: '관심 있다면 지금 바로 캘린더에 마감일 메모하기', '첨부파일 다운로드해서 지원 자격만 빠르게 확인하기', '이번에는 가볍게 넘어가도 좋아요' 등)

### 추가 규칙
- 각 항목에 대한 정보가 원문에 없으면, 반드시 "정보 없음"이라고 명확히 기재해주세요.
- 중요한 키워드는 `<b>` 태그로 감싸서 강조하고, `**`는 사용하지 마세요.
- 전체적인 톤은 친하고 똑똑한 선배가 후배에게 조언해주는 것처럼 작성해주세요.
"""
    try:
        response = await aclient.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"### 공지사항 원문\n{text}"}
            ],
            temperature=0.2,
            max_tokens=1500
        )
        summary = response.choices[0].message.content.strip()
        # 이중 **를 <b>로 변환
        summary = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', summary)
        return summary
    except Exception as e:
        logging.error(f"❌ OpenAI API 요약 오류: {e}", exc_info=True)
        return "요약 중 오류가 발생했습니다. 나중에 다시 시도해주세요."
        
async def ocr_image_from_url(session: aiohttp.ClientSession, url: str) -> str:
    """URL에서 이미지를 비동기적으로 받아 OCR을 수행하고 텍스트를 반환합니다."""
    if not ocr_reader:
        logging.warning("OCR 리더가 초기화되지 않아 이미지 처리를 건너뜁니다.")
        return ""
    try:
        async with session.get(url) as response:
            if response.status != 200:
                logging.error(f"이미지 다운로드 실패: {url}, 상태 코드: {response.status}")
                return ""
            image_bytes = await response.read()

            # EasyOCR의 readtext는 동기 함수이므로 asyncio.to_thread로 실행하여 이벤트 루프 블로킹 방지
            result = await asyncio.to_thread(
                ocr_reader.readtext, image_bytes, detail=0
            )

            logging.info(f"이미지 OCR 완료: {url}")
            return " ".join(result)
    except Exception as e:
        logging.error(f"이미지 OCR 처리 중 오류 발생 {url}: {e}", exc_info=True)
        return ""

async def extract_content(url: str) -> tuple:
    """
    웹페이지 본문을 추출하고, 텍스트가 부족하면 이미지에서 OCR을 수행하여 요약합니다.
    """
    try:
        html_content = await fetch_url(url)
        if not html_content:
            return ("페이지 내용을 불러올 수 없습니다.", [])

        soup = BeautifulSoup(html_content, "html.parser")
        container = soup.find("div", class_="bdvTxt_wrap") or soup

        # 1. 원본 텍스트 추출
        raw_text = " ".join(container.get_text(separator=" ", strip=True).split())
        # 2. 이미지 URL 목록 추출
        images = [
            urllib.parse.urljoin(url, img["src"])
            for img in container.find_all("img")
            if img.get("src")
        ]

        summary_text = ""
        # 3. 텍스트가 100자 미만으로 매우 적고 이미지가 있을 경우에만 OCR 수행
        if (not raw_text or len(raw_text) < 100) and images:
            logging.info(f"텍스트가 부족하여 이미지 OCR을 시도합니다: {url}")
            # aiohttp 세션을 한 번 생성하여 모든 이미지 다운로드에 재사용
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                tasks = [ocr_image_from_url(session, img_url) for img_url in images]
                ocr_texts = await asyncio.gather(*tasks)

            full_ocr_text = "\n".join(filter(None, ocr_texts))

            if full_ocr_text.strip():
                # OCR로 추출된 텍스트를 요약
                summary_text = await summarize_text(full_ocr_text)
            else:
                summary_text = "이미지가 있으나 텍스트를 추출할 수 없었습니다."
        else:
            # 텍스트가 충분하면 기존 방식대로 텍스트 요약
            summary_text = await summarize_text(raw_text)

        return (summary_text, images)

    except Exception as e:
        logging.error(f"❌ 본문 내용 추출 오류 {url}: {e}", exc_info=True)
        return ("내용 처리 중 오류가 발생했습니다.", [])

# ▼ 추가: PKNU AI 비교과 파싱 함수
# script.py

def _parse_pknuai_page(soup: BeautifulSoup) -> list:
    """PKNU AI 시스템의 HTML을 파싱하여 프로그램 목록 반환 (상세 페이지 URL 추출)"""
    programs = []
    items = soup.select("li.col-xl-3.col-lg-4.col-md-6")

    for li in items:
        card_body = li.select_one(".card-body[data-url]")
        if not card_body:
            continue
            
        title_element = li.select_one("h5 a.ellip_2")
        title = title_element.get_text(strip=True) if title_element else "제목 없음"
        
        yy = card_body.get("data-yy")
        shtm = card_body.get("data-shtm")
        nonsubjc_cd = card_body.get("data-nonsubjc-cd")
        nonsubjc_crs_cd = card_body.get("data-nonsubjc-crs-cd")
        
        if not all([yy, shtm, nonsubjc_cd, nonsubjc_crs_cd]):
            continue
            
        detail_url = (f"{PKNUAI_BASE_URL}/web/nonSbjt/programDetail.do?mId=216&order=3&"
                      f"yy={yy}&shtm={shtm}&nonsubjcCd={nonsubjc_cd}&nonsubjcCrsCd={nonsubjc_crs_cd}")

        programs.append({
            "title": title,
            "href": detail_url,
            "unique_id": f"{yy}-{shtm}-{nonsubjc_cd}-{nonsubjc_crs_cd}"
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
    """
    공지사항 알림을 전송하는 함수 (첫 이미지를 캡션과 함께 전송)
    """
    title, href, department, date_ = notice
    summary, images = await extract_content(href)
    
    # 1. 메시지 본문과 키보드를 미리 준비합니다.
    message_text = (
        f"<b>[부경대 {html.escape(department)} 공지]</b>\n{html.escape(title)}\n\n"
        f"<i>{html.escape(date_)}</i>\n______________________________________________\n{summary}"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="자세히 보기", url=href)]]
    )

    # 2. 이미지가 있는 경우, 첫 이미지를 캡션과 함께 전송 시도
    if images:
        try:
            # aiohttp 세션을 사용하여 첫 번째 이미지만 비동기적으로 다운로드
            async with aiohttp.ClientSession() as session:
                async with session.get(images[0]) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        photo_file = BufferedInputFile(image_bytes, filename="photo.jpg")
                        
                        # 사진 전송 API를 사용하여 이미지와 텍스트(캡션)를 한 번에 보냅니다.
                        await bot.send_photo(
                            chat_id=target_chat_id,
                            photo=photo_file,
                            caption=message_text,
                            reply_markup=keyboard,
                            parse_mode="HTML"
                        )
                        return # 성공적으로 보내면 함수 종료

        except Exception as e:
            logging.error(f"이미지와 함께 메시지 전송 실패 (텍스트만 전송으로 대체): {e}", exc_info=True)
            # 실패 시 사용자에게 간단히 알릴 수 있습니다.
            message_text += "\n\n<i>(공지 이미지를 불러오는 데 실패했습니다.)</i>"

    # 3. 이미지가 없거나, 전송에 실패한 경우 텍스트 메시지만 보냅니다.
    await bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True # 텍스트만 보낼 땐 링크 미리보기 비활성화
    )

# ▼ 추가: PKNU AI 프로그램 알림 전송 함수
async def send_pknuai_program_notification(program: dict, summary: str, target_chat_id: str):
    """
    GPT가 요약한 AI 비교과 프로그램의 상세 정보를 전송하는 함수.
    """
    title = html.escape(program.get("title", "제목 없음"))

    message_text = (
        f"<b>[AI 비교과 프로그램]</b>\n"
        f"<b>{title}</b>\n"
        f"______________________________________________\n\n"
        f"{summary}"
    )
    
    # 상세 내용이 요약에 모두 포함되므로 링크 버튼 제거
    await bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

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
# script.py

async def check_for_new_pknuai_programs(target_chat_id: str):
    logging.info("새로운 AI 비교과 프로그램을 확인합니다...")
    seen = load_pknuai_program_cache()
    # '나만의 프로그램(필터)'가 아닌 일반적인 신규 프로그램 확인이므로 filters=None
    html_content = await fetch_program_html()
    if not html_content:
        return
        
    soup = BeautifulSoup(html_content, 'html.parser')
    current_programs_list = _parse_pknuai_page(soup)
    found = False

    for program_summary in current_programs_list:
        key = generate_cache_key(program_summary['title'], program_summary['unique_id'])
        if key not in seen:
            logging.info(f"새 AI 비교과 프로그램 발견: {program_summary['title']}")
            
            detail_html = await fetch_program_html(detail_url=program_summary['href'])
            if not detail_html:
                continue

            soup = BeautifulSoup(detail_html, 'html.parser')
            content_area = soup.select_one(".wh-body")
            detail_text = content_area.get_text(strip=True) if content_area else ""
            
            summary = await summarize_text(detail_text)

            await send_pknuai_program_notification(program_summary, summary, target_chat_id)

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
    # 역량별
    "주도적 학습", "통섭적 사고", "확산적 연계",
    "협력적 소통", "문화적 포용", "사회적 실천",
    # 학년별
    "1학년", "2학년", "3학년", "4학년",
    # 유형별
    "학생 학습역량 강화", "진로·심리 상담 지원", "취·창업 지원", "기타 활동"
]

PROGRAM_FILTER_MAP = {
    # 역량별
    "주도적 학습": "diag_A01", "통섭적 사고": "diag_A02", "확산적 연계": "diag_A03",
    "협력적 소통": "diag_B01", "문화적 포용": "diag_B02", "사회적 실천": "diag_B03",
    # 학년별
    "1학년": "std_1", "2학년": "std_2", "3학년": "std_3", "4학년": "std_4",
    # 유형별
    "학생 학습역량 강화": "clsf_A01", "진로·심리 상담 지원": "clsf_A02",
    "취·창업 지원": "clsf_A03", "기타 활동": "clsf_A04"
}

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
        keyboard = get_program_filter_keyboard(callback.message.chat.id)
        await callback.message.edit_text("🎯 먼저 필터를 선택해주세요:", reply_markup=keyboard)
        return

    status_msg = await callback.message.edit_text("📊 필터로 검색 중... (로그인 필요)")

    html_content = await fetch_program_html(filters=user_filters)
    
    await status_msg.delete()

    programs = []
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        programs = _parse_pknuai_page(soup)

    if not programs:
        await callback.message.answer("조건에 맞는 프로그램이 없습니다.")
    else:
        # 결과를 하나씩 순차적으로 전송합니다.
        for program in programs:
            # 상세 페이지 내용을 가져와 요약합니다.
            detail_html = await fetch_url(program['href'])
            detail_text = ""
            if detail_html:
                detail_soup = BeautifulSoup(detail_html, 'html.parser')
                content_area = detail_soup.select_one(".wh-body")
                if content_area:
                    detail_text = content_area.get_text(strip=True)
            
            summary = await summarize_text(detail_text)
            await send_pknuai_program_notification(program, summary, callback.message.chat.id)
            
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

    status_msg = await message.answer(f"🔍 '{keyword}' 키워드로 검색 중입니다... (로그인 필요)")
    html_content = await fetch_program_html(keyword=keyword)

    await status_msg.delete()

    programs = []
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        programs = _parse_pknuai_page(soup)

    if not programs:
        await message.answer(f"❌ '{keyword}' 키워드에 해당하는 프로그램이 없습니다.")
    else:
        # 결과를 하나씩 순차적으로 전송합니다.
        for program in programs:
            # 상세 페이지 내용을 가져와 요약합니다.
            detail_html = await fetch_url(program['href'])
            detail_text = ""
            if detail_html:
                detail_soup = BeautifulSoup(detail_html, 'html.parser')
                content_area = detail_soup.select_one(".wh-body")
                if content_area:
                    detail_text = content_area.get_text(strip=True)
            
            summary = await summarize_text(detail_text)
            await send_pknuai_program_notification(program, summary, message.chat.id)

################################################################################
#                            기타 상태 및 메시지 핸들러                            #
################################################################################
def parse_date(date_str: str):
    """다양한 날짜 형식을 처리하는 함수"""
    try:
        return datetime.strptime(date_str, "%Y.%m.%d")
    except ValueError:
        return None
        
# 기존 process_date_input 함수를 지우고 아래 최종 버전으로 교체하세요.
@dp.message(FilterState.waiting_for_date)
async def process_date_input(message: types.Message, state: FSMContext) -> None:
    """날짜 입력을 처리하는 핸들러 (디버깅 강화 및 숫자 비교 방식)"""
    # --- 생략되었던 권한 확인 부분 ---
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        await message.answer("❌ 접근 권한이 없습니다.")
        return
    # ---------------------------------

    input_text = message.text.strip()
    try:
        month, day = map(int, input_text.split('/'))
    except ValueError:
        # --- 생략되었던 오류 처리 부분 ---
        await message.answer("⚠️ 날짜 형식이 올바르지 않습니다. MM/DD 형식으로 다시 입력해 주세요.")
        return
        # ---------------------------------

    await state.clear()
    await message.answer(f"📅 {month}월 {day}일 날짜의 공지사항을 검색합니다...")
    
    all_notices = await get_school_notices()
    
    filtered_notices = []
    logging.info(f"사용자 요청 날짜: Month={month}, Day={day}") # 디버깅 로그 추가

    for notice_tuple in all_notices:
        notice_date_str = notice_tuple[3]
        try:
            notice_date_obj = datetime.strptime(notice_date_str, "%Y.%m.%d")
            # 비교 직전에 로그를 남겨서 확인
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

