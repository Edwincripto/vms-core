import subprocess
import sys
import time

print("Инициализация VMS & BI Core...")

# 1. Запускаем MediaMTX в фоновом режиме
print("-> Запуск медиасервера (MediaMTX)...")
mediamtx_process = subprocess.Popen(["mediamtx.exe"])

# 2. Запускаем наш умный API-сервер
print("-> Запуск умного VMS-сервера (порт 8080)...")
http_server_process = subprocess.Popen([sys.executable, "server.py"])

try:
    print("\n====================================================")
    print("✅ ВСЕ СИСТЕМЫ ЗАПУЩЕНЫ И РАБОТАЮТ!")
    print("🌐 Открой в браузере ссылку: http://localhost:8080/dashboard.html")
    print("🛑 Для полной остановки нажми Ctrl + C в этом окне")
    print("====================================================\n")
    
    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\nОстановка серверов...")
    mediamtx_process.terminate()
    http_server_process.terminate()
    print("✅ Работа VMS & BI Core завершена.")