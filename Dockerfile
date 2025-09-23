# Microsoft의 공식 Playwright 이미지를 베이스로 사용합니다.
# 이 이미지에는 Python, Playwright, 브라우저 및 모든 종속성이 이미 설치되어 있습니다.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

# 작업 디렉터리 설정
WORKDIR /app

# 프로젝트에 필요한 추가 시스템 패키지만 설치합니다 (git, git-crypt).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-crypt \
    curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 의존성 파일(requirements.txt) 복사 및 설치
COPY requirements.txt /app/
# --no-cache-dir 옵션은 이미지 용량을 줄이는 데 도움이 됩니다.
RUN pip install --no-cache-dir -r requirements.txt

# 전체 프로젝트 코드 복사
COPY . /app

# 컨테이너 시작 시 봇 스크립트를 실행합니다.
CMD ["python3", "script.py"]
