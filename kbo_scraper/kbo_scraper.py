import json, time, datetime, sys, io, traceback, os
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
            print(f"✅ 설정 파일 로드 완료: {cfg}", flush=True)
            return cfg
    except Exception as e:
        print(f"⚠️  /data/options.json 로드 실패 ({e}), 기본값 사용", flush=True)
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
# 웹드라이버 초기화 (컨테이너 환경 최적화)
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

    print(f"🔧 ChromeDriver 경로: {chromedriver_path}", flush=True)
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
    """away/home 점수를 (away_score, home_score) 형태로 반환. 없으면 ("", "")"""
    # 방식 1: 경기 진행 중 (div.score > strong.away / strong.home)
    score_div = item.find('div', class_='score')
    if score_div:
        a_tag = score_div.find('strong', class_='away')
        h_tag = score_div.find('strong', class_='home')
        if a_tag and h_tag:
            a, h = a_tag.get_text(strip=True), h_tag.get_text(strip=True)
            if a.isdigit() and h.isdigit():
                return a, h

    # 방식 2: 경기 종료 (div.team.away div.score / div.team.home div.score)
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

    while True:
        now = datetime.datetime.now()
        ts  = now.strftime('%H:%M:%S')

        # 시즌 외 체크
        if not is_in_season(cfg):
            msg = f"⚾ 시즌 외 ({now.strftime('%m/%d')})"
            client.publish(topic_state, msg, retain=True)
            client.publish(topic_start, "00:00", retain=True)
            print(f"[{ts}] {msg}", flush=True)
            time.sleep(cfg['interval_standby'] * 60)
            continue

        # ── 스크래핑 ──────────────────────────────
        is_playing     = False
        error_occurred = False
        state_out      = "데이터 없음"
        start_out      = "00:00"
        g_status_raw   = "정보 없음"
        attr_data      = {"status": "대기", "last_update": ts}
        driver         = None

        try:
            print(f"[{ts}] 🔍 KBO 접속 중...", flush=True)
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")

            WebDriverWait(driver, 40).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont"))
            )
            time.sleep(3)

            soup  = BeautifulSoup(driver.page_source, 'html.parser')
            items = soup.find_all('li', class_='game-cont')
            print(f"[{ts}] 발견된 경기 수: {len(items)}", flush=True)

            found = False
            for item in items:
                away_nm = item.get('away_nm', '')
                home_nm = item.get('home_nm', '')
                if not away_nm or not home_nm:
                    continue

                if target not in away_nm and target not in home_nm:
                    continue

                found      = True
                is_home    = target in home_nm
                opponent   = away_nm if is_home else home_nm

                # 시작 시간
                time_li = item.select_one('div.top > ul > li:nth-child(3)')
                start_out = time_li.get_text(strip=True) if time_li else "시간미정"

                # 상태 텍스트
                status_tag = item.find('p', class_='staus')
                g_status_raw = status_tag.get_text(strip=True) if status_tag else "상태불명"
                if "회" in g_status_raw:
                    is_playing = True

                # 점수 추출
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
                    "last_update": ts
                }
                print(f"[{ts}] 경기 발견: {state_out}", flush=True)
                break

            if not found:
                state_out    = f"오늘 {target} 경기 없음"
                start_out    = "00:00"
                g_status_raw = "경기없음"
                attr_data    = {"status": "경기없음", "last_update": ts}
                print(f"[{ts}] {state_out}", flush=True)

            # MQTT 발행
            client.publish(topic_state, state_out, retain=True)
            client.publish(topic_start, start_out, retain=True)
            client.publish(topic_attr,  json.dumps(attr_data, ensure_ascii=False), retain=True)
            print(f"[{ts}] ✅ MQTT 발행 완료", flush=True)

        except TimeoutException:
            print(f"[{ts}] ❌ 페이지 로딩 타임아웃", flush=True)
            client.publish(topic_state, "⚠️ 로딩 타임아웃", retain=True)
            error_occurred = True

        except WebDriverException as e:
            err = str(e).split('\n')[0][:60]
            print(f"[{ts}] ❌ WebDriver 오류: {err}", flush=True)
            traceback.print_exc()
            client.publish(topic_state, f"⚠️ 드라이버 오류", retain=True)
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

        if error_occurred:
            sleep_time = 300
            print(f"[{now.strftime('%H:%M:%S')}] ⏳ 오류 발생, {sleep_time}초 후 재시도", flush=True)

        elif is_playing:
            sleep_time = cfg['interval_game'] * 60
            print(f"[{now.strftime('%H:%M:%S')}] ⚾ 경기 중 - {sleep_time}초 후 갱신", flush=True)

        elif "종료" in g_status_raw or "취소" in g_status_raw or "경기없음" in g_status_raw:
            target_dt = now.replace(hour=13, minute=0, second=0, microsecond=0)
            if now >= target_dt:
                target_dt += datetime.timedelta(days=1)
            sleep_time = max(60, (target_dt - now).total_seconds())
            print(f"[{now.strftime('%H:%M:%S')}] 😴 절전 모드 - {int(sleep_time/3600)}시간 {int((sleep_time%3600)/60)}분 후 재개", flush=True)

        else:
            sleep_time = cfg['interval_standby'] * 60
            if ":" in start_out:
                try:
                    parts = start_out.strip().split(':')
                    gh, gm = int(parts[0]), int(parts[1])  # 앞 두 개만 사용
                    game_dt = now.replace(hour=gh, minute=gm, second=0, microsecond=0)
                    delta = (game_dt - now).total_seconds()
                    print(f"[{now.strftime('%H:%M:%S')}] 🕐 start_out='{start_out}' delta={int(delta)}초", flush=True)
        
                    if delta <= 0:
                        sleep_time = cfg['interval_game'] * 60
                        print(f"[{now.strftime('%H:%M:%S')}] ⚾ 경기 시작 시간 경과 - {int(sleep_time)}초 간격으로 체크", flush=True)
                    elif delta < sleep_time:
                        sleep_time = max(60, delta)
                        print(f"[{now.strftime('%H:%M:%S')}] ⏳ 경기 {int(delta/60)}분 전 - {int(sleep_time)}초 후 갱신", flush=True)
                    else:
                        print(f"[{now.strftime('%H:%M:%S')}] ⏳ 경기 전 대기 - {int(sleep_time/60)}분 후 갱신", flush=True)
                except Exception as parse_err:
                    print(f"[{now.strftime('%H:%M:%S')}] ⚠️ 시간 파싱 오류: start_out='{start_out}' err={parse_err}", flush=True)
            else:
                print(f"[{now.strftime('%H:%M:%S')}] ⏳ 경기 전 대기 - {int(sleep_time/60)}분 후 갱신", flush=True)

        time.sleep(sleep_time)


if __name__ == "__main__":
    main()
