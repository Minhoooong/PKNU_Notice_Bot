FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

# 한글 폰트
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-nanum \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
CMD ["python","script.py"]
