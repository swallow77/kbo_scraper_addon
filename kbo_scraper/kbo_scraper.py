import json, time, datetime, sys, io, traceback, os
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt

# 로그 즉시 출력을 위한 설정
sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8', line_buffering=True)
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8', line_buffering=True)

def load_config():
    try:
        with open('/data/options.json', 'r') as f:
            return json.load(f)
    except:
        return {
            "target_team": "LG", "mqtt_broker": "192.168.0.40", "mqtt_port": 1883,
            "mqtt_username": "admin", "mqtt_password": "swallow77!",
            "season_start": "03-20", "season_end": "11-30",
            "interval_standby": 60, "interval_game": 1
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
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    service = Service(executable_path='/usr/bin/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    })
    driver.set_page_load_timeout(45)
    return driver

def main():
    print("🚀 KBO Scraper 가동 시작...", flush=True)
    cfg = load_config()
    target = cfg['target_team']
    eng_team = get_eng_team(target)
    
    topic_state = f"kbo/{eng_team}_sensor/state"
    topic_start = f"kbo/{eng_team}_sensor/starttime"
    topic_attr = f"kbo/{eng_team}_sensor/attributes"

    client = mqtt.Client()
    if cfg['mqtt_username']: client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])
    
    try:
        client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
        client.loop_start()
        client.publish(topic_state, "🔄 데이터 읽는 중...", retain=True)
    except Exception as e:
        print(f"❌ MQTT 연결 실패: {e}", flush=True)

    while True:
        is_playing, error_occurred = False, False
        state_out, start_out, g_status_raw = "데이터 없음", "00:00", "정보 없음"
        attr_data = {"status": "대기"}
        driver = None
        
        try:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 🔍 KBO 접속 시도...", flush=True)
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")
            
            WebDriverWait(driver, 35).until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont")))
            time.sleep(5)
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            items = soup.find_all('li', class_='game-cont')
            
            found = False
            for item in items:
                away, home = item.get('away_nm'), item.get('home_nm')
                if not away or not home: continue
                
                if target in away or target in home:
                    found = True
                    time_li = item.select_one('div.top > ul > li:nth-child(3)')
                    start_out = time_li.get_text(strip=True) if time_li else "시간미정"
                    
                    status_tag = item.find('p', class_='staus')
                    g_status_raw = status_tag.get_text(strip=True) if status_tag else "상태불명"
                    if "회" in g_status_raw: is_playing = True
                    
                    a_score, h_score = "", ""
                    a_div = item.select_one('div.team.away div.score')
                    h_div = item.select_one('div.team.home div.score')
                    if a_div and h_div:
                        ta, th = a_div.get_text(strip=True), h_div.get_text(strip=True)
                        if ta.isdigit() and th.isdigit(): a_score, h_score = ta, th
                    
                    if a_score.isdigit() and h_score.isdigit():
                        prefix = f"{g_status_raw} " if "회" in g_status_raw else f"[{g_status_raw}] "
                        state_out = f"{prefix}🔻{target}({h_score}):🔺{away}({a_score})" if target in home else f"{prefix}🔺{target}({a_score}):🔻{home}({h_score})"
                    else:
                        vs_text = f"🔻{target} vs 🔺{away}" if target in home else f"🔺{target} vs 🔻{home}"
                        state_out = f"[{start_out} {g_status_raw}] {vs_text}"
                    
                    attr_data = {
                        "opponent": away if target in home else home, "status": g_status_raw,
                        "my_score": h_score if target in home else a_score, "opp_score": a_score if target in home else h_score,
                        "last_update": datetime.datetime.now().strftime('%H:%M:%S')
                    }
                    break
            
            if not found:
                state_out, start_out, g_status_raw = f"오늘 {target} 경기 없음", "00:00", "경기없음"

            client.publish(topic_state, state_out, retain=True)
            client.publish(topic_start, start_out, retain=True)
            client.publish(topic_attr, json.dumps(attr_data, ensure_ascii=False), retain=True)
            print(f"✅ 업데이트: {state_out}", flush=True)

        except Exception as e:
            # ★ 여기 들여쓰기가 중요합니다 ★
            full_error = traceback.format_exc()
            err_detail = str(e).split('\n')[0][:30]
            print(f"❌ 스크래핑 에러 상세:\n{full_error}", flush=True)
            client.publish(topic_state, f"⚠️ 오류: {err_detail}", retain=True)
            error_occurred = True
            
        finally:
            if driver: driver.quit()

        now = datetime.datetime.now()
        if error_occurred:
            sleep_time = 300 
        else:
            sleep_time = cfg['interval_game'] * 60 if is_playing else cfg['interval_standby'] * 60
            if not is_playing and ("종료" in g_status_raw or "취소" in g_status_raw or "경기 없음" in state_out):
                target_1pm = now.replace(hour=13, minute=0, second=0, microsecond=0)
                if now >= target_1pm: target_1pm += datetime.timedelta(days=1)
                sleep_time = (target_1pm - now).total_seconds()
                print(f"😴 절전 모드 진입", flush=True)
            elif not is_playing and ":" in start_out:
                try:
                    gh, gm = map(int, start_out.split(':'))
                    game_dt = now.replace(hour=gh, minute=gm, second=0, microsecond=0)
                    delta = (game_dt - now).total_seconds()
                    if 0 < delta < sleep_time: sleep_time = delta
                except: pass
        
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
