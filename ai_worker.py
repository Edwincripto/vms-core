import cv2
import time
import json
import os
import requests
import threading

print("Запуск ИИ-аналитики (Motion Detection + Screenshots)...")

CAMERA_NAME = "dahua_cam1"
RTSP_URL = f"rtsp://localhost:8554/{CAMERA_NAME}"
SERVER_ALERT_URL = "http://localhost:8080/api/trigger_camera_alert"
SERVER_SETTINGS_URL = "http://localhost:8080/api/settings"

# Глобальный флаг состояния детектора (по умолчанию включен)
MOTION_ENABLED = True

def fetch_settings():
    global MOTION_ENABLED
    while True:
        try:
            res = requests.get(SERVER_SETTINGS_URL, timeout=2)
            if res.status_code == 200:
                MOTION_ENABLED = res.json().get("motion_enabled", True)
        except Exception:
            pass
        time.sleep(3)

# Запускаем проверку настроек в фоновом режиме
threading.Thread(target=fetch_settings, daemon=True).start()

if not os.path.exists("alerts"):
    os.makedirs("alerts")

cap = cv2.VideoCapture(RTSP_URL)
fgbg = cv2.createBackgroundSubtractorMOG2(history=500, varThreshold=150, detectShadows=False)

last_alarm_time = 0
COOLDOWN = 15
ALARM_FILE = "alarms.json"

with open(ALARM_FILE, "w", encoding="utf-8") as f:
    json.dump([], f)

while True:
    ret, frame = cap.read()
    if not ret:
        print("Ожидание видеопотока...")
        time.sleep(2)
        cap = cv2.VideoCapture(RTSP_URL)
        continue

    # Если рубильник выключен — пропускаем кадр (экономим процессор), 
    # но продолжаем читать поток, чтобы он не завис.
    if not MOTION_ENABLED:
        time.sleep(0.05)
        continue

    resized = cv2.resize(frame, (640, 360))
    blurred = cv2.GaussianBlur(resized, (21, 21), 0)
    fgmask = fgbg.apply(blurred)
    fgmask = cv2.medianBlur(fgmask, 5)
    
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    fgmask = cv2.morphologyEx(fgmask, cv2.MORPH_CLOSE, kernel)

    contours, _ = cv2.findContours(fgmask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    motion_detected = False
    for contour in contours:
        if cv2.contourArea(contour) > 5000:
            motion_detected = True
            break
    
    current_time = time.time()
    
    if motion_detected and (current_time - last_alarm_time > COOLDOWN):
        print(f"🚨 ДЕТЕКЦИЯ: Движение зафиксировано! Делаем скриншот...")
        last_alarm_time = current_time
        
        alert_filename = f"alerts/alert_{int(current_time)}.jpg"
        cv2.imwrite(alert_filename, frame)
        
        alarm_data = {
            "id": int(current_time),
            "title": "Вторжение в зону (Движение)",
            "camera": CAMERA_NAME,
            "sla": 15,
            "timestamp": current_time
        }
        
        try:
            if os.path.exists(ALARM_FILE):
                with open(ALARM_FILE, "r", encoding="utf-8") as f:
                    try:
                        alarms = json.load(f)
                    except json.JSONDecodeError:
                        alarms = []
            else:
                alarms = []
            
            alarms.append(alarm_data)
            with open(ALARM_FILE, "w", encoding="utf-8") as f:
                json.dump(alarms[-20:], f, ensure_ascii=False)
                
        except Exception as e:
            print(f"Ошибка записи JSON: {e}")

        try:
            payload = {
                "camera": CAMERA_NAME,
                "type": "Вторжение в зону (Движение)",
                "image_path": alert_filename
            }
            requests.post(SERVER_ALERT_URL, json=payload, timeout=2)
            print("📨 Скриншот передан на сервер для отправки в Telegram!")
        except Exception as e:
            print(f"⚠️ Ошибка связи с ядром VMS: {e}")

cap.release()