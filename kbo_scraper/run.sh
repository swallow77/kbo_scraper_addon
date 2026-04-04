#!/bin/bash
set -e

echo "============================================"
echo " KBO Smart Scraper Add-on 시작"
echo "============================================"

# Chromium 및 ChromeDriver 확인
echo "[환경 확인] Chromium 경로: $(which chromium 2>/dev/null || echo '없음')"
echo "[환경 확인] Chromedriver 경로: $(which chromedriver 2>/dev/null || echo '없음')"
echo "[환경 확인] Python 버전: $(python3 --version)"

# 크래시 시 자동 재시작 루프
RESTART_DELAY=30
while true; do
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 스크립트 실행 시작..."
    python3 /app/kbo_scraper.py
    EXIT_CODE=$?
    if [ $EXIT_CODE -ne 0 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ⚠️  스크립트 종료 (코드: $EXIT_CODE). ${RESTART_DELAY}초 후 재시작..."
        sleep $RESTART_DELAY
    else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 스크립트 정상 종료."
        break
    fi
done
