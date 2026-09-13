import ssl
ssl._create_default_https_context = ssl._create_unverified_context

import cv2
import time
from datetime import datetime
import requests
import easyocr
import re
import os
import json
from ultralytics import YOLO

STREAM_URL = "rtsp://localhost:8554/dahua_cam1"
SERVER_URL = "http://localhost:8080/api/anpr"

def format_az_plate(raw_text):
    """
    Очищает распознанный текст и проверяет азербайджанский шаблон (DD-LL-DDD).
    Возвращает отформатированный номер (например, 10-AZ-010) или None.
    """
    # Убираем все пробелы, тире и точки, оставляем только буквы и цифры
    clean_plate = re.sub(r'[^A-Z0-9]', '', raw_text.upper())
    
    # Ищем строгое совпадение: 2 цифры + 2 буквы + 3 цифры
    match = re.match(r'^(\d{2})([A-Z]{2})(\d{3})$', clean_plate)
    
    if match:
        # Возвращаем в красивом стандарте
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    
    return None

def start_anpr():
    print("Запуск модуля ANPR (Распознавание автономеров AZ)...")
    model = YOLO("yolov8n.pt")
    # Подключаем EasyOCR (gpu=False, чтобы работало на процессоре)
    reader = easyocr.Reader(['en'], gpu=False) # Убрали 'ru', для латиницы номеров достаточно 'en'
    
    cap = cv2.VideoCapture(STREAM_URL)
    last_processed_time = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            print("Нет сигнала потока, переподключение...")
            time.sleep(3)
            cap = cv2.VideoCapture(STREAM_URL)
            continue

        current_time = time.time()
        # Пауза между проверками — 4 секунды (чтобы не спамить один и тот же номер)
        if current_time - last_processed_time < 4:
            continue

        results = model(frame, verbose=False, conf=0.4)
        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                class_name = model.names[cls_id]
                
                if class_name in ['car', 'truck', 'bus']:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    car_crop = frame[y1:y2, x1:x2]
                    
                    if car_crop.size == 0:
                        continue

                    ocr_results = reader.readtext(car_crop)
                    for bbox, text, score in ocr_results:
                        
                        # Прогоняем текст через наш азербайджанский фильтр
                        plate_number = format_az_plate(text)
                        
                        # Если номер прошел проверку формата и уверенность ИИ выше 25%
                        if plate_number and score > 0.25:
                            timestamp = datetime.now().strftime("%H:%M:%S")
                            msg = f"🚗 Госномер: {plate_number} (Уверенность: {int(score*100)}%)"
                            print(f"[{timestamp}] ANPR: {msg}")
                            
                            payload = {
                                "timestamp": timestamp,
                                "plate": plate_number,
                                "score": int(score*100)
                            }
                            
                            # 1. Записываем в локальный JSON (чтобы Дашборд сразу их подхватил)
                            try:
                                log_file = 'anpr_log.json'
                                logs = []
                                if os.path.exists(log_file):
                                    with open(log_file, 'r', encoding='utf-8') as f:
                                        try: logs = json.load(f)
                                        except: pass
                                logs.append(payload)
                                logs = logs[-30:] # Храним только последние 30 номеров
                                with open(log_file, 'w', encoding='utf-8') as f:
                                    json.dump(logs, f, ensure_ascii=False, indent=4)
                            except Exception as e:
                                print(f"Ошибка записи в файл: {e}")

                            # 2. Дублируем POST-запросом на сервер (если там есть обработчик)
                            try:
                                requests.post(SERVER_URL, json=payload, timeout=2)
                            except:
                                pass
                            
                            last_processed_time = current_time
                            break

    cap.release()

if __name__ == "__main__":
    start_anpr()