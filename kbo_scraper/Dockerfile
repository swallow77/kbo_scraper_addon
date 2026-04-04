FROM python:3.11-slim

# ✅ chromium + chromedriver 설치
# python:3.11-slim (Debian Bookworm 기준)
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=Asia/Seoul

# ✅ chromedriver 경로 확인용 (빌드 시 로그 출력)
RUN which chromedriver && chromedriver --version || true
RUN which chromium && chromium --version || true

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod a+x run.sh

CMD ["./run.sh"]
