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

def load_config():
    try:
        with open('/data/options.json', 'r') as f:
            return json.load(f)
    except:
        return {"target_team": "LG", "mqtt_broker": "192.168.0.40", "mqtt_port": 1883, "mqtt_username": "admin", "mqtt_password": "swallow77!", "season_start": "03-20", "season_end": "11-30", "interval_standby": 60, "interval_game": 1}

def init_driver():
    options = webdriver.ChromeOptions()
    # 봇 감지 우회 옵션 강화
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option('useAutomationExtension', False)
    
    # ChromeDriver 경로 자동 확인 (보통 /usr/bin/chromedriver)
    chrome_path = "/usr/bin/chromedriver"
    if not os.path.exists(chrome_path):
        chrome_path = "chromedriver" # 경로가 다를 경우 대비
        
    service = Service(executable_path=chrome_path)
    driver = webdriver.Chrome(service=service, options=options)
    # 봇 감지 우회 스크립트 실행
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": "Object.defineProperty(navigator, 'webdriver', {get: () =>点 undefined})"
    })
    driver.set_page_load_timeout(30)
    return driver

def main():
    cfg = load_config()
    target = cfg['target_team']
    client = mqtt.Client()
    if cfg['mqtt_username']: client.username_pw_set(cfg['mqtt_username'], cfg['mqtt_password'])
    
    # MQTT 연결 (로그 추가)
    try:
        client.connect(cfg['mqtt_broker'], cfg['mqtt_port'], 60)
        client.loop_start()
        print(f"[{datetime.datetime.now()}] ✅ MQTT 연결 성공", flush=True)
    except Exception as e:
        print(f"❌ MQTT 연결 실패: {e}", flush=True)

    topic_state = f"kbo/lg_sensor/state"

    while True:
        driver = None
        error_msg = None
        try:
            print(f"[{datetime.datetime.now()}] 🔍 KBO 스크래핑 시작...", flush=True)
            driver = init_driver()
            driver.get("https://www.koreabaseball.com/Schedule/GameCenter/Main.aspx")
            
            # 페이지가 로딩될 때까지 대기
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.CSS_SELECTOR, "li.game-cont")))
            
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            # ... (중략: 기존 데이터 파싱 로직 동일) ...
            # 정상 작동 시 state_out 발행
            client.publish(topic_state, state_out, retain=True)
            print(f"✅ 데이터 업데이트 완료: {state_out}", flush=True)

        except Exception as e:
            # 에러 발생 시 상세 내용을 MQTT와 로그에 모두 뿌림
            full_error = traceback.format_exc()
            short_error = str(e).split('\n')[0]
            print(f"❌ 에러 발생 상세:\n{full_error}", flush=True)
            client.publish(topic_state, f"오류: {short_error[:20]}", retain=True)
        
        finally:
            if driver: driver.quit()
            time.sleep(cfg['interval_standby'] * 60)

if __name__ == "__main__":
    main()
