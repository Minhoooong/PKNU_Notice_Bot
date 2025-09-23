# ================= STAGE 1: 의존성 설치 =================
# 별명을 builder로 지정합니다.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy AS builder

WORKDIR /app

# 시스템 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-crypt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 파이썬 패키지를 시스템 영역이 아닌 별도 경로에 설치합니다.
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt


# ================= STAGE 2: 최종 이미지 생성 =================
# 더 가벼운 playwright 이미지를 기반으로 시작합니다.
FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

# 시스템 패키지 설치 (git-crypt는 최종 이미지에 필요할 수 있습니다)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    git-crypt \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# builder 스테이지에서 설치했던 파이썬 패키지들만 복사해옵니다.
COPY --from=builder /root/.local /root/.local

# PATH 환경 변수에 패키지 경로를 추가해줍니다.
ENV PATH=/root/.local/bin:$PATH

# 소스 코드 복사
COPY . .

# 컨테이너 시작 시 봇 스크립트를 실행합니다.
CMD ["python3", "script.py"]
