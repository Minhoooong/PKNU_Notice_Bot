# Playwright 버전을 라이브러리와 동일한 v1.55.0으로 변경합니다.
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

# 시스템 프로그램(git-crypt)과 한글 폰트를 설치합니다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git-crypt \
    fonts-nanum \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 파이썬 라이브러리를 설치합니다.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# ▼▼▼ 라이브러리에 맞는 브라우저를 설치하는 핵심 단계입니다. ▼▼▼
RUN python -m playwright install --with-deps chromium

# 나머지 프로젝트 코드를 복사합니다.
COPY . .

# 스크립트를 실행합니다.
CMD ["python3", "-m", "app.run_auto_agent"]