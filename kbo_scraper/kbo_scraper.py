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

# ──────────────────────────────────────────────
# 설정 로드
# ──────────────────────────────────────────────
def load_config():
    try:
        with open('/data/options.json', 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            print(f"✅ 설정 로드: 팀={cfg['target_team']}, MQTT={cfg['mqtt_broker']}", flush=True)
            return cfg
    except Exception as e:
        print(f"⚠️  설정 로드 실패 ({e}), 기본값 사용", flush=True)
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
        print(f"⚠️  시즌 체크 오류: {e}", flush=True)
        return True

# ──────────────────────────────────────────────
# 날짜 파싱: "05.04 (일)" 또는 "05/04" 등 → datetime.date
# 실패 시 None 반환
# ──────────────────────────────────────────────
def parse_game_date(date_str):
    """
    KBO 사이트의 날짜 문자열을 파싱해서 datetime.date 반환
    예: "05.04 (일)", "05/04", "05.04" → date(2026, 5, 4)
    """
    try:
        # 숫자만 뽑아서 월/일 추출 (예: "05.04 (일)" → ["05", "04"])
        nums = re.findall(r'\d+', date_str)
        if len(nums) >= 2:
            month, day = int(nums[0]), int(nums[1])
            year = datetime.datetime.now().year
            return datetime.date(year, month, day)
    except Exception:
        pass
    return None

# ──────────────────────────────────────────────
# 시간 파싱: "18:30" → (18, 30) / 실패 시 None
# ──────────────────────────────────────────────
def parse_game_time(time_str):
    if ":" not in time_str:
        return None
    try:
        parts = time_str.strip().split(':')
        return int(parts[0]), int(parts[1])
    except Exception:
        return None

# ──────────────────────────────────────────────
# 웹드라이버 초기화
# ──────────────────────────────────────────────
def init_driver():
    options = webdriver.ChromeOptions()
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
    options.add_argument("--single-process")
    options.add_argument("--memory-pressure-off")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
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
    driver.set_page_load_timeout(60)
    return driver

# ──────────────────────────────────────────────
# MQTT 클라이언트 초기화
# ──────────────────────────────────────────────
def init_mqtt(cfg):
    client = mqtt.Client()
    if cfg.get('mqtt_username'):
        client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])
    client.on_connect    = lambda c, u, f, rc: print(f"{'✅ MQTT 연결 성공' if rc==0 else f'❌ MQTT 연결 실패 rc={rc}'}", flush=True)
    client.on_disconnect = lambda c, u, rc: print("⚡ MQTT 연결 끊김", flush=True)
    try:
        client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
        client.loop_start()
        time.sleep(1)
    except Exception as e:
        print(f"❌ MQTT 연결 실패: {e}", flush=True)
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
# 메인 루프
# ──────────────────────────────────────────────
def main():
    print("🚀 KBO Smart Scraper 시작!", flush=True)
    cfg    = load_config()
    target = cfg['target_team']
    eng    = get_eng_team(target)

    topic_state = f"kbo/{eng}_sensor/state"
    topic_start = f"kbo/{eng}_sensor/starttime"
    topic_attr  = f"kbo/{eng}_sensor/attributes"

    client = init_mqtt(cfg)

    prev_state = None  # 상태 변경 시에만 로그 출력용

    while True:
        now = datetime.datetime.now()
        ts  = now.strftime('%H:%M:%S')
        today = now.date()

        # 시즌 외 체크
        if not is_in_season(cfg):
            msg = f"⚾ 시즌 외 ({now.strftime('%m/%d')})"
            client.publish(topic_state, msg, retain=True)
            client.publish(topic_start, "00:00", retain=True)
            if prev_state != msg:
                print(f"[{ts}] {msg}", flush=True)
                prev_state = msg
            time.sleep(cfg['interval_standby'] * 60)
            continue

        # ── 스크래핑 ──────────────────────────────
        is_playing     = False
        error_occurred = False
        state_out      = "데이터 없음"
        start_out      = "00:00"
        g_status_raw   = "정보 없음"
        game_date      = None   # ★ 실제 경기 날짜 (datetime.date)
        attr_data      = {"status": "대기", "last_update": ts}
        driver         = None

        try:
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")

            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont"))
            )
            time.sleep(3)

            soup  = BeautifulSoup(driver.page_source, 'html.parser')
            items = soup.find_all('li', class_='game-cont')

            found = False
            for item in items:
                away_nm = item.get('away_nm', '')
                home_nm = item.get('home_nm', '')
                if not away_nm or not home_nm:
                    continue
                if target not in away_nm and target not in home_nm:
                    continue

                found   = True
                is_home = target in home_nm
                opponent = away_nm if is_home else home_nm

                # ★ div.top > ul 안의 모든 li 읽기 (날짜/구장/시간 파싱)
                top_lis = item.select('div.top > ul > li')

                # 디버그: 처음 실행 시 top_lis 내용을 한 번만 출력
                if prev_state is None:
                    li_texts = [f"li[{i+1}]='{li.get_text(strip=True)}'" for i, li in enumerate(top_lis)]
                    print(f"[{ts}] 🔎 top_lis: {', '.join(li_texts)}", flush=True)

                # li 순서에서 날짜(MM.DD 패턴)와 시간(HH:MM 패턴) 추출
                date_str = ""
                time_str = ""
                for li in top_lis:
                    text = li.get_text(strip=True)
                    # 날짜 패턴: "05.04" or "05/04" or "05.04 (일)"
                    if re.search(r'\d{2}[./]\d{2}', text):
                        date_str = text
                    # 시간 패턴: "18:30" or "14:00"
                    elif re.search(r'^\d{2}:\d{2}$', text):
                        time_str = text

                # 날짜 파싱 못하면 li:nth-child(3) fallback
                if not time_str:
                    time_li = item.select_one('div.top > ul > li:nth-child(3)')
                    time_str = time_li.get_text(strip=True) if time_li else "시간미정"

                start_out = time_str
                game_date = parse_game_date(date_str) if date_str else None

                print(f"[{ts}] 🕐 날짜='{date_str}' ({game_date}) 시간='{start_out}'", flush=True)

                status_tag = item.find('p', class_='staus')
                g_status_raw = status_tag.get_text(strip=True) if status_tag else "상태불명"
                if "회" in g_status_raw:
                    is_playing = True

                a_score, h_score = extract_score(item)
                my_score   = h_score if is_home else a_score
                opp_score  = a_score if is_home else h_score
                my_symbol  = "🔻" if is_home else "🔺"
                opp_symbol = "🔺" if is_home else "🔻"

                if my_score.isdigit() and opp_score.isdigit():
                    prefix = f"{g_status_raw} " if "회" in g_status_raw else f"[{g_status_raw}] "
                    state_out = (
                        f"{prefix}{my_symbol}{target}({my_score})"
                        f":{opp_symbol}{opponent}({opp_score})"
                    )
                else:
                    state_out = f"[{start_out} {g_status_raw}] {my_symbol}{target} vs {opp_symbol}{opponent}"

                attr_data = {
                    "home":        home_nm,
                    "away":        away_nm,
                    "is_home":     is_home,
                    "opponent":    opponent,
                    "status":      g_status_raw,
                    "my_score":    my_score,
                    "opp_score":   opp_score,
                    "start_time":  start_out,
                    "game_date":   str(game_date) if game_date else "",
                    "last_update": ts
                }
                break

            if not found:
                state_out    = f"오늘 {target} 경기 없음"
                start_out    = "00:00"
                g_status_raw = "경기없음"
                attr_data    = {"status": "경기없음", "last_update": ts}

            # ★ 날짜 기반 오탐 감지
            # game_date가 파싱됐고 오늘 날짜가 아니면 → 확실히 오늘 경기 없음
            if found and game_date is not None and game_date != today:
                # 내일(또는 미래) 경기 → 오늘은 경기 없음으로 처리
                gt = parse_game_time(start_out)
                game_dt_future = None
                if gt:
                    game_dt_future = datetime.datetime.combine(game_date, datetime.time(gt[0], gt[1]))

                state_out    = (
                    f"오늘 {target} 경기 없음 "
                    f"(다음 경기: {game_date.strftime('%m/%d')} {start_out})"
                )
                g_status_raw = "경기없음"
                attr_data["status"] = "경기없음"
                attr_data["next_game"] = str(game_dt_future) if game_dt_future else ""
                is_playing = False
                print(f"[{ts}] 📅 날짜 불일치! KBO 표시={game_date} / 오늘={today} → 오늘 경기 없음", flush=True)

            # 상태 변경 시에만 로그
            if state_out != prev_state:
                print(f"[{ts}] 📢 {state_out}", flush=True)
                prev_state = state_out

            client.publish(topic_state, state_out, retain=True)
            client.publish(topic_start, start_out, retain=True)
            client.publish(topic_attr,  json.dumps(attr_data, ensure_ascii=False), retain=True)

        except TimeoutException:
            print(f"[{ts}] ❌ 페이지 로딩 타임아웃", flush=True)
            client.publish(topic_state, "⚠️ 로딩 타임아웃", retain=True)
            error_occurred = True

        except WebDriverException as e:
            err = str(e).split('\n')[0][:60]
            print(f"[{ts}] ❌ WebDriver 오류: {err}", flush=True)
            traceback.print_exc()
            client.publish(topic_state, "⚠️ 드라이버 오류", retain=True)
            error_occurred = True

        except Exception as e:
            print(f"[{ts}] ❌ 예외 발생:\n{traceback.format_exc()}", flush=True)
            client.publish(topic_state, "⚠️ 스크래핑 오류", retain=True)
            error_occurred = True

        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

        # ── 대기 시간 계산 ────────────────────────
        now = datetime.datetime.now()
        today = now.date()

        if error_occurred:
            sleep_time = 300
            print(f"[{now.strftime('%H:%M:%S')}] ⏳ 오류 발생, 5분 후 재시도", flush=True)

        elif is_playing:
            # 경기 진행 중 → 1분 간격
            sleep_time = cfg['interval_game'] * 60

        elif "경기없음" in g_status_raw or "종료" in g_status_raw or "취소" in g_status_raw:
            # ★ 경기 없음/종료/취소
            # game_date가 미래 날짜면 → 그 날 5분 전까지 슬립
            # 아니면 → 내일 오후 1시까지 절전
            gt = parse_game_time(start_out)
            if (game_date is not None and game_date > today and gt is not None):
                # 다음 경기 날짜/시간이 확정됨 → 그 날 5분 전까지 슬립
                game_dt_future = datetime.datetime.combine(game_date, datetime.time(gt[0], gt[1]))
                delta = (game_dt_future - now).total_seconds()
                if delta <= 300:
                    sleep_time = cfg['interval_game'] * 60
                    print(f"[{now.strftime('%H:%M:%S')}] ⚾ 경기 임박 - 1분 간격 체크", flush=True)
                else:
                    sleep_time = max(60, delta - 300)
                    wakeup = (now + datetime.timedelta(seconds=sleep_time)).strftime('%m/%d %H:%M')
                    print(f"[{now.strftime('%H:%M:%S')}] 😴 절전 → {wakeup} ({game_date.strftime('%m/%d')} 경기 5분 전) 에 재개", flush=True)
            else:
                # 다음 경기 날짜 모름 → 내일 오후 1시에 확인
                target_dt = now.replace(hour=13, minute=0, second=0, microsecond=0)
                if now >= target_dt:
                    target_dt += datetime.timedelta(days=1)
                sleep_time = max(60, (target_dt - now).total_seconds())
                wakeup = target_dt.strftime('%m/%d %H:%M')
                print(f"[{now.strftime('%H:%M:%S')}] 😴 절전 → {wakeup} 에 경기 일정 확인", flush=True)

        else:
            # 경기 전 → 5분 전까지 한 번에 슬립
            sleep_time = cfg['interval_standby'] * 60
            gt = parse_game_time(start_out)
            if gt is not None:
                game_dt = now.replace(hour=gt[0], minute=gt[1], second=0, microsecond=0)
                delta = (game_dt - now).total_seconds()
                if delta <= 300:
                    sleep_time = cfg['interval_game'] * 60
                    print(f"[{now.strftime('%H:%M:%S')}] ⚾ 경기 임박 - 1분 간격 체크", flush=True)
                else:
                    sleep_time = max(60, delta - 300)
                    wakeup = (now + datetime.timedelta(seconds=sleep_time)).strftime('%H:%M')
                    print(f"[{now.strftime('%H:%M:%S')}] ⏳ 경기 {int(delta/60)}분 전 → {wakeup} 에 재개", flush=True)

        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
