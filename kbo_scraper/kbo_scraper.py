import json, time, datetime, sys, io, traceback, os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt

# 로그 즉시 출력을 위한 설정 (Hass.io 로그 확인용)
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
    mapping = {"LG":"lg", "KIA":"kia", "SSG":"ssg", "NC":"nc", "두산":"doosan", "KT":"kt", "롯데":"lotte", "한화":"hanwha", "삼성":"samsung", "키움":"kiwoom"}
    return mapping.get(team, "unknown")

def init_driver():
    options = webdriver.ChromeOptions()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    
    # 봇 감지 우회 설정
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    service = Service(executable_path='/usr/bin/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)
    
    # 웹드라이버 흔적 제거 스크립트
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
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
    topic_attr = f"kbo/{eng_team}_sensor/attributes"

    client = mqtt.Client()
    if cfg['mqtt_username']: 
        client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])
    
    # MQTT 연결 시도
    while True:
        try:
            client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
            client.loop_start()
            print(f"✅ MQTT 서버({cfg['mqtt_broker']}) 연결 성공!", flush=True)
            break
        except Exception as e:
    # 1. 어떤 에러인지 상세히 기록
    full_error = traceback.format_exc()
    # 2. 만약 페이지 소스를 가져올 수 있다면, 앞부분 200자만 잘라서 센서로 쏩니다.
    debug_html = ""
    if driver:
        try:
            debug_html = driver.page_source[:200].replace('\n', ' ')
        except:
            debug_html = "페이지 소스 확보 실패"
    
    # 3. MQTT 센서에 범인 검거 메시지 전송
    error_summary = f"❌ {str(e)[:20]} | HTML: {debug_html}"
    client.publish(topic_state, error_summary, retain=True)
    
    print(f"🚨 [에러 검거] 상세내용:\n{full_error}", flush=True)
    error_occurred = True

    while True:
        if not is_season(cfg):
            print("💤 비시즌입니다. 24시간 뒤에 다시 확인합니다.", flush=True)
            time.sleep(86400)
            continue

        state_out, start_out = "데이터 없음", "데이터 없음"
        attr_data = {"status": "경기 없음"}
        is_playing = False
        error_occurred = False
        driver = None
        
        try:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 KBO 웹페이지 접속 중...", flush=True)
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")
            
            # 페이지 핵심 요소 대기
            try:
                WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont")))
            except TimeoutException:
                print("⚠️ 페이지 로딩 지연 (경기 목록을 찾을 수 없음)", flush=True)

            time.sleep(3)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
            # 504 Gateway Time-out 체크
            if "504 Gateway" in soup.get_text():
                raise Exception("KBO 서버 504 에러 발생")

            items = soup.find_all('li', class_='game-cont')
            found = False
            for item in items:
                away = item.get('away_nm')
                home = item.get('home_nm')
                if not away or not home: continue
                
                if target in away or target in home:
                    found = True
                    time_li = item.select_one('div.top > ul > li:nth-child(3)')
                    start_out = time_li.get_text(strip=True) if time_li else "시간정보없음"
                    
                    g_status_raw = item.find('p', class_='staus')
                    g_status_raw = g_status_raw.get_text(strip=True) if g_status_raw else "상태 불명"
                    
                    if "회" in g_status_raw: is_playing = True
                    
                    # 점수 추출 로직
                    a_score, h_score = "", ""
                    a_div = item.select_one('div.team.away div.score')
                    h_div = item.select_one('div.team.home div.score')
                    if a_div and h_div:
                        ta, th = a_div.get_text(strip=True), h_div.get_text(strip=True)
                        if ta.isdigit() and th.isdigit(): a_score, h_score = ta, th
                        
                    if a_score.isdigit() and h_score.isdigit():
                        prefix = f"{g_status_raw} " if "회" in g_status_raw else f"[{g_status_raw}] "
                        if target in home:
                            state_out = f"{prefix}🔻{target}({h_score}):🔺{away}({a_score})"
                        else:
                            state_out = f"{prefix}🔺{target}({a_score}):🔻{home}({h_score})"
                    else:
                        vs_text = f"🔻{target} vs 🔺{away}" if target in home else f"🔺{target} vs 🔻{home}"
                        if ":" in g_status_raw or "경기" in g_status_raw or g_status_raw == "상태 불명":
                            state_out = f"[{start_out} 경기예정] {vs_text}" 
                        else:
                            state_out = f"[{g_status_raw}] {vs_text}"
                    
                    attr_data = {
                        "opponent": away if target in home else home, 
                        "home_away": "Home" if target in home else "Away",
                        "start_time": start_out, "status": g_status_raw,
                        "my_score": h_score if target in home else a_score, 
                        "opp_score": a_score if target in home else h_score
                    }
                    break
                    
            if not found: 
                state_out, start_out = f"오늘 {target} 경기 없음", "00:00"
                attr_data = {"status": "경기 없음"}
            
            # MQTT 발행
            client.publish(topic_state, state_out, retain=True)
            client.publish(topic_start, start_out, retain=True)
            client.publish(topic_attr, json.dumps(attr_data, ensure_ascii=False), retain=True)
            print(f"✅ 업데이트 완료: {state_out}", flush=True)
            
        except Exception as e:
            err_msg = str(e).split('\n')[0]
            print(f"❌ 스크래핑 오류 상세: {traceback.format_exc()}", flush=True)
            client.publish(topic_state, f"오류: {err_msg[:25]}", retain=True)
            error_occurred = True
            
        finally:
            if driver: driver.quit()
            
        # --- 수면 스케줄링 로직 ---
        now = datetime.datetime.now()
        
        # 1. 오류 발생 시 -> 5분 뒤 재시도
        if error_occurred:
            sleep_time = 300
            print(f"⚠️ 5분 후 재시도합니다.", flush=True)
        else:
            sleep_time = cfg['interval_game'] * 60 if is_playing else cfg['interval_standby'] * 60
            current_status = attr_data.get("status", "")
            
            # 2. 경기 종료/취소/없음 -> 다음 날 오후 1시까지 절전
            if not is_playing and ("종료" in current_status or "취소" in current_status or "없음" in current_status or "경기 없음" in state_out):
                target_1pm = now.replace(hour=13, minute=0, second=0, microsecond=0)
                if now >= target_1pm: target_1pm += datetime.timedelta(days=1)
                sleep_time = (target_1pm - now).total_seconds()
                print(f"😴 업무 종료. 오후 1시까지 휴식: {target_1pm.strftime('%Y-%m-%d %H:%M')}", flush=True)
            
            # 3. 경기 예정 상태 -> 경기 시작 정각에 깨어나기
            elif not is_playing and ":" in start_out:
                try:
                    g_hour, g_min = map(int, start_out.split(':'))
                    game_dt = now.replace(hour=g_hour, minute=g_min, second=0, microsecond=0)
                    delta_sec = (game_dt - now).total_seconds()
                    if 0 < delta_sec < sleep_time:
                        print(f"⏰ 경기 시작({start_out}) 정각에 깨어납니다.", flush=True)
                        sleep_time = delta_sec
                except: pass
                
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
