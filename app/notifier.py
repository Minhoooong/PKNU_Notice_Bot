from aiogram import Bot
from aiogram.client.bot import DefaultBotProperties
from .core.config import settings

class Notifier:
    def __init__(self):
        self.bot = Bot(token=settings.TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))

    def _format_message(self, program: dict) -> str:
        """공통 메시지 포맷을 생성합니다."""
        title = program.get('title', '제목 없음')
        status = program.get('status', '상태 정보 없음')
        period = program.get('period', '기간 정보 없음')
        url = program.get('url', '#')
        
        # 불필요한 공백을 제거하여 메시지를 깔끔하게 만듭니다.
        status = ' '.join(status.split())
        period = ' '.join(period.split())

        return (
            f"<b>{title}</b>\n"
            f"상태: {status}\n"
            f"기간: {period}\n"
            f"🔗 <a href='{url}'>자세히 보기</a>"
        )

    def format_auto_message(self, program: dict) -> str:
        """자동 에이전트가 보낼 메시지 포맷입니다."""
        header = "<b>[📢 새로운 비교과]</b>\n"
        return header + self._format_message(program)

    def format_search_message(self, program: dict) -> str:
        """검색 결과로 보낼 메시지 포맷입니다."""
        return self._format_message(program)

    async def send(self, chat_id: str, text: str):
        """특정 채팅 ID로 메시지를 전송합니다."""
        await self.bot.send_message(
            chat_id, 
            text, 
            disable_web_page_preview=True
        )

    async def aclose(self):
        """봇 세션을 종료합니다."""
        await self.bot.session.close()

