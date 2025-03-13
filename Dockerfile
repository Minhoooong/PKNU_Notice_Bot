# Ubuntu 22.04를 베이스 이미지로 사용
FROM ubuntu:22.04

# 시스템 업데이트 및 필수 패키지 설치
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    curl \
    git-crypt \
    && rm -rf /var/lib/apt/lists/*

# 작업 디렉터리 설정
WORKDIR /app

# 의존성 파일 복사 및 설치
COPY requirements.txt /app/
RUN pip3 install --no-cache-dir -r requirements.txt
RUN playwright install --with-deps chromium

# 전체 프로젝트 코드 복사
COPY . /app

# 컨테이너 시작 시 봇 스크립트를 실행합니다.
CMD ["python3", "script.py"]
