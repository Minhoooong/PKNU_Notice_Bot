# 1. 깨끗한 Ubuntu 22.04에서 시작합니다.
FROM ubuntu:22.04

# 2. 시스템 업데이트 및 모든 필수 프로그램을 직접 설치합니다.
#    - DEBIAN_FRONTEND=noninteractive: 설치 중 질문이 뜨지 않도록 합니다.
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    git \
    git-crypt \
    && rm -rf /var/lib/apt/lists/*

# 3. 작업 디렉터리를 설정합니다.
WORKDIR /app

# 4. 파이썬 라이브러리 목록을 복사하고 설치합니다.
COPY requirements.txt ./
RUN pip3 install --no-cache-dir -r requirements.txt

# 5. 설치된 라이브러리에 맞는 브라우저를 다운로드하고 설치합니다. (가장 중요!)
RUN python3 -m playwright install --with-deps chromium

# 6. 나머지 모든 프로젝트 코드를 복사합니다.
COPY . .

# 7. 컨테이너가 시작될 때, 자동화 스크립트를 직접 실행합니다.
CMD ["python3", "-m", "app.run_auto_agent"]