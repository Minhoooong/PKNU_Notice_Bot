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

class PersonalizationState(StatesGroup):
    selecting_college = State() # 단과대학 선택 중
    selecting_department = State() # 세부학과 선택 중

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

async def fetch_program_html(url: str, keyword: str = None, filters: dict = None) -> str:
    """
    Playwright를 사용하여 로그인 세션을 유지하며 지정된 URL의 HTML을 가져오는 범용 함수.
    """
    if not PKNU_USERNAME:
        logging.error("❌ PKNU_USERNAME 환경 변수가 설정되지 않았습니다.")
        return ""

    logging.info(f"🚀 Playwright 작업 시작 (URL: {url})")
    
    async with async_playwright() as p:
        browser = None
        page = None
        try:
            browser = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                locale="ko-KR",
            )
            page = await context.new_page()

            # 최초 접근 시에만 로그인 브리지 URL을 사용
            login_bridge_url = f"https://pknuai.pknu.ac.kr/web/login/pknuLoginProc.do?mId=3&userId={PKNU_USERNAME}"
            await page.goto(login_bridge_url, wait_until="networkidle")
            logging.info("Playwright 세션 로그인 성공.")

            # 실제 목표 URL로 이동
            target_url = url
            if keyword:
                target_url = f"https://pknuai.pknu.ac.kr/web/nonSbjt/program.do?mId=216&order=3&searchKeyword={quote(keyword)}"

            logging.info(f"타겟 URL로 이동: {target_url}")
            await page.goto(target_url, wait_until="networkidle")

            if filters and any(filters.values()):
                logging.info(f"필터를 적용합니다: {filters}")
                for filter_name, is_selected in filters.items():
                    if is_selected:
                        input_id = PROGRAM_FILTER_MAP.get(filter_name)
                        if input_id:
                            await page.click(f"label[for='{input_id}']")
                await page.wait_for_load_state("networkidle")

            return await page.content()

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

async def summarize_text(text: str, original_title: str, user_id: str = None) -> dict:
    """
    공지사항 원문과 원본 제목을 받아, 정제된 제목과 AI 요약문을 포함한 딕셔너리를 반환하는 고도화된 함수.
    (사용자 ID를 받아 개인화된 분석 관점을 적용)
    """
    if not text or not text.strip():
        return {"refined_title": original_title, "summary_body": "요약할 수 없는 공지입니다."}

    # 기본 분석 관점
    analysis_viewpoint = """
    - <b>대상:</b> 모든 부경대학교 학부생
    - <b>핵심 평가 기준:</b>
        1. <b>혜택의 보편성:</b> 얼마나 많은 학생에게 실질적인 이득(장학금, 경력, 경험 등)이 되는가?
        2. <b>참여의 용이성:</b> 특정 학과/학년에 제한되지 않고 누구나 쉽게 참여할 수 있는가?
        3. <b>시의성 및 중요도:</b> 등록금, 수강신청 등 다수의 학생에게 영향을 미치는 중요한 학사일정인가?
    """

    # 개인화 설정이 켜져 있고, 사용자 ID가 있는 경우 분석 관점을 동적으로 생성
    if user_id:
        user_settings = ALLOWED_USERS.get(user_id, {}).get("personalization", {})
        if user_settings.get("enabled"):
            profile_parts = []
            criteria_parts = []

            # 프로필 조합
            selected_grade = user_settings.get("학년", "전체학년")
            if selected_grade != "전체학년":
                profile_parts.append(selected_grade)

            selected_dept = user_settings.get("전공학과", "전체학과")
            if selected_dept != "전체학과":
                profile_parts.append(selected_dept)

            # 관심분야에 따라 평가 기준 추가
            selected_interests = user_settings.get("관심분야", [])
            if not selected_interests:
                 criteria_parts.append("일반적인 학업 및 교내 활동에 대한 중요도")
            else:
                criteria_map = {
                    "취업": "채용, 인턴 등 취업 준비와의 직접적인 연관성", "채용": "채용 공고와의 직접적인 연관성",
                    "인턴": "인턴십 기회 제공 여부", "현장실습": "현장실습 기회 제공 여부",
                    "장학금": "장학금 수혜 가능성 및 금액", "등록금": "등록금 관련 중요 안내",
                    "공모전": "수상 경력 및 스펙 획득 가능성", "경진대회": "경진대회 참여 기회", "대외활동": "새로운 경험 및 인맥 형성 기회",
                    "특강": "관심 분야 지식 및 역량 강화 기회", "워크숍": "실습 중심의 역량 강화 기회", "교내활동": "교내 행사 및 활동 참여 기회",
                    "학사일정": "졸업, 수강신청 등 필수 학업 일정과의 관련성", "수강신청": "수강신청 관련 중요 안내", "졸업": "졸업 요건 및 절차 관련성",
                    "창업": "창업 지원 및 아이디어 실현 기회", "상담": "진로, 심리 등 상담 프로그램 제공 여부",
                    "봉사": "봉사활동 시간 인정 및 참여 기회", "자격증": "자격증 취득 지원 여부",
                    "대학원": "대학원 진학 및 연구 관련 정보"
                }
                for interest in selected_interests:
                    if interest in criteria_map:
                        criteria_parts.append(criteria_map[interest])

            target_audience = " ".join(profile_parts) if profile_parts else "모든 부경대 학생"
            
            # 최종 analysis_viewpoint 생성
            analysis_viewpoint = (
                f"- <b>대상:</b> {target_audience}의 관점에서 분석\n"
                f"- <b>핵심 평가 기준:</b>\n"
                + "\n".join([f"    {i+1}. <b>{part}</b>" for i, part in enumerate(criteria_parts)])
            )

    prompt = f"""
당신은 부경대학교 학생들을 위한 똑똑한 AI 조교입니다.
아래 '분석 관점'과 '작업 규칙'에 따라 '공지사항 원문'을 분석하고, 지정된 '출력 형식'으로만 요약해주세요.

### 분석 관점
{analysis_viewpoint}

### 작업 규칙 (매우 중요)
1.  **제목 정제:** '공지사항 원본 제목'에서 날짜, 이모지, 부서명 등 불필요한 수식어는 제거하고 핵심 내용만 남겨 간결한 제목으로 만든다.
2.  **정보 추출 강화:** '정보 없음'을 최소화해야 한다. 각 항목에 해당하는 내용이 있는지 원문을 여러 번 읽고, 명시적인 단어가 없더라도 문맥을 통해 **반드시 내용을 추론하여 채워넣는다.**
3.  **중요도 평가 보정 (5점 척도):** 아래의 엄격한 기준에 따라 중요도를 ⭐ 1개에서 5개까지로 평가한다.
    - ⭐⭐⭐⭐⭐ (필수/긴급): 수강신청, 등록금, 성적, 졸업 등 **모든 학생의 학사에 직접적이고 긴급한 영향을 미치는 공지.**
    - ⭐⭐⭐⭐ (강력 추천): 전체 대상 주요 장학금, 대규모 채용/공모전 등 **놓치면 매우 아쉬운 핵심 기회.**
    - ⭐⭐⭐ (확인 권장): 특정 단과대/학과 대상의 중요 공지, 유용한 특강, 인기 비교과 프로그램 등.
    - ⭐⭐ (관심 시 확인): 소수 대상 행사, 동아리 모집, 일반적인 대외활동 등.
    - ⭐ (참고): 단순 정보 공지, 시설 안내, 홍보 등.
4.  **평가 근거 형식:** '평가 근거'는 완전한 문장이 아닌, '전체 학생 대상, 성적 장학금, 높은 중요도' 와 같이 **핵심 키워드를 명사형으로 나열**하여 간결하게 제시한다.
5.  **추천 액션 구체화:** 아래 기준을 종합적으로 고려하여 **실질적인 다음 행동**을 1~2개 제안한다.
    - **마감 임박성:** 마감까지 3일 이내 남았다면 "마감이 임박했어요, 지금 바로 신청하세요!" 와 같이 긴급성을 강조.
    - **혜택의 희소성:** 선착순이거나 혜택이 매우 좋다면 "인기 많은 활동이니 빠르게 지원하는 걸 추천해요." 라고 제안.
    - **절차의 간편성:** 신청 방법이 간단하면 "절차가 간단하니 5분만 투자해서 신청해보세요." 라고 실천 장벽을 낮춰줌.
6.  **다양하고 일관된 태그 생성:** 아래 예시 목록을 참고하여, 가장 관련 있는 태그를 2~5개 선택하여 맨 마지막에 추가한다. **단과대학, 학과 태그는 공지 내용과 관련 있을 경우에만 추가한다.**
    - [분야] #학사일정 #장학금 #취업 #채용 #인턴 #공모전 #특강 #대외활동 #교내활동 #프로그램 #마일리지
    - [단과대학] #공과대학 #인문사회과학대학 #자연과학대학 #경영대학 #수산과학대학 #정보융합대학
    - [주요학과] #기계공학과 #컴퓨터공학과 #IT융합응용공학과 #데이터정보과학부 #경영학과

### 출력 형식 (Key-Value JSON 형식)
{{
    "refined_title": "AI가 정제한 새로운 공지 제목",
    "summary_body": "<b>⭐⭐⭐(여기 별 개수를 수정) 한 줄 요약</b>\\n- *평가 근거: 명사형 키워드 나열*\\n\\n<b>📋 핵심 정보</b>\\n- <b>지원 자격:</b> ...\\n- <b>주요 혜택:</b> ...\\n- <b>모집/운영 기간:</b> ...\\n- <b>신청 방법:</b> ...\\n- <b>문의처:</b> ...\\n\\n<b>🚀 추천 액션</b>\\n- ...\\n\\n<b>#️⃣ 관련 태그</b>\\n- ..."
}}
"""
    try:
        response = await aclient.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"### 공지사항 원본 제목\n{original_title}\n\n### 공지사항 원문\n{text}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1500
        )
        result = json.loads(response.choices[0].message.content)
        result["summary_body"] = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', result.get("summary_body", ""))
        return result
    except Exception as e:
        logging.error(f"❌ OpenAI API 요약 오류: {e}", exc_info=True)
        return {"refined_title": original_title, "summary_body": "요약 중 오류가 발생했습니다."}
        
async def summarize_program_details(details: dict, original_title: str) -> dict:
    """
    파싱된 비교과 프로그램 상세 정보를 받아 AI로 재가공 및 요약하는 함수.
    """
    # AI에게 전달할 정보를 문자열로 변환
    input_text = "\n".join([f"- {key}: {value}" for key, value in details.items()])

    prompt = f"""
당신은 부경대학교 학생들을 위한 똑똑한 AI 조교입니다.
아래 '작업 규칙'에 따라 '비교과 프로그램 정보'를 분석하고, 지정된 '출력 형식'으로만 요약해주세요.

### 작업 규칙 (매우 중요)
1.  **핵심 정보 요약:** '내용', '모집안내', '신청안내' 등 여러 항목에 흩어진 정보를 종합하여 가장 중요한 핵심 내용을 간결하게 요약한다.
2.  **참여 대상 통합:** '참여대상'과 '모집안내'에 언급된 대상을 통합하여 최종 '참여 대상'을 명확하게 정리한다. 예를 들어 '참여대상: 1학년'과 '모집안내: 자유전공학부 학생'이라면, 최종적으로 '자유전공학부 1학년'으로 합쳐준다.
3.  **중요도 평가 (5점 척도):** 아래 기준에 따라 중요도를 ⭐ 1개에서 5개까지로 평가한다.
    - ⭐⭐⭐⭐⭐ (강력 추천): 대다수 학생에게 유용하며, 마일리지가 높거나 혜택이 매우 좋은 프로그램.
    - ⭐⭐⭐⭐ (추천): 특정 단과대/학과 학생들에게 매우 유용한 핵심 전공 관련 프로그램.
    - ⭐⭐⭐ (확인 권장): 참여하면 좋은 일반적인 교양, 특강, 학습법 관련 프로그램.
    - ⭐⭐ (관심 시 확인): 소수 대상이거나 특정 관심 분야에만 해당되는 프로그램.
    - ⭐ (참고): 단순 안내 또는 홍보성 프로그램.
4.  **기간 포맷 정리:** '모집기간'과 '운영기간'의 날짜와 시간을 "YYYY.MM.DD HH:MM" 형식으로 통일하고, 시작일과 종료일이 같으면 날짜는 한 번만 표시한다. (예: "2025.09.12 16:30 ~ 20:30")

### 비교과 프로그램 정보
- 원본 제목: {original_title}
{input_text}

### 출력 형식 (Key-Value JSON 형식)
{{
    "refined_title": "AI가 정제한 새로운 프로그램 제목",
    "summary_body": "<b>⭐⭐⭐(여기 별 개수를 수정) 한 줄 요약</b>\\n\\n<b>📋 핵심 정보</b>\\n- <b>모집기간:</b> (정리된 형식)\\n- <b>운영기간:</b> (정리된 형식)\\n- <b>참여 대상:</b> (통합된 대상)\\n- <b>주요 내용:</b> (핵심 내용 요약)\\n- <b>신청 방법:</b> ...",
    "tags": "#비교과 #프로그램 #마일리지"
}}
"""
    try:
        response = await aclient.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "위 규칙에 따라 비교과 프로그램 정보를 요약해주세요."}
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=1000
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"❌ OpenAI API 프로그램 요약 오류: {e}", exc_info=True)
        return {
            "refined_title": original_title,
            "summary_body": "AI 요약 중 오류가 발생했습니다.",
            "tags": ""
        }


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

async def extract_content(url: str, original_title: str, user_id: str = None) -> dict:
    """
    웹페이지 본문을 추출하고, 요약하여 정제된 제목, 요약 본문, 이미지 목록을 포함한 딕셔너리를 반환합니다.
    (user_id를 summarize_text로 전달)
    """
    try:
        html_content = await fetch_url(url)
        if not html_content:
            return {"refined_title": original_title, "summary_body": "페이지 내용을 불러올 수 없습니다.", "images": []}

        soup = BeautifulSoup(html_content, "html.parser")
        container = soup.find("div", class_="bdvTxt_wrap") or soup
        
        raw_text = " ".join(container.get_text(separator=" ", strip=True).split())
        images = [urllib.parse.urljoin(url, img["src"]) for img in container.find_all("img") if img.get("src")]

        text_to_summarize = raw_text
        if (not raw_text or len(raw_text) < 100) and images:
            logging.info(f"텍스트가 부족하여 이미지 OCR을 시도합니다: {url}")
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
                tasks = [ocr_image_from_url(session, img_url) for img_url in images]
                ocr_texts = await asyncio.gather(*tasks)
            
            full_ocr_text = "\n".join(filter(None, ocr_texts))
            if full_ocr_text.strip():
                text_to_summarize = full_ocr_text
            else:
                return {"refined_title": original_title, "summary_body": "이미지가 있으나 텍스트를 추출할 수 없었습니다.", "images": images}

        # user_id를 전달하도록 수정
        summary_dict = await summarize_text(text_to_summarize, original_title, user_id=user_id)
        summary_dict["images"] = images
        return summary_dict

    except Exception as e:
        logging.error(f"❌ 본문 내용 추출 오류 {url}: {e}", exc_info=True)
        return {"refined_title": original_title, "summary_body": "내용 처리 중 오류가 발생했습니다.", "images": []}
        
# ▼ 추가: PKNU AI 비교과 파싱 함수
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

def parse_pknuai_program_details(soup: BeautifulSoup) -> dict:
    """PKNU AI 시스템의 상세 페이지 HTML을 파싱하여 주요 정보 반환 (기간 포맷팅 강화)"""
    details = {}

    # ✨ [NEW] 기간 문자열을 정제하는 헬퍼 함수
    def format_period_string(raw_text: str) -> str:
        # nbsp; 같은 공백 문자를 일반 공백으로 바꾸고, 여러 공백을 하나로 합칩니다.
        clean_text = re.sub(r'\s+', ' ', raw_text.replace('\xa0', ' ')).strip()
        # " ~ " 양 옆의 공백을 통일합니다.
        return re.sub(r'\s*~\s*', ' ~ ', clean_text)

    pro_desc_box = soup.select_one(".pro_desc_box")
    if pro_desc_box:
        # ✨ [수정] 헬퍼 함수를 적용하여 기간 데이터를 가공합니다.
        raw_recruit_period = pro_desc_box.find("span", string=re.compile(r"모집기간:")).find_next_sibling("span").get_text(strip=True, separator=" ")
        details["모집기간"] = format_period_string(raw_recruit_period)
        
        raw_operating_period = pro_desc_box.find("span", string=re.compile(r"운영기간:")).find_next_sibling("span").get_text(strip=True, separator=" ")
        details["운영기간"] = format_period_string(raw_operating_period)

        details["운영방식"] = pro_desc_box.find("span", string=re.compile(r"운영방식:")).find_next_sibling("span").get_text(strip=True)
        details["장소"] = pro_desc_box.find("span", string=re.compile(r"장소:")).find_next_sibling("span").get_text(strip=True)
        details["참여대상"] = pro_desc_box.find("span", string=re.compile(r"참여대상:")).find_next_sibling("span").get_text(strip=True)
        details["예상 마일리지"] = pro_desc_box.find("span", string=re.compile(r"예상 마일리지:")).find_next_sibling("span").get_text(strip=True).replace("점","").strip() + "점"

    # 모집인원 숫자 추출
    app_gauge = soup.select_one(".app_gauge")
    if app_gauge:
        get_num = lambda text: int(re.search(r'\d+', text).group()) if re.search(r'\d+', text) else 0
        total_member_text = app_gauge.select_one(".total_member").get_text(strip=True) if app_gauge.select_one(".total_member") else "0"
        volun_text = app_gauge.select_one(".volun").get_text(strip=True) if app_gauge.select_one(".volun") else "0"
        details["모집인원"] = get_num(total_member_text)
        details["지원인원"] = get_num(volun_text)

    # 내용, 신청안내 등 pre 태그 정보 추출
    for header in soup.select("h4.pi_header"):
        header_text = header.get_text(strip=True)
        content_box = header.find_next_sibling("div", class_="pi_box")
        if content_box and content_box.select_one("pre"):
            details[header_text] = content_box.select_one("pre").get_text(strip=True)

    return details
    
async def get_pknuai_programs() -> list:
    """PKNU AI 비교과 프로그램 목록을 가져옵니다"""
    program_list_url = f"{PKNUAI_BASE_URL}/web/nonSbjt/program.do?mId=216&order=3"
    html_content = await fetch_program_html(program_list_url)
    if not html_content:
        return []
    soup = BeautifulSoup(html_content, 'html.parser')
    return _parse_pknuai_page(soup)

################################################################################
#                                알림 전송 및 확인 함수                            #
################################################################################
# script.py에서 send_notification 함수를 찾아 아래 코드로 교체하세요.

async def send_notification(notice: tuple, target_chat_id: str):
    """
    AI가 요약하고 정제한 정보를 바탕으로 공지사항 알림을 전송하는 함수. (구분선 추가)
    (target_chat_id를 user_id로 활용하여 extract_content에 전달)
    """
    original_title, href, department, date_ = notice
    
    # target_chat_id를 user_id로 전달
    summary_data = await extract_content(href, original_title, user_id=target_chat_id)
    
    refined_title = summary_data.get("refined_title", original_title)
    summary_body = summary_data.get("summary_body", "요약 정보를 불러올 수 없습니다.")
    images = summary_data.get("images", [])

    separator = "─" * 23

    message_text = (
        f"<b>{html.escape(refined_title)}</b>\n"
        f"{separator}\n\n"
        f"{summary_body}\n\n"
        f"<i>- {html.escape(department)} / {html.escape(date_)}</i>"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔗 공지 확인하기", url=href)]]
    )

    if images:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(images[0]) as resp:
                    if resp.status == 200:
                        image_bytes = await resp.read()
                        photo_file = BufferedInputFile(image_bytes, filename="photo.jpg")
                        
                        await bot.send_photo(
                            chat_id=target_chat_id,
                            photo=photo_file,
                            caption=message_text,
                            reply_markup=keyboard,
                            parse_mode="HTML"
                        )
                        return
        except Exception as e:
            logging.error(f"이미지와 함께 메시지 전송 실패 (텍스트만 전송으로 대체): {e}", exc_info=True)
            message_text += "\n\n<i>(공지 이미지를 불러오는 데 실패했습니다.)</i>"

    await bot.send_message(
        chat_id=target_chat_id,
        text=message_text,
        reply_markup=keyboard,
        parse_mode="HTML",
        disable_web_page_preview=True
    )

async def summarize_program_details(details: dict, original_title: str) -> dict:
    """
    파싱된 비교과 프로그램 상세 정보를 받아 AI로 재가공 및 요약하는 함수 (규칙 기반 강화).
    """
    # AI에게 전달할 정보를 문자열로 변환
    input_text = "\n".join([f"- {key}: {value}" for key, value in details.items()])

    prompt = f"""
당신은 부경대학교 학생들을 위한 똑똑한 AI 조교입니다.
아래 '작업 규칙'에 따라 '비교과 프로그램 정보'를 분석하고, 지정된 '출력 형식'으로만 요약해주세요.

### 작업 규칙 (매우 중요)

1.  **제목 정제:** '원본 제목'에서 불필요한 수식어를 제거하고 간결한 핵심 제목으로 만든다.

2.  **정보 통합 및 요약:**
    - '내용', '모집안내', '신청안내' 등 여러 항목에 흩어진 정보를 종합하여 '주요 내용'을 간결하게 요약한다.
    - '참여대상'과 '모집안내'에 언급된 대상을 통합하여 최종 '참여 대상'을 명확하게 정리한다.

3.  **중요도 평가 (5점 척도):**
    - ⭐⭐⭐⭐⭐ (강력 추천): 대다수 학생에게 유용하며, 마일리지가 높거나 혜택이 매우 좋은 프로그램.
    - ⭐⭐⭐⭐ (추천): 특정 단과대/학과 학생들에게 매우 유용한 핵심 전공 관련 프로그램.
    - ⭐⭐⭐ (확인 권장): 참여하면 좋은 일반적인 교양, 특강, 학습법 관련 프로그램.
    - ⭐⭐ (관심 시 확인): 소수 대상이거나 특정 관심 분야에만 해당되는 프로그램.
    - ⭐ (참고): 단순 안내 또는 홍보성 프로그램.

4.  **추천 액션 생성 (아래 조건에 따라 1~2개 생성):**
    - **(긴급성)** '모집기간' 마감까지 3일 이내라면: "마감이 임박했어요! 놓치기 아까운 기회이니 지금 바로 신청하세요."
    - **(경쟁성)** '선발방식'이 '선착순'이고 모집률이 70% 이상이라면: "선착순 마감이니 서두르는 걸 추천해요."
    - **(접근성)** '참여대상'이 '전체' 또는 '1학년' 대상이고 신청이 간편해 보이면: "신청 절차가 간단해 보여요. 5분만 투자해서 경험과 마일리지를 얻어보세요."
    - **(진로 연관성)** 내용이 '취업', '자격증', '상담' 등과 관련 있다면: "진로나 취업을 준비하고 있다면 좋은 스펙이 될 거예요."

5.  **관련 태그 선택 (아래 '태그 목록'에서 가장 적합한 2~5개만 선택):**
    - **`단과대학`이나 `주요 학과` 태그는 프로그램이 명시적으로 해당 집단을 대상으로 할 때만 포함한다.**
    - 절대 목록에 없는 태그를 만들지 않는다.

### 태그 목록

-   **분야:** `#특강` `#워크숍` `#공모전` `#경진대회` `#상담` `#컨설팅` `#현장실습` `#인턴십` `#봉사` `#자격증`
-   **혜택:** `#마일리지` `#장학금` `#인증서` `#기념품`
-   **대상:** `#전체학생` `#새내기` `#졸업예정자` `#외국인유학생`
-   **단과대학:** `#공과대학` `#정보융합대학` `#인문사회과학대학` `#자연과학대학` `#경영대학` `#수산과학대학`
-   **주요 학과:** `#기계공학과` `#컴퓨터공학과` `#IT융합응용공학과` `#데이터정보과학부`

### 비교과 프로그램 정보
- 원본 제목: {original_title}
{input_text}

### 출력 형식 (Key-Value JSON 형식)
{{
    "refined_title": "AI가 정제한 새로운 프로그램 제목",
    "summary_body": "<b>⭐⭐⭐(여기 별 개수를 수정) 한 줄 요약</b>\\n\\n<b>📋 핵심 정보</b>\\n- <b>모집기간:</b> ...\\n- <b>운영기간:</b> ...\\n- <b>참여 대상:</b> (통합된 대상)\\n- <b>주요 내용:</b> (핵심 내용 요약)\\n- <b>신청 방법:</b> ...\\n\\n<b>🚀 추천 액션</b>\\n- (규칙에 따라 생성된 추천 액션)\\n\\n<b>#️⃣ 관련 태그</b>\\n- (태그 목록에서 선택된 태그)"
}}
"""
    try:
        response = await aclient.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": "위 규칙에 따라 비교과 프로그램 정보를 요약해주세요."}
            ],
            response_format={"type": "json_object"},
            temperature=0.0, # 규칙 기반이므로 창의성을 최소화
            max_tokens=1000
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        logging.error(f"❌ OpenAI API 프로그램 요약 오류: {e}", exc_info=True)
        return {
            "refined_title": original_title,
            "summary_body": "AI 요약 중 오류가 발생했습니다.",
        }
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

async def check_for_new_pknuai_programs(target_chat_id: str):
    """새로운 PKNU AI 비교과 프로그램을 확인하고 알림을 보냅니다. (오류 수정)"""
    logging.info("새로운 AI 비교과 프로그램을 확인합니다...")
    seen = load_pknuai_program_cache()
    
    # ✨ [수정] get_pknuai_programs()를 호출하여 프로그램 목록을 가져옵니다.
    current_programs_list = await get_pknuai_programs() 
    found = False

    for program_summary in current_programs_list:
        # unique_id를 사용하도록 키 생성 방식을 통일합니다.
        key = generate_cache_key(program_summary['title'], program_summary['unique_id'])
        if key not in seen:
            logging.info(f"새 비교과 프로그램 발견: {program_summary['title']}")
            
            detail_html = await fetch_program_html(program_summary['href'])
            if not detail_html:
                continue

            # ✨ [수정] AI 요약 대신 직접 파싱 함수를 사용합니다.
            detail_soup = BeautifulSoup(detail_html, 'html.parser')
            program_details = parse_pknuai_program_details(detail_soup)

            await send_pknuai_program_notification(program_summary, program_details, target_chat_id)

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
    if str(message.chat.id) not in ALLOWED_USERS:
        await message.answer("이 봇은 등록된 사용자만 이용할 수 있습니다.\n등록하려면 `/register [등록코드]`를 입력해 주세요.")
        return
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 공지사항", callback_data="notice_menu"),
                InlineKeyboardButton(text="🎓 비교과 프로그램", callback_data="compare_programs")
            ],
            [
                InlineKeyboardButton(text="⚙️ 개인화 설정", callback_data="personalization_menu")
            ]
        ]
    )
    await message.answer("안녕하세요! 부경대학교 알림 봇입니다.\n어떤 정보를 확인하시겠어요?", reply_markup=keyboard)

@dp.message(Command("register"))
async def register_command(message: types.Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("등록 코드를 함께 입력해주세요. 예: `/register 1234`")
        return
    code, user_id_str = parts[1].strip(), str(message.chat.id)
    if code == REGISTRATION_CODE:
        if user_id_str in ALLOWED_USERS:
            await message.answer("이미 등록된 사용자입니다.")
        else:
            # 새로운 개인화 설정 기본값을 포함하여 사용자 데이터 생성
            ALLOWED_USERS[user_id_str] = {
                "filters": {f: False for f in PROGRAM_FILTERS},
                "personalization": get_default_personalization() # 기본 설정 함수 호출
            }
            save_whitelist(ALLOWED_USERS)
            push_file_changes(WHITELIST_FILE, f"New user registration: {user_id_str}")
            await message.answer("✅ 등록이 완료되었습니다! 이제 모든 기능을 사용할 수 있습니다.")
            logging.info(f"새 사용자 등록: {user_id_str}")
    else:
        await message.answer("❌ 등록 코드가 올바르지 않습니다.")
    
    await callback.answer(f"개인화 요약이 {'ON' if new_status else 'OFF'} 되었습니다.")
    
    # 변경된 상태를 반영하여 메뉴를 다시 표시
    button_text = f"✅ 개인화 요약 ON" if new_status else f"⬜️ 개인화 요약 OFF"
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=button_text, callback_data="toggle_personalization")],
        [InlineKeyboardButton(text="⬅️ 뒤로가기", callback_data="back_to_start")]
    ])
    await callback.message.edit_reply_markup(reply_markup=keyboard)

# 시작 메뉴로 돌아가는 콜백 핸들러 추가
@dp.callback_query(lambda c: c.data == "back_to_start")
async def back_to_start_handler(callback: CallbackQuery):
    await callback.answer()
    # /start 명령어의 메시지와 키보드를 재사용
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 공지사항", callback_data="notice_menu"),
                InlineKeyboardButton(text="🎓 비교과 프로그램", callback_data="compare_programs")
            ],
            [
                InlineKeyboardButton(text="⚙️ 개인화 설정", callback_data="personalization_menu")
            ]
        ]
    )
    await callback.message.edit_text("안녕하세요! 부경대학교 알림 봇입니다.\n어떤 정보를 확인하시겠어요?", reply_markup=keyboard)

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

# ▼▼▼ [REPLACE] 기존 PERSONALIZATION 관련 코드를 모두 지우고 아래 내용으로 교체 ▼▼▼

# 개인화 설정 옵션 정의
PERSONALIZATION_OPTIONS = {
    "학년": {
        "type": "single",
        "options": ["1학년", "2학년", "3학년", "4학년", "전체학년"]
    },
    "전공학과": {
        "type": "hierarchical", # 계층형 선택 타입
        "options": {
            "공과대학": [
                "기계공학부", "전기공학부", "에너지수송시스템공학부", "화학공학과", "공업화학과",
                "고분자공학과", "융합소재공학부", "시스템경영·안전공학부", "건축공학과", "지속가능공학부",
                "미래융합공학부"
            ],
            "정보융합대학": [
                "데이터정보과학부", "미디어커뮤니케이션학부", "스마트헬스케어학부", "전자정보통신공학부",
                "컴퓨터·인공지능공학부", "조형학부", "디지털금융학과", "스마트모빌리티공학과"
            ],
            "인문사회과학대학": [
                "국어국문학과", "영어영문학부", "일어일문학부", "사학과", "경제학과", "법학과",
                "행정복지학부", "국제지역학부", "중국학과", "정치외교학과", "유아교육과", "패션디자인학과"
            ],
            "자연과학대학": ["응용수학과", "물리학과", "화학과", "미생물학과", "간호학과", "과학컴퓨팅학과"],
            "경영대학": ["경영학부", "국제통상학부"],
            "수산과학대학": [
                "수산생명과학부", "식품과학부", "해양생산시스템관리학부", "해양수산경영경제학부", "수해양산업교육과",
                "수산생명의학과"
            ],
            "환경·해양대학": ["지구환경시스템과학부", "해양공학과", "에너지자원공학과"],
            "기타": ["전체학과"]
        }
    },
    "관심분야": {
        "type": "multi", # 여러 개 선택 가능
        "options": [
            "취업", "채용", "인턴", "현장실습", "장학금", "등록금", "공모전", "경진대회",
            "대외활동", "특강", "워크숍", "교내활동", "학사일정", "수강신청", "졸업",
            "창업", "상담", "봉사", "자격증", "대학원"
        ]
    }
}

def get_default_personalization():
    """개인화 설정 기본값을 생성하는 함수"""
    settings = {"enabled": False}
    for category, value in PERSONALIZATION_OPTIONS.items():
        if value["type"] == "single" or value["type"] == "hierarchical":
            settings[category] = "전체학과" if category == "전공학과" else value["options"][-1]
        else:
            settings[category] = []
    return settings

@dp.callback_query(lambda c: c.data == "personalization_menu")
@dp.callback_query(lambda c: c.data == "personalization_menu")
async def personalization_menu_handler(callback: CallbackQuery, state: FSMContext):
    """개인화 설정 메인 메뉴를 표시하는 핸들러 (요약 기능 강화)"""
    await callback.answer()
    await state.clear()
    user_id_str = str(callback.message.chat.id)

    # --- 기존 코드와 동일 ---
    if "personalization" not in ALLOWED_USERS.get(user_id_str, {}):
        if user_id_str not in ALLOWED_USERS: ALLOWED_USERS[user_id_str] = {}
        ALLOWED_USERS[user_id_str]["personalization"] = get_default_personalization()
        save_whitelist(ALLOWED_USERS)

    user_settings = ALLOWED_USERS[user_id_str]["personalization"]
    is_enabled = user_settings.get("enabled", False)

    # ▼▼▼ [수정] 현재 설정 값을 요약하는 텍스트 생성 ▼▼▼
    status_lines = []
    for cat in PERSONALIZATION_OPTIONS.keys():
        value = user_settings.get(cat, '미설정')
        if isinstance(value, list) and not value:
            value_str = '없음'
        elif isinstance(value, list):
            value_str = ", ".join(value)
        else:
            value_str = str(value)
        status_lines.append(f"  - {cat}: {value_str}")
        
    status_text = "\n".join(status_lines)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{'✅ 개인화 요약 ON' if is_enabled else '⬜️ 개인화 요약 OFF'}", callback_data="p13n_toggle_enabled")],
        [InlineKeyboardButton(text="⚙️ 학년 설정", callback_data="p13n_cat_학년"),
         InlineKeyboardButton(text="⚙️ 전공학과 설정", callback_data="p13n_cat_전공학과")],
        [InlineKeyboardButton(text="⚙️ 관심분야 설정", callback_data="p13n_cat_관심분야")],
        [InlineKeyboardButton(text="⬅️ 뒤로가기", callback_data="back_to_start")]
    ])

    await callback.message.edit_text(
        "<b>개인화 요약 설정</b>\n\n"
        "이 기능을 켜면, 아래 설정된 프로필을 바탕으로 공지사항의 중요도와 평가 근거가 맞춤형으로 제공됩니다.\n\n"
        f"<b>현재 프로필:</b>\n{status_text}",
        reply_markup=keyboard
    )

@dp.callback_query(lambda c: c.data == "p13n_toggle_enabled")
async def toggle_personalization_enabled_handler(callback: CallbackQuery, state: FSMContext):
    user_id_str = str(callback.message.chat.id)
    settings = ALLOWED_USERS[user_id_str].setdefault("personalization", get_default_personalization())
    settings["enabled"] = not settings.get("enabled", False)
    save_whitelist(ALLOWED_USERS)
    push_file_changes(WHITELIST_FILE, f"User {user_id_str} toggled personalization")
    await callback.answer(f"개인화 요약이 {'ON' if settings['enabled'] else 'OFF'} 되었습니다.")
    await personalization_menu_handler(callback, state)

@dp.callback_query(lambda c: c.data.startswith("p13n_cat_"))
async def personalization_category_handler(callback: CallbackQuery, state: FSMContext):
    category = callback.data.replace("p13n_cat_", "")
    user_id_str = str(callback.message.chat.id)
    settings = ALLOWED_USERS[user_id_str]["personalization"]
    cat_info = PERSONALIZATION_OPTIONS[category]

    if cat_info["type"] == "hierarchical":
        await state.set_state(PersonalizationState.selecting_college)
        colleges = list(cat_info["options"].keys())
        buttons = [InlineKeyboardButton(text=college, callback_data=f"p13n_college_{college}") for college in colleges]
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            buttons[i:i+2] for i in range(0, len(buttons), 2)
        ] + [[InlineKeyboardButton(text="⬅️ 이전 메뉴로", callback_data="personalization_menu")]])
        await callback.message.edit_text(f"소속 <b>단과대학</b>을 선택하세요:", reply_markup=keyboard)
        return

    buttons = []
    for option in cat_info["options"]:
        is_selected = (settings.get(category) == option) if cat_info["type"] == "single" else (option in settings.get(category, []))
        text = f"{'✅' if is_selected else '⬜️'} {option}"
        buttons.append(InlineKeyboardButton(text=text, callback_data=f"p13n_set_{category}_{option}"))

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        buttons[i:i+2] for i in range(0, len(buttons), 2)
    ] + [[InlineKeyboardButton(text="⬅️ 이전 메뉴로", callback_data="personalization_menu")]])
    await callback.message.edit_text(f"<b>{category}</b> 설정:", reply_markup=keyboard)

@dp.callback_query(PersonalizationState.selecting_college, lambda c: c.data.startswith("p13n_college_"))
async def department_selection_handler(callback: CallbackQuery, state: FSMContext):
    college = callback.data.replace("p13n_college_", "")
    user_id_str = str(callback.message.chat.id)

    if college == "기타":
        ALLOWED_USERS[user_id_str]["personalization"]["전공학과"] = "전체학과"
        save_whitelist(ALLOWED_USERS)
        await state.clear()
        await callback.answer("'전체학과'로 설정되었습니다.")
        await personalization_menu_handler(callback, state)
        return

    await state.set_state(PersonalizationState.selecting_department)
    departments = PERSONALIZATION_OPTIONS["전공학과"]["options"][college]
    buttons = [InlineKeyboardButton(text=dept, callback_data=f"p13n_dept_{dept}") for dept in departments]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [b] for b in buttons
    ] + [[InlineKeyboardButton(text="⬅️ 단과대학 다시 선택", callback_data="p13n_cat_전공학과")]])
    await callback.message.edit_text(f"<b>{college}</b>의 세부 전공/학부/학과를 선택하세요:", reply_markup=keyboard)

@dp.callback_query(PersonalizationState.selecting_department, lambda c: c.data.startswith("p13n_dept_"))
async def set_department_handler(callback: CallbackQuery, state: FSMContext):
    department = callback.data.replace("p13n_dept_", "")
    user_id_str = str(callback.message.chat.id)
    ALLOWED_USERS[user_id_str]["personalization"]["전공학과"] = department
    save_whitelist(ALLOWED_USERS)
    await state.clear()
    await callback.answer(f"'{department}'으로 설정되었습니다.")
    await personalization_menu_handler(callback, state)

@dp.callback_query(lambda c: c.data.startswith("p13n_set_"))
async def set_personalization_option_handler(callback: CallbackQuery, state: FSMContext):
    _, category, option = callback.data.split("_", 3)
    user_id_str = str(callback.message.chat.id)
    settings = ALLOWED_USERS[user_id_str]["personalization"]
    cat_info = PERSONALIZATION_OPTIONS[category]

    if cat_info["type"] == "single":
        settings[category] = option
        save_whitelist(ALLOWED_USERS)
        await callback.answer(f"{category}가 '{option}'으로 설정되었습니다.")
        await personalization_menu_handler(callback, state)
    else: # multi
        current_options = settings.setdefault(category, [])
        if option in current_options:
            current_options.remove(option)
            await callback.answer(f"'{option}' 선택 해제")
        else:
            current_options.append(option)
            await callback.answer(f"'{option}' 선택")
        save_whitelist(ALLOWED_USERS)
        await personalization_category_handler(callback, state)

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

    status_msg = await callback.message.edit_text("📊 필터로 검색 중...")
    
    list_url = "https://pknuai.pknu.ac.kr/web/nonSbjt/program.do?mId=216&order=3"
    html_content = await fetch_program_html(list_url, filters=user_filters)
    
    await status_msg.delete()

    programs = []
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        programs = _parse_pknuai_page(soup)

    if not programs:
        await callback.message.answer("조건에 맞는 프로그램이 없습니다.")
    else:
        for program in programs:
            detail_html = await fetch_program_html(program['href'])
            if detail_html:
                # ✨ [수정] AI 요약 대신 직접 파싱 함수를 사용합니다.
                detail_soup = BeautifulSoup(detail_html, 'html.parser')
                program_details = parse_pknuai_program_details(detail_soup)
                await send_pknuai_program_notification(program, program_details, callback.message.chat.id)
            
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

    status_msg = await message.answer(f"🔍 '{keyword}' 키워드로 검색 중입니다...")
    
    # 키워드 검색 시에는 URL을 직접 만들지 않고 fetch_program_html에 인자로 전달합니다.
    list_url = "https://pknuai.pknu.ac.kr/web/nonSbjt/program.do?mId=216&order=3"
    html_content = await fetch_program_html(list_url, keyword=keyword)

    await status_msg.delete()

    programs = []
    if html_content:
        soup = BeautifulSoup(html_content, 'html.parser')
        programs = _parse_pknuai_page(soup)

    if not programs:
        await message.answer(f"❌ '{keyword}' 키워드에 해당하는 프로그램이 없습니다.")
    else:
        for program in programs:
            detail_html = await fetch_program_html(program['href'])
            if detail_html:
                # ✨ [수정] AI 요약 대신 직접 파싱 함수를 사용합니다.
                detail_soup = BeautifulSoup(detail_html, 'html.parser')
                program_details = parse_pknuai_program_details(detail_soup)
                await send_pknuai_program_notification(program, program_details, message.chat.id)
                
class KeywordSearchState(StatesGroup):
    waiting_for_keyword = State()

class PersonalizationState(StatesGroup):
    selecting_college = State() # 단과대학 선택 중
    selecting_department = State() # 세부학과 선택 중

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

