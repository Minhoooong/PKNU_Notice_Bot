FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# 시스템 프로그램(git-crypt)과 한글 폰트를 설치합니다.
RUN apt-get update && apt-get install -y --no-install-recommends \
    git-crypt \
    fonts-nanum \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 파이썬 라이브러리를 설치합니다.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# 나머지 프로젝트 코드를 복사합니다.
COPY . .

# 스크립트를 실행합니다.
CMD ["python","script.py"]