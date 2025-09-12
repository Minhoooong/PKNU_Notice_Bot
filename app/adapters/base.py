from abc import ABC, abstractmethod
from playwright.async_api import Page

class SiteAdapter(ABC):
    """
    모든 사이트 어댑터가 상속해야 하는 추상 기본 클래스입니다.
    """
    def __init__(self, page: Page, selectors: dict):
        self.page = page
        self.sel = selectors

    @abstractmethod
    async def iter_current(self):
        """현재 페이지만 크롤링하는 메서드"""
        raise NotImplementedError

    @abstractmethod
    async def iter_all_terms(self):
        """모든 학기를 크롤링하는 메서드"""
        raise NotImplementedError