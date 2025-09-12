from abc import ABC, abstractmethod

class SiteAdapter(ABC):
    """
    모든 사이트 크롤링 어댑터가 상속받아야 하는 기본 클래스입니다.
    """
    def __init__(self, page, selectors):
        self.page = page
        self.sel = selectors

    @abstractmethod
    async def login(self, username: str, password: str):
        """사이트에 로그인하는 로직을 구현해야 합니다."""
        pass

