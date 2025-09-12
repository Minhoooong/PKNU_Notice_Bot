# script.py (파일 내용은 main.py의 기능을 수행하도록 변경)
import asyncio
import hashlib
import html
import json
import logging
import os
import re
import subprocess
from datetime import datetime
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher, types
from aiogram.client.bot import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, InlineKeyboardButton,
                           InlineKeyboardMarkup)

# 현재 프로젝트의 모듈을 가져옵니다.
from app.run_announcement_agent import \
    scrape_announcements as fetch_school_notices
from app.run_announcement_agent import \
    send_telegram_message as send_notification
from app.run_auto_agent import get_programs

# --- 환경 변수 및 상수 설정 ---
TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
GROUP_CHAT_ID = os.environ.get('GROUP_CHAT_ID') # 자동 알림용 그룹 ID
REGISTRATION_CODE = os.environ.get('REGISTRATION_CODE')

WHITELIST_FILE = "whitelist.json"

# --- 로깅 설정 ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler(),
        RotatingFileHandler("bot.log", maxBytes=10**6, backupCount=3)
    ]
)

# --- AIogram 설정 ---
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()


# --- 상태 머신 정의 ---
class KeywordSearchState(StatesGroup):
    waiting_for_keyword = State()


# --- 화이트리스트 관리 ---
def load_whitelist() -> dict:
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def save_whitelist(data: dict):
    with open(WHITELIST_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

# --- Git 연동 (선택적) ---
def push_changes(file_path: str, commit_message: str):
    try:
        subprocess.run(["git", "config", "--global", "user.email", "bot@github.action"], check=True)
        subprocess.run(["git", "config", "--global", "user.name", "NoticeBot"], check=True)
        subprocess.run(["git", "add", file_path], check=True)
        # Check if there are changes to commit
        status_result = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
        if file_path in status_result.stdout:
            subprocess.run(["git", "commit", "-m", commit_message], check=True)
            subprocess.run(["git", "push"], check=True)
            logging.info(f"✅ {file_path} 파일이 저장소에 푸시되었습니다.")
        else:
            logging.info(f"ℹ️ {file_path} 파일에 변경 사항이 없어 커밋하지 않았습니다.")
    except subprocess.CalledProcessError as e:
        logging.error(f"❌ Git 푸시 오류: {e}")
    except Exception as e:
        logging.error(f"❌ 예상치 못한 Git 오류: {e}")


ALLOWED_USERS = load_whitelist()


# --- 명령어 핸들러 ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id_str = str(message.chat.id)
    if user_id_str not in ALLOWED_USERS:
        await message.answer("🔒 이 봇은 등록된 사용자만 이용할 수 있습니다.\n`/register [등록코드]`를 입력하여 등록해주세요.")
        return

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📢 학교 공지사항", callback_data="fetch_notices"),
            InlineKeyboardButton(text="🎓 비교과 프로그램", callback_data="search_programs")
        ]
    ])
    await message.answer("안녕하세요! 부경대학교 알림봇입니다. 무엇을 도와드릴까요?", reply_markup=keyboard)


@dp.message(Command("register"))
async def register_command(message: types.Message):
    args = message.text.split()
    if len(args) != 2:
        await message.answer("사용법: `/register [등록코드]`")
        return

    code = args[1]
    user_id_str = str(message.chat.id)

    if code == REGISTRATION_CODE:
        if user_id_str in ALLOWED_USERS:
            await message.answer("✅ 이미 등록된 사용자입니다.")
        else:
            ALLOWED_USERS[user_id_str] = {"username": message.from_user.full_name}
            save_whitelist(ALLOWED_USERS)
            # push_changes(WHITELIST_FILE, f"User registered: {message.from_user.full_name}")
            await message.answer("🎉 등록이 완료되었습니다! /start 명령어를 사용해 봇을 시작하세요.")
            logging.info(f"New user registered: {user_id_str} ({message.from_user.full_name})")
    else:
        await message.answer("❌ 등록 코드가 올바르지 않습니다.")


# --- 콜백 및 상태 처리 핸들러 ---
@dp.callback_query(lambda c: c.data == 'fetch_notices')
async def handle_fetch_notices(callback_query: CallbackQuery):
    await callback_query.answer("최신 공지사항을 가져오는 중...")
    notices = await fetch_school_notices()
    if not notices:
        await callback_query.message.answer("새로운 공지사항이 없습니다.")
        return

    for notice in notices[:5]:  # 최신 5개만 표시
        await send_notification(notice, target_chat_id=callback_query.from_user.id)


@dp.callback_query(lambda c: c.data == 'search_programs')
async def handle_search_programs(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    await callback_query.message.answer("🔎 검색할 비교과 프로그램의 키워드를 입력해주세요.")
    await state.set_state(KeywordSearchState.waiting_for_keyword)


@dp.message(KeywordSearchState.waiting_for_keyword)
async def process_keyword(message: types.Message, state: FSMContext):
    keyword = message.text
    await state.clear()
    await message.answer(f"⏳ '{keyword}'(으)로 비교과 프로그램을 검색합니다. 잠시만 기다려주세요...")

    programs = await get_programs(keyword=keyword)

    if not programs:
        await message.answer(f"😅 '{keyword}'에 대한 비교과 프로그램을 찾을 수 없습니다.")
        return

    await message.answer(f"✅ '{keyword}'에 대한 {len(programs)}개의 프로그램을 찾았습니다.")
    for program in programs:
        # get_programs가 반환하는 데이터 형식에 맞춰 메시지 전송 로직 필요
        # 예시: send_program_notification(program, message.chat.id)
        # 현재 run_auto_agent.py에는 send 함수가 없으므로 직접 구성
        msg = (f"🎓 <b>{html.escape(program.get('title',''))}</b>\n"
               f"상태: {html.escape(program.get('status',''))}\n"
               f"기간: {html.escape(program.get('period',''))}\n"
               f"🔗 <a href='{program.get('url')}'>자세히 보기</a>")
        await bot.send_message(message.chat.id, msg)


# --- 메인 실행 함수 ---
async def main():
    """10분 동안 봇을 실행하고 종료합니다."""
    logging.info("🚀 10분간 대화형 봇을 시작합니다...")
    try:
        # asyncio.gather를 사용하여 타임아웃과 폴링을 함께 실행
        await asyncio.wait_for(dp.start_polling(bot), timeout=600.0)
    except asyncio.TimeoutError:
        logging.info("⏳ 10분이 경과하여 봇을 정상적으로 종료합니다.")
    except Exception as e:
        logging.error(f"봇 실행 중 오류 발생: {e}", exc_info=True)
    finally:
        # 모든 태스크를 정리하고 세션을 닫습니다.
        await dp.storage.close()
        await bot.session.close()
        logging.info("✅ 봇이 종료되었습니다.")


if __name__ == '__main__':
    # 윈도우 환경에서 asyncio 정책 설정
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
