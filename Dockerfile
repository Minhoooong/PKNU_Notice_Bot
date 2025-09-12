FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# 한글 폰트
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-nanum \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# 현재 디렉토리의 모든 파일을 이미지로 복사합니다.
COPY . .
CMD ["python","script.py"]
