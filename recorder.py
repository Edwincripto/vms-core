import subprocess

print("Запуск прямого стриминга в MediaMTX (без архива)...")

# Название камеры берем из твоих предыдущих логов
CAMERA_NAME = "HD WebCam"

cmd = [
    "ffmpeg", 
    "-f", "dshow",                      
    "-i", f"video={CAMERA_NAME}",      
    "-c:v", "libx264",                  
    "-preset", "ultrafast",             
    "-tune", "zerolatency",             
    "-an",                             
    "-f", "rtsp",                        
    "rtsp://localhost:8554/cam1"
]

try:
    print("Начинаем трансляцию... Нажми Ctrl+C для остановки.")
    subprocess.run(cmd)
except KeyboardInterrupt:
    print("\nОстановка стриминга.")
except Exception as e:
    print(f"Критическая ошибка: {e}")