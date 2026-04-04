import json, time, datetime, sys, io, traceback, os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt

sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8', line_buffering=True)

def load_config():
    try:
        with open('/data/options.json', 'r') as f:
            return json.load(f)
    except:
        return {
            "target_team": "LG",
            "mqtt_broker": "192.168.0.40",
            "mqtt_port": 1883,
            "mqtt_username": "admin",
            "mqtt_password": "swallow77!",
            "season_start": "03-20",
            "season_end": "11-30",
            "interval_standby": 60,
            "interval_game": 1
        }

def get_eng_team(team):
    mapping = {
        "LG": "lg", "KIA": "kia", "SSG": "ssg", "NC": "nc",
        "두산": "doosan", "KT": "kt", "롯데": "lotte",
        "한화": "hanwha", "삼성": "samsung", "키움": "kiwoom"
    }
    return mapping.get(team, "unknown")

def init_driver():
    options = webdriver.ChromeOptions()

    # ✅ 수정1: Chromium 바이너리 경로 명시 (python:3.11-slim 기준)
    chromium_paths = [
        '/usr/bin/chromium',
        '/usr/bin/chromium-browser',
        '/usr/bin/google-chrome',
    ]
    for path in chromium_paths:
        if os.path.exists(path):
            options.binary_location = path
            print(f"✅ Chromium 경로: {path}", flush=True)
            break
    else:
        print("⚠️ Chromium 바이너리를 찾지 못했습니다!", flush=True)

    # ✅ 수정2: 컨테이너 환경 안정화 플래그
    options.add_argument("--headless=new")          # 최신 headless 모드
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--disable-setuid-sandbox")
    options.add_argument("--disable-extensions")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--single-process")        # 메모리 제한 환경 대응
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    )

    # ✅ 수정3: experimental_option 제거 (headless 충돌 원인)
    # 아래 두 줄은 최신 Chrome에서 오류 발생 → 삭제
    # options.add_experimental_option("excludeSwitches", ["enable-automation"])
    # options.add_experimental_option('useAutomationExtension', False)

    service = Service(executable_path='/usr/bin/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(40)
    return driver

def is_season(cfg):
    today = datetime.date.today()
    sm, sd = map(int, cfg['season_start'].split('-'))
    em, ed = map(int, cfg['season_end'].split('-'))
    start_date = datetime.date(today.year, sm, sd)
    end_date = datetime.date(today.year, em, ed)
    return start_date <= today <= end_date

def main():
    print("🚀 KBO Scraper Add-on 시작...", flush=True)
    cfg = load_config()
    target = cfg['target_team']
    eng_team = get_eng_team(target)

    topic_state = f"kbo/{eng_team}_sensor/state"
    topic_start = f"kbo/{eng_team}_sensor/starttime"
    topic_attr  = f"kbo/{eng_team}_sensor/attributes"

    print(f"📋 설정: 팀={target}, MQTT={cfg['mqtt_broker']}:{cfg['mqtt_port']}", flush=True)
    print(f"📋 시즌: {cfg['season_start']} ~ {cfg['season_end']}", flush=True)

    client = mqtt.Client()
    if cfg['mqtt_username']:
        client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])

    while True:
        try:
            client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
            client.loop_start()
            print(f"✅ MQTT 연결 성공 ({cfg['mqtt_broker']})", flush=True)
            break
        except Exception as e:
            print(f"❌ MQTT 연결 실패, 5초 후 재시도... ({e})", flush=True)
            time.sleep(5)

    while True:
        if not is_season(cfg):
            print("💤 비시즌. 24시간 후 재확인.", flush=True)
            time.sleep(86400)
            continue

        state_out = "데이터 없음"
        start_out = "데이터 없음"
        attr_data = {"status": "경기 없음"}
        is_playing = False
        error_occurred = False
        driver = None

        try:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 KBO 접속 중...", flush=True)
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")

            try:
                WebDriverWait(driver, 30).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont"))
                )
            except TimeoutException:
                print("⚠️ 경기 목록 로딩 타임아웃 - 페이지 소스로 진행", flush=True)

            time.sleep(3)
            soup = BeautifulSoup(driver.page_source, 'html.parser')

            if "504 Gateway" in soup.get_text():
                raise Exception("KBO 서버 504 에러")

            items = soup.find_all('li', class_='game-cont')
            print(f"📊 경기 목록 수: {len(items)}개", flush=True)

            found = False
            for item in items:
                away = item.get('away_nm', '')
                home = item.get('home_nm', '')
                if not away or not home:
                    continue
                if target not in away and target not in home:
                    continue

                found = True
                time_li = item.select_one('div.top > ul > li:nth-child(3)')
                start_out = time_li.get_text(strip=True) if time_li else "시간정보없음"

                g_status_tag = item.find('p', class_='staus')
                g_status_raw = g_status_tag.get_text(strip=True) if g_status_tag else "상태 불명"

                if "회" in g_status_raw:
                    is_playing = True

                # 점수 추출
                a_score, h_score = "", ""
                a_div = item.select_one('div.team.away div.score')
                h_div = item.select_one('div.team.home div.score')
                if a_div and h_div:
                    ta = a_div.get_text(strip=True)
                    th = h_div.get_text(strip=True)
                    if ta.isdigit() and th.isdigit():
                        a_score, h_score = ta, th

                if a_score.isdigit() and h_score.isdigit():
                    prefix = f"{g_status_raw} " if "회" in g_status_raw else f"[{g_status_raw}] "
                    if target in home:
                        state_out = f"{prefix}🔻{target}({h_score}):🔺{away}({a_score})"
                    else:
                        state_out = f"{prefix}🔺{target}({a_score}):🔻{home}({h_score})"
                else:
                    vs_text = f"🔻{target} vs 🔺{away}" if target in home else f"🔺{target} vs 🔻{home}"
                    if "종료" in g_status_raw:
                        state_out = f"[종료] {vs_text}"
                    elif ":" in g_status_raw or "경기" in g_status_raw or g_status_raw == "상태 불명":
                        state_out = f"[{start_out} 경기예정] {vs_text}"
                    else:
                        state_out = f"[{g_status_raw}] {vs_text}"

                attr_data = {
                    "opponent":   away if target in home else home,
                    "home_away":  "Home" if target in home else "Away",
                    "start_time": start_out,
                    "status":     g_status_raw,
                    "my_score":   h_score if target in home else a_score,
                    "opp_score":  a_score if target in home else h_score,
                }
                break

            if not found:
                state_out = f"오늘 {target} 경기 없음"
                start_out = "00:00"
                attr_data = {"status": "경기 없음"}

            # MQTT 발행
            client.publish(topic_state, state_out, retain=True)
            client.publish(topic_start, start_out, retain=True)
            client.publish(topic_attr, json.dumps(attr_data, ensure_ascii=False), retain=True)
            print(f"✅ 업데이트: {state_out}", flush=True)

        except Exception as e:
            err_msg = str(e).split('\n')[0]
            print(f"❌ 오류:\n{traceback.format_exc()}", flush=True)
            client.publish(topic_state, f"오류: {err_msg[:30]}", retain=True)
            error_occurred = True

        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

        # ── 수면 스케줄링 ──────────────────────────────────────
        now = datetime.datetime.now()

        if error_occurred:
            sleep_time = 300
            print("⚠️ 오류 발생. 5분 후 재시도.", flush=True)
        else:
            sleep_time = cfg['interval_game'] * 60 if is_playing else cfg['interval_standby'] * 60
            current_status = attr_data.get("status", "")

            # 경기 종료/없음 → 다음날 오후 1시까지 휴식
            if not is_playing and any(k in current_status or k in state_out
                                      for k in ["종료", "취소", "없음", "경기 없음"]):
                target_1pm = now.replace(hour=13, minute=0, second=0, microsecond=0)
                if now >= target_1pm:
                    target_1pm += datetime.timedelta(days=1)
                sleep_time = (target_1pm - now).total_seconds()
                print(f"😴 다음 확인: {target_1pm.strftime('%Y-%m-%d %H:%M')}", flush=True)

            # 경기 예정 → 경기 시작 시각에 맞춰 깨어나기
            elif not is_playing and ":" in start_out:
                try:
                    g_hour, g_min = map(int, start_out.split(':'))
                    game_dt = now.replace(hour=g_hour, minute=g_min, second=0, microsecond=0)
                    delta_sec = (game_dt - now).total_seconds()
                    if 0 < delta_sec < sleep_time:
                        print(f"⏰ 경기 시작({start_out})에 맞춰 대기.", flush=True)
                        sleep_time = delta_sec
                except:
                    pass

        print(f"💤 {sleep_time/60:.1f}분 대기...", flush=True)
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
