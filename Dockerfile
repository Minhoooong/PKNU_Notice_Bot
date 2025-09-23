# Ubuntu 22.04를 베이스 이미지로 사용
FROM ubuntu:22.04

# 시스템 업데이트 및 필수 패키지 설치 (Playwright 종속성 포함)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    curl \
    git-crypt \
    # Playwright 브라우저 실행에 필요한 라이브러리 목록
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libdbus-glib-1-2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libatspi2.0-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉터리 설정
WORKDIR /app

# 의존성 파일 복사 및 설치
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt
# --with-deps 옵션을 제거하여 chromium 브라우저만 설치합니다.
RUN python3 playwright install chromium

# 전체 프로젝트 코드 복사
COPY . /app

# 컨테이너 시작 시 봇 스크립트를 실행합니다.
CMD ["python3", "script.py"]
