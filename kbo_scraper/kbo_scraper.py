import json, time, datetime, sys, io, traceback
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException
from bs4 import BeautifulSoup
import paho.mqtt.client as mqtt

sys.stdout = io.TextIOWrapper(sys.stdout.detach(), encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.detach(), encoding='utf-8')

def load_config():
    try:
        with open('/data/options.json', 'r') as f:
            return json.load(f)
    except:
        return {"target_team": "LG", "mqtt_broker": "192.168.0.40", "mqtt_port": 1883, "mqtt_username": "admin", "mqtt_password": "swallow77!", "season_start": "03-20", "season_end": "11-30", "interval_standby": 60, "interval_game": 1}

def get_eng_team(team):
    mapping = {"LG":"lg", "KIA":"kia", "SSG":"ssg", "NC":"nc", "두산":"doosan", "KT":"kt", "롯데":"lotte", "한화":"hanwha", "삼성":"samsung", "키움":"kiwoom"}
    return mapping.get(team, "unknown")

def init_driver():
    options = webdriver.ChromeOptions()
    for arg in ["--headless", "--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]: 
        options.add_argument(arg)
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
    service = Service(executable_path='/usr/bin/chromedriver')
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(60)
    return driver

def is_season(cfg):
    today = datetime.date.today()
    sm, sd = map(int, cfg['season_start'].split('-'))
    em, ed = map(int, cfg['season_end'].split('-'))
    start_date = datetime.date(today.year, sm, sd)
    end_date = datetime.date(today.year, em, ed)
    return start_date <= today <= end_date

def main():
    cfg = load_config()
    target = cfg['target_team']
    eng_team = get_eng_team(target)
    
    topic_state = f"kbo/{eng_team}_sensor/state"
    topic_start = f"kbo/{eng_team}_sensor/starttime"
    topic_attr = f"kbo/{eng_team}_sensor/attributes"

    client = mqtt.Client()
    if cfg['mqtt_username']: 
        client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])
    
    while True:
        try:
            client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
            client.loop_start()
            break
        except:
            time.sleep(5)

    while True:
        if not is_season(cfg):
            time.sleep(86400)
            continue

        state_out, start_out = "경기 목록 없음", "경기 목록 없음"
        attr_data = {"status": "경기 없음"}
        is_playing = False
        driver = None
        
        try:
            print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] KBO 웹페이지 로딩 시도...")
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")
            
            try:
                WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont")))
            except TimeoutException:
                print("경기 요소 로딩 시간 초과 (오늘 경기가 없거나 KBO 사이트 지연)")

            time.sleep(3)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            
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
                    
                    g_state = item.find('p', class_='staus')
                    g_state = g_state.get_text(strip=True) if g_state else "상태 불명"
                    if "회" in g_state: is_playing = True
                    
                    a_score, h_score = "", ""
                    sc_old = item.find('div', class_='score')
                    if sc_old:
                        a_tag = sc_old.find('strong', class_='away')
                        h_tag = sc_old.find('strong', class_='home')
                        if a_tag: a_score = a_tag.get_text(strip=True)
                        if h_tag: h_score = h_tag.get_text(strip=True)
                        
                    if not (a_score.isdigit() and h_score.isdigit()):
                        a_div = item.select_one('div.team.away div.score')
                        h_div = item.select_one('div.team.home div.score')
                        ta = a_div.get_text(strip=True) if a_div else ""
                        th = h_div.get_text(strip=True) if h_div else ""
                        if ta.isdigit() and th.isdigit(): a_score, h_score = ta, th
                        
                    if a_score.isdigit() and h_score.isdigit():
                        prefix = f"{g_state} " if "회" in g_state else ""
                        if target in home:
                            state_out = f"{prefix}🔻{target}({h_score}):🔺{away}({a_score})"
                        else:
                            state_out = f"{prefix}🔺{target}({a_score}):🔻{home}({h_score})"
                    else:
                        # 점수가 없는 경우 (경기 전, 우천 취소 등)
                        vs_text = f"🔻{target} vs 🔺{away}" if target in home else f"🔺{target} vs 🔻{home}"
                        if ":" in g_state or "경기" in g_state or g_state == "상태 불명":
                            state_out = f"[{start_out} 경기예정] {vs_text}" 
                        else:
                            state_out = f"[{g_state}] {vs_text}"
                    
                    # 속성(Attributes) 데이터 구성
                    attr_data = {
                        "opponent": away if target in home else home,
                        "home_away": "Home" if target in home else "Away",
                        "start_time": start_out,
                        "status": g_state,
                        "my_score": h_score if target in home else a_score,
                        "opp_score": a_score if target in home else h_score
                    }
                    break
                    
            if not found: 
                state_out, start_out = f"오늘 {target} 경기 없음", f"오늘 {target} 경기 없음"
                attr_data = {"status": "경기 없음"}
            
            client.publish(topic_state, state_out, retain=True)
            client.publish(topic_start, start_out, retain=True)
            client.publish(topic_attr, json.dumps(attr_data, ensure_ascii=False), retain=True)
            
        except Exception as e:
            print(f"스크래핑 중 오류 발생: {e}")
            traceback.print_exc()
            client.publish(topic_state, "KBO 확인 오류", retain=True)
            
        finally:
            if driver: driver.quit()
            
        sleep_time = cfg['interval_game'] * 60 if is_playing else cfg['interval_standby'] * 60
        time.sleep(sleep_time)

if __name__ == "__main__":
    main()
