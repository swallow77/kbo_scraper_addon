#!/bin/bash

echo "=== KBO Scraper Add-on 시작 ==="
echo "Chromium 경로 확인:"
which chromium 2>/dev/null || echo "chromium 없음"
which chromium-browser 2>/dev/null || echo "chromium-browser 없음"
which chromedriver 2>/dev/null || echo "chromedriver 없음"

echo ""
echo "버전 정보:"
chromium --version 2>/dev/null || chromium-browser --version 2>/dev/null || echo "버전 확인 불가"
chromedriver --version 2>/dev/null || echo "chromedriver 버전 확인 불가"
echo ""

# 스크립트 실행 (비정상 종료 시 30초 후 재시작)
while true; do
    python3 /app/kbo_scraper.py
    echo "⚠️ 스크립트 종료됨. 30초 후 재시작..."
    sleep 30
done
