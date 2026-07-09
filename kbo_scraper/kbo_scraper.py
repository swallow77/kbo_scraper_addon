import json, time, datetime, sys, io, traceback, os, re
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt

# 로그 즉시 출력
sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8', line_buffering=True)

URL = "https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx"
MAX_DRIVER_REUSE = 30

# ──────────────────────────────────────────────
# 로그 헬퍼
# ──────────────────────────────────────────────
def log(msg):
    print(msg, flush=True)

def log_error_once(error_key, count, message):
    """
    같은 오류가 반복될 때 로그 폭증 방지.
    1회, 5회, 10회, 이후 10회 단위로만 출력.
    """
    if count in (1, 5, 10) or count % 10 == 0:
        log(f"{message} (연속 {count}회)")

# ──────────────────────────────────────────────
# 설정 로드
# ──────────────────────────────────────────────
def load_config():
    try:
        with open('/data/options.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            log(f"✅ 설정 로드: 팀={cfg['target_team']}, MQTT={cfg['mqtt_broker']}")
            return cfg
    except Exception as e:
        log(f"⚠️  설정 로드 실패 ({e}), 기본값 사용")
        return {
            "target_team": "LG", "mqtt_broker": "192.168.0.40", "mqtt_port": 1883,
            "mqtt_username": "admin", "mqtt_password": "swallow77!",
            "season_start": "03-20", "season_end": "11-30",
            "interval_standby": 60, "interval_game": 1
        }

# ──────────────────────────────────────────────
# 팀 이름 영어 변환
# ──────────────────────────────────────────────
TEAM_ENG = {
    "LG": "lg", "KIA": "kia", "SSG": "ssg", "NC": "nc",
    "두산": "doosan", "KT": "kt", "롯데": "lotte",
    "한화": "hanwha", "삼성": "samsung", "키움": "kiwoom"
}

def get_eng_team(team):
    return TEAM_ENG.get(team, "unknown")

# ──────────────────────────────────────────────
# 시즌 체크
# ──────────────────────────────────────────────
def is_in_season(cfg):
    try:
        now = datetime.datetime.now()
        year = now.year
        start = datetime.datetime.strptime(f"{year}-{cfg['season_start']}", "%Y-%m-%d")
        end   = datetime.datetime.strptime(f"{year}-{cfg['season_end']}", "%Y-%m-%d")
        return start <= now <= end
    except Exception as e:
        log(f"⚠️  시즌 체크 오류: {e}")
        return True

# ──────────────────────────────────────────────
# 날짜/시간 파싱
# ──────────────────────────────────────────────
def parse_game_date(date_str):
    try:
        nums = re.findall(r'\d+', date_str)
        if len(nums) >= 2:
            month, day = int(nums[0]), int(nums[1])
            year = datetime.datetime.now().year
            return datetime.date(year, month, day)
    except Exception:
        pass
    return None

def parse_game_time(time_str):
    if not time_str or ":" not in time_str:
        return None
    try:
        parts = time_str.strip().split(':')
        return int(parts[0]), int(parts[1])
    except Exception:
        return None

# ──────────────────────────────────────────────
# 경기 종료 결과 상태 변환
# ──────────────────────────────────────────────
def get_finished_status(raw_status, my_score, opp_score):
    """
    선택 팀 기준으로 종료 경기를 경기승리/경기패배/경기무승부로 변환.
    점수가 없거나 종료 상태가 아니면 원본 상태를 그대로 사용.
    """
    if "종료" not in raw_status:
        return raw_status
    if not (str(my_score).isdigit() and str(opp_score).isdigit()):
        return raw_status

    my = int(my_score)
    opp = int(opp_score)
    if my > opp:
        return "경기승리"
    if my < opp:
        return "경기패배"
    return "경기무승부"

# ──────────────────────────────────────────────
# 다음 확인 시간 계산
# ──────────────────────────────────────────────
def tomorrow_13(now):
    target_dt = now.replace(hour=13, minute=0, second=0, microsecond=0)
    if now >= target_dt:
        target_dt += datetime.timedelta(days=1)
    return target_dt

def next_check_for_game_date(game_date, start_out, now):
    if game_date and game_date > now.date():
        gt = parse_game_time(start_out)
        if gt:
            game_dt = datetime.datetime.combine(game_date, datetime.time(gt[0], gt[1]))
            return max(now + datetime.timedelta(minutes=1), game_dt - datetime.timedelta(minutes=5))
        return datetime.datetime.combine(game_date, datetime.time(13, 0))
    return tomorrow_13(now)

# ──────────────────────────────────────────────
# 웹드라이버 초기화/정리
# ──────────────────────────────────────────────
def init_driver():
    options = webdriver.ChromeOptions()
    options.page_load_strategy = "eager"
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-background-networking")
    options.add_argument("--disable-default-apps")
    options.add_argument("--disable-sync")
    options.add_argument("--disable-translate")
    options.add_argument("--metrics-recording-only")
    options.add_argument("--mute-audio")
    options.add_argument("--no-first-run")
    options.add_argument("--safebrowsing-disable-auto-update")
    options.add_argument("--window-size=1280,800")
    options.add_argument("--memory-pressure-off")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    chromedriver_path = "/usr/bin/chromedriver"
    if not os.path.exists(chromedriver_path):
        for alt in ["/usr/lib/chromium/chromedriver", "/usr/local/bin/chromedriver"]:
            if os.path.exists(alt):
                chromedriver_path = alt
                break

    service = Service(executable_path=chromedriver_path)
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.set_page_load_timeout(30)
    return driver

def close_driver(driver):
    if driver:
        try:
            driver.quit()
        except Exception:
            pass
    return None

# ──────────────────────────────────────────────
# MQTT 클라이언트 초기화
# ──────────────────────────────────────────────
def init_mqtt(cfg):
    client = mqtt.Client()
    if cfg.get('mqtt_username'):
        client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])
    client.on_connect    = lambda c, u, f, rc: log(f"{'✅ MQTT 연결 성공' if rc==0 else f'❌ MQTT 연결 실패 rc={rc}'}")
    client.on_disconnect = lambda c, u, rc: log("⚡ MQTT 연결 끊김")
    try:
        client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
        client.loop_start()
        time.sleep(1)
    except Exception as e:
        log(f"❌ MQTT 연결 실패: {e}")
    return client

# ──────────────────────────────────────────────
# 점수 추출 헬퍼
# ──────────────────────────────────────────────
def extract_score(item):
    score_div = item.find('div', class_='score')
    if score_div:
        a_tag = score_div.find('strong', class_='away')
        h_tag = score_div.find('strong', class_='home')
        if a_tag and h_tag:
            a, h = a_tag.get_text(strip=True), h_tag.get_text(strip=True)
            if a.isdigit() and h.isdigit():
                return a, h

    a_div = item.select_one('div.team.away div.score')
    h_div = item.select_one('div.team.home div.score')
    if a_div and h_div:
        a, h = a_div.get_text(strip=True), h_div.get_text(strip=True)
        if a.isdigit() and h.isdigit():
            return a, h

    return "", ""

# ──────────────────────────────────────────────
# 스크래핑 1회 실행
# ──────────────────────────────────────────────
def scrape_once(driver, target, prev_state):
    now = datetime.datetime.now()
    ts = now.strftime('%H:%M:%S')
    today = now.date()

    driver.get(URL)
    WebDriverWait(driver, 25).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont"))
    )
    time.sleep(1)

    soup = BeautifulSoup(driver.page_source, 'html.parser')
    items = soup.find_all('li', class_='game-cont')

    is_playing = False
    state_out = "데이터 없음"
    start_out = "00:00"
    g_status_raw = "정보 없음"
    display_status = "정보 없음"
    game_date = None
    next_check_at = None
    attr_data = {"status": "대기", "last_update": ts}

    found = False
    for item in items:
        away_nm = item.get('away_nm', '')
        home_nm = item.get('home_nm', '')
        if not away_nm or not home_nm:
            continue
        if target not in away_nm and target not in home_nm:
            continue

        found = True
        is_home = target in home_nm
        opponent = away_nm if is_home else home_nm

        top_lis = item.select('div.top > ul > li')
        date_str = ""
        time_str = ""
        for li in top_lis:
            text = li.get_text(strip=True)
            if re.search(r'\d{2}[./]\d{2}', text):
                date_str = text
            elif re.search(r'^\d{2}:\d{2}$', text):
                time_str = text

        if not time_str:
            time_li = item.select_one('div.top > ul > li:nth-child(3)')
            time_str = time_li.get_text(strip=True) if time_li else "시간미정"

        status_tag = item.find('p', class_='staus')
        g_status_raw = status_tag.get_text(strip=True) if status_tag else "상태불명"
        if "회" in g_status_raw:
            is_playing = True

        a_score, h_score = extract_score(item)
        has_score = a_score.isdigit() and h_score.isdigit()
        start_out = time_str
        if date_str:
            game_date = parse_game_date(date_str)
        elif is_playing or has_score:
            game_date = today
        else:
            game_date = None

        my_score = h_score if is_home else a_score
        opp_score = a_score if is_home else h_score
        my_symbol = "🔻" if is_home else "🔺"
        opp_symbol = "🔺" if is_home else "🔻"
        display_status = get_finished_status(g_status_raw, my_score, opp_score)

        if has_score:
            prefix = f"{display_status} " if "회" in display_status else f"[{display_status}] "
            state_out = (
                f"{prefix}{my_symbol}{target}({my_score})"
                f":{opp_symbol}{opponent}({opp_score})"
            )
        else:
            state_out = f"[{start_out} {display_status}] {my_symbol}{target} vs {opp_symbol}{opponent}"

        attr_data = {
            "home": home_nm,
            "away": away_nm,
            "is_home": is_home,
            "opponent": opponent,
            "status": display_status,
            "raw_status": g_status_raw,
            "my_score": my_score,
            "opp_score": opp_score,
            "start_time": start_out,
            "game_date": str(game_date) if game_date else "",
            "last_update": ts
        }
        break

    if not found:
        state_out = f"오늘 {target} 경기 없음"
        start_out = "00:00"
        g_status_raw = "경기없음"
        display_status = "경기없음"
        attr_data = {"status": "경기없음", "last_update": ts}
        next_check_at = tomorrow_13(now)

    if found and game_date is not None and game_date != today:
        next_check_at = next_check_for_game_date(game_date, start_out, now)
        state_out = (
            f"오늘 {target} 경기 없음 "
            f"(다음 경기: {game_date.strftime('%m/%d')} {start_out})"
        )
        g_status_raw = "경기없음"
        display_status = "경기없음"
        attr_data["status"] = "경기없음"
        attr_data["next_check"] = next_check_at.strftime('%Y-%m-%d %H:%M:%S')
        is_playing = False

    if state_out != prev_state:
        log(f"[{ts}] 📢 {state_out}")

    return {
        "state_out": state_out,
        "start_out": start_out,
        "g_status_raw": g_status_raw,
        "display_status": display_status,
        "is_playing": is_playing,
        "next_check_at": next_check_at,
        "attr_data": attr_data,
    }

# ──────────────────────────────────────────────
# 메인 루프
# ──────────────────────────────────────────────
def main():
    log("🚀 KBO Smart Scraper 시작!")
    cfg = load_config()
    target = cfg['target_team']
    eng = get_eng_team(target)

    topic_state = f"kbo/{eng}_sensor/state"
    topic_start = f"kbo/{eng}_sensor/starttime"
    topic_attr = f"kbo/{eng}_sensor/attributes"

    client = init_mqtt(cfg)

    prev_state = None
    last_success_is_playing = False
    consecutive_errors = 0
    scrape_count = 0
    driver = None

    while True:
        now = datetime.datetime.now()
        ts = now.strftime('%H:%M:%S')

        if not is_in_season(cfg):
            msg = f"⚾ 시즌 외 ({now.strftime('%m/%d')})"
            client.publish(topic_state, msg, retain=True)
            client.publish(topic_start, "00:00", retain=True)
            if prev_state != msg:
                log(f"[{ts}] {msg}")
                prev_state = msg
            time.sleep(cfg['interval_standby'] * 60)
            continue

        result = None
        error_occurred = False

        try:
            if driver is None:
                driver = init_driver()
                scrape_count = 0

            result = scrape_once(driver, target, prev_state)
            scrape_count += 1
            consecutive_errors = 0

            prev_state = result["state_out"]
            last_success_is_playing = result["is_playing"]

            client.publish(topic_state, result["state_out"], retain=True)
            client.publish(topic_start, result["start_out"], retain=True)
            client.publish(topic_attr, json.dumps(result["attr_data"], ensure_ascii=False), retain=True)

            if scrape_count >= MAX_DRIVER_REUSE:
                driver = close_driver(driver)

        except TimeoutException:
            error_occurred = True
            consecutive_errors += 1
            driver = close_driver(driver)
            log_error_once("timeout", consecutive_errors, f"[{ts}] ⚠️ KBO 페이지 타임아웃")

        except WebDriverException as e:
            error_occurred = True
            consecutive_errors += 1
            driver = close_driver(driver)
            err = str(e).split('\n')[0][:80]
            log_error_once("webdriver", consecutive_errors, f"[{ts}] ⚠️ WebDriver 오류: {err}")

        except Exception as e:
            error_occurred = True
            consecutive_errors += 1
            driver = close_driver(driver)
            err = str(e).split('\n')[0][:80]
            log_error_once("exception", consecutive_errors, f"[{ts}] ⚠️ 스크래핑 오류: {err}")

        now = datetime.datetime.now()

        if error_occurred:
            # 경기 중에는 상태를 덮어쓰지 않고 짧게 재시도. 비경기/대기 상태는 긴 간격.
            sleep_time = 30 if last_success_is_playing else 300

        elif result and result["is_playing"]:
            sleep_time = max(30, int(cfg['interval_game']) * 60)

        elif result and result["next_check_at"] is not None:
            delta = (result["next_check_at"] - now).total_seconds()
            sleep_time = max(60, delta)
            wakeup = result["next_check_at"].strftime('%m/%d %H:%M')
            log(f"[{now.strftime('%H:%M:%S')}] 😴 절전 → {wakeup} 에 경기 일정 확인")

        elif result and (
            "경기없음" in result["display_status"]
            or "종료" in result["g_status_raw"]
            or "취소" in result["g_status_raw"]
        ):
            target_dt = tomorrow_13(now)
            sleep_time = max(60, (target_dt - now).total_seconds())
            wakeup = target_dt.strftime('%m/%d %H:%M')
            log(f"[{now.strftime('%H:%M:%S')}] 😴 절전 → {wakeup} 에 경기 일정 확인")

        else:
            sleep_time = int(cfg['interval_standby']) * 60
            if result:
                gt = parse_game_time(result["start_out"])
                if gt is not None:
                    game_dt = now.replace(hour=gt[0], minute=gt[1], second=0, microsecond=0)
                    delta = (game_dt - now).total_seconds()
                    if delta <= 300:
                        sleep_time = max(30, int(cfg['interval_game']) * 60)
                    else:
                        sleep_time = max(60, delta - 300)
                        wakeup = (now + datetime.timedelta(seconds=sleep_time)).strftime('%H:%M')
                        log(f"[{now.strftime('%H:%M:%S')}] ⏳ 경기 {int(delta/60)}분 전 → {wakeup} 에 재개")

        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
