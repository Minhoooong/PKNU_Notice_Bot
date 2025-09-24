# playwright 공식 이미지를 기반으로 시작합니다.
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

# 작업 디렉토리를 설정합니다.
WORKDIR /app

# 시스템에 필요한 기본 패키지를 먼저 설치합니다.
# (이 레이어는 거의 바뀌지 않습니다)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-crypt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ▼▼▼ 핵심 변경 사항 ▼▼▼
# 1. 의존성 파일만 먼저 복사합니다.
COPY requirements.txt .

# 2. 파이썬 패키지를 설치합니다.
# requirements.txt 파일이 변경되지 않는 한, 이 레이어는 캐시되어 재사용됩니다.
RUN pip install --no-cache-dir -r requirements.txt

# 3. Playwright 브라우저 설치 (핵심 수정)
RUN playwright install

# 4. 모든 소스 코드를 마지막에 복사합니다.
# script.py 등 코드 파일이 변경되면 이 레이어부터 다시 빌드됩니다.
COPY . .

# 컨테이너 시작 시 봇 스크립트를 실행합니다.
CMD ["python3", "script.py"]
