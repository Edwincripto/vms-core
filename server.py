import http.server
import socketserver
import json
import subprocess
import os
import time
import threading
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime
import requests # ДЛЯ ОТПРАВКИ ФОТО В TELEGRAM

PORT = 8080
RETENTION_DAYS = 7
DB_FILE = "vms_database.db"

# Настройки Telegram
BOT_TOKEN = "8786557810:AAE1eb_Pmj8eQSp6NFbdkuUSHD6VHgLfVXU"
SUPER_ADMIN_CHAT_ID = "386048422"

login_requests = {}
last_telegram_update_id = 0

# Настройки системы (Глобальный рубильник)
SYSTEM_SETTINGS = {
    "motion_enabled": True
}

class ThreadingHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS operators (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            name TEXT,
            role TEXT,
            department TEXT,
            status TEXT DEFAULT 'active'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT,
            contact_person TEXT,
            telegram_chat_id TEXT UNIQUE,
            assigned_camera TEXT,
            status TEXT DEFAULT 'active'
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            initiator TEXT,
            module TEXT,
            description TEXT
        )
    ''')
    
    cursor.execute("PRAGMA table_info(operators)")
    columns = [row[1] for row in cursor.fetchall()]
    if 'username' not in columns:
        cursor.execute("ALTER TABLE operators ADD COLUMN username TEXT")
    if 'status' not in columns:
        cursor.execute("ALTER TABLE operators ADD COLUMN status TEXT DEFAULT 'active'")

    seed_users = [
        ("admin", "Mustafayev Edvin", "SuperAdmin", "Management", "active"),
        ("op_leyla", "Aliyev Leyla", "Operator", "Sales", "active"),
        ("op_rashad", "Hasanov Rashad", "Operator", "Support", "active")
    ]
    for u in seed_users:
        cursor.execute('''
            INSERT OR IGNORE INTO operators (username, name, role, department, status)
            VALUES (?, ?, ?, ?, ?)
        ''', u)
        cursor.execute('''
            UPDATE operators 
            SET username = ?, role = ?, status = ? 
            WHERE name = ?
        ''', (u[0], u[2], u[4], u[1]))

    cursor.execute('''
        INSERT OR IGNORE INTO clients (company_name, contact_person, telegram_chat_id, assigned_camera, status)
        VALUES (?, ?, ?, ?, ?)
    ''', ("Склад Маркетплейса", "Ильхам Мамедов", "386048422", "dahua_cam1", "active"))

    conn.commit()
    conn.close()

init_db()

def log_audit(initiator, module, description):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute('''
            INSERT INTO audit_logs (timestamp, initiator, module, description)
            VALUES (?, ?, ?, ?)
        ''', (timestamp, initiator, module, description))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Audit Log Error] {e}")

def send_telegram_message(chat_id, text, reply_markup=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML"
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode('utf-8'))
        urllib.request.urlopen(req, timeout=5)
        return True
    except Exception as e:
        print(f"[TG Send Error] {e}")
        return False

def send_telegram_photo(chat_id, caption, image_path):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, 'rb') as photo:
            payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
            files = {"photo": photo}
            requests.post(url, data=payload, files=files, timeout=10)
    except Exception as e:
        print(f"[TG Photo Error] {e}")
        send_telegram_message(chat_id, caption)

def send_telegram_approval(request_id, operator_name):
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "✅ Разрешить смену", "callback_data": f"approve:{request_id}"},
                {"text": "❌ Отклонить", "callback_data": f"reject:{request_id}"}
            ]
        ]
    }
    text = (f"🔐 <b>Запрос на вход в VMS</b>\n\n"
            f"Оператор: <b>{operator_name}</b>\n"
            f"Время: {datetime.now().strftime('%H:%M:%S')}\n\n"
            f"Подтвердить доступ к мониторингу?")
    send_telegram_message(SUPER_ADMIN_CHAT_ID, text, keyboard)

def telegram_polling_worker():
    global last_telegram_update_id
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_telegram_update_id + 1}&timeout=10"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                if data.get("ok"):
                    for update in data.get("result", []):
                        last_telegram_update_id = update["update_id"]
                        if "callback_query" in update:
                            cb = update["callback_query"]
                            cb_data = cb.get("data", "")
                            action, req_id = cb_data.split(":", 1) if ":" in cb_data else (None, None)
                            
                            if req_id and req_id in login_requests:
                                target_name = login_requests[req_id]["user"]
                                if action == "approve":
                                    login_requests[req_id]["status"] = "approved"
                                    response_text = f"✅ Доступ для {target_name} РАЗРЕШЕН"
                                    log_audit("Telegram Bot", "ДОСТУП", f"Разрешен вход в систему для: {target_name}")
                                elif action == "reject":
                                    login_requests[req_id]["status"] = "rejected"
                                    response_text = f"❌ Вход для {target_name} ОТКЛОНЕН"
                                    log_audit("Telegram Bot", "ДОСТУП", f"Отклонена попытка входа: {target_name}")
                                
                                ack_url = f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery"
                                ack_data = urllib.parse.urlencode({"callback_query_id": cb["id"], "text": response_text}).encode('utf-8')
                                urllib.request.urlopen(urllib.request.Request(ack_url, data=ack_data), timeout=5)
                                
                                edit_url = f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText"
                                edit_data = urllib.parse.urlencode({
                                    "chat_id": SUPER_ADMIN_CHAT_ID,
                                    "message_id": cb["message"]["message_id"],
                                    "text": f"{response_text}\nОбработано в: {datetime.now().strftime('%H:%M:%S')}"
                                }).encode('utf-8')
                                urllib.request.urlopen(urllib.request.Request(edit_url, data=edit_data), timeout=5)
        except Exception:
            pass
        time.sleep(1)

threading.Thread(target=telegram_polling_worker, daemon=True).start()

class VMSHandler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        for page in ['login.html', 'dashboard.html', 'clients.html', 'audit.html', 'events.html', 'export.html']:
            if self.path in [f'/{page}', '/'] and os.path.exists(page):
                target = 'login.html' if self.path == '/' else page
                with open(target, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return

        if self.path == '/api/settings':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(SYSTEM_SETTINGS).encode('utf-8'))
            return

        if self.path == '/api/operators_list':
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('SELECT username, name, role FROM operators WHERE role != "SuperAdmin" AND status = "active"')
                rows = cursor.fetchall()
                conn.close()
                ops = [{"username": r[0], "name": r[1], "role": r[2]} for r in rows]
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(ops).encode('utf-8'))
            except Exception as e:
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'[]')
            return

        elif self.path == '/api/admin/operators':
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('SELECT id, username, name, role, department, status FROM operators WHERE role != "SuperAdmin" ORDER BY id DESC')
                rows = cursor.fetchall()
                conn.close()
                ops = [{
                    "id": r[0],
                    "username": r[1],
                    "name": r[2],
                    "role": r[3],
                    "department": r[4],
                    "status": r[5]
                } for r in rows]
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(ops).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        elif self.path == '/api/clients':
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('SELECT id, company_name, contact_person, telegram_chat_id, assigned_camera, status FROM clients ORDER BY id DESC')
                clients = []
                for r in cursor.fetchall():
                    clients.append({
                        "id": r[0],
                        "company_name": r[1],
                        "contact_person": r[2],
                        "telegram_chat_id": r[3],
                        "assigned_camera": r[4],
                        "status": r[5]
                    })
                conn.close()
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(clients).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        elif self.path.startswith('/api/check_auth_status?req_id='):
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            req_id = params.get('req_id', [None])[0]
            status = login_requests.get(req_id, {}).get("status", "not_found")
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": status}).encode('utf-8'))
            return

        elif self.path == '/api/audit':
            logs = [{"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "user": "SYSTEM", "type": "СТАТУС", "desc": "База SQLite активна. Диспетчер доступа онлайн."}]
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('SELECT timestamp, initiator, module, description FROM audit_logs ORDER BY id DESC LIMIT 50')
                for row in cursor.fetchall():
                    logs.append({"time": row[0], "user": row[1], "type": row[2], "desc": row[3]})
                conn.close()
            except: pass
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(logs).encode('utf-8'))
            return

        elif self.path == '/api/anpr':
            if os.path.exists('anpr_log.json'):
                try:
                    with open('anpr_log.json', 'r', encoding='utf-8') as f:
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(f.read().encode('utf-8'))
                        return
                except: pass
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')
            return

        elif self.path == '/api/alarms':
            if os.path.exists('alarms.json'):
                try:
                    with open('alarms.json', 'r', encoding='utf-8') as f:
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(f.read().encode('utf-8'))
                        return
                except: pass
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(b'[]')
            return

        super().do_GET()

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else '{}'
        try:
            data = json.loads(body) if body else {}
        except:
            data = {}

        if self.path == '/api/settings':
            global SYSTEM_SETTINGS
            SYSTEM_SETTINGS['motion_enabled'] = data.get('motion_enabled', True)
            
            status_text = "ВКЛЮЧЕН" if SYSTEM_SETTINGS['motion_enabled'] else "ВЫКЛЮЧЕН"
            log_audit("ADMIN", "СИСТЕМА", f"Детектор движения {status_text}")
            
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "settings": SYSTEM_SETTINGS}).encode('utf-8'))
            return

        if self.path == '/api/request_access':
            username = data.get('username')
            user_name = data.get('name')
            req_id = f"req_{int(time.time())}_{os.urandom(2).hex()}"
            login_requests[req_id] = {
                "user": user_name or username,
                "status": "pending",
                "time": time.time()
            }
            send_telegram_approval(req_id, user_name or username)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "pending", "req_id": req_id}).encode('utf-8'))
            return

        elif self.path == '/api/save_operator':
            op_id = data.get('id')
            username = data.get('username')
            name = data.get('name')
            department = data.get('department', 'Охрана / Мониторинг')
            role = data.get('role', 'Operator')
            status = data.get('status', 'active')

            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                if op_id:
                    cursor.execute('''
                        UPDATE operators 
                        SET username = ?, name = ?, department = ?, role = ?, status = ?
                        WHERE id = ?
                    ''', (username, name, department, role, status, op_id))
                    log_audit("ADMIN", "ПЕРСОНАЛ", f"Обновлены данные сотрудника: {name}")
                else:
                    cursor.execute('''
                        INSERT INTO operators (username, name, department, role, status)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (username, name, department, role, status))
                    log_audit("ADMIN", "ПЕРСОНАЛ", f"Добавлен новый сотрудник: {name}")
                conn.commit()
                conn.close()

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Данные сотрудника сохранены!"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        elif self.path == '/api/toggle_operator_status':
            op_id = data.get('id')
            new_status = data.get('status')
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('UPDATE operators SET status = ? WHERE id = ?', (new_status, op_id))
                conn.commit()
                conn.close()
                log_audit("ADMIN", "ПЕРСОНАЛ", f"Изменен статус аккаунта ID {op_id} на '{new_status}'")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        elif self.path == '/api/delete_operator':
            op_id = data.get('id')
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('DELETE FROM operators WHERE id = ?', (op_id,))
                conn.commit()
                conn.close()
                log_audit("ADMIN", "ПЕРСОНАЛ", f"Удален аккаунт оператора ID {op_id}")
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        elif self.path == '/api/save_client':
            company = data.get('company_name')
            contact = data.get('contact_person')
            chat_id = data.get('telegram_chat_id')
            camera = data.get('assigned_camera', 'dahua_cam1')
            
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT INTO clients (company_name, contact_person, telegram_chat_id, assigned_camera, status)
                    VALUES (?, ?, ?, ?, 'active')
                    ON CONFLICT(telegram_chat_id) DO UPDATE SET
                    company_name=excluded.company_name,
                    contact_person=excluded.contact_person,
                    assigned_camera=excluded.assigned_camera
                ''', (company, contact, chat_id, camera))
                conn.commit()
                conn.close()

                send_telegram_message(
                    chat_id, 
                    f"🤝 <b>Здравствуйте, {contact}!</b>\n\nВаш объект <b>«{company}»</b> успешно привязан к системе VMS Core.\nУведомления безопасности будут поступать в этот чат."
                )
                
                log_audit("ADMIN", "ОБЪЕКТЫ", f"Зарегистрирован/обновлен клиент: {company}")

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Клиент сохранен и уведомлен!"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        elif self.path == '/api/trigger_camera_alert':
            camera = data.get('camera', 'dahua_cam1')
            event_type = data.get('type', 'Обнаружено движение')
            image_path = data.get('image_path')
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            cursor.execute('SELECT company_name, contact_person, telegram_chat_id FROM clients WHERE assigned_camera = ? AND status = "active"', (camera,))
            targets = cursor.fetchall()
            conn.close()

            alert_text = (f"🚨 <b>СИГНАЛ БЕЗОПАСНОСТИ VMS</b>\n\n"
                          f"Камера: <code>{camera}</code>\n"
                          f"Событие: <b>{event_type}</b>\n"
                          f"Время: {timestamp}\n\n"
                          f"<i>Проверьте видеопоток в личном кабинете.</i>")

            for t in targets:
                chat_id = t[2]
                caption = f"⚠️ <b>Внимание, {t[1]} ({t[0]})!</b>\n" + alert_text
                if image_path and os.path.exists(image_path):
                    send_telegram_photo(chat_id, caption, image_path)
                else:
                    send_telegram_message(chat_id, caption)

            admin_caption = f"🔔 [Дубликат Админу] Тревога по камере {camera}\n" + alert_text
            if image_path and os.path.exists(image_path):
                send_telegram_photo(SUPER_ADMIN_CHAT_ID, admin_caption, image_path)
            else:
                send_telegram_message(SUPER_ADMIN_CHAT_ID, admin_caption)

            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "sent_to": len(targets)}).encode('utf-8'))
            return

        elif self.path == '/api/clear_alarms':
            try:
                with open('alarms.json', 'w', encoding='utf-8') as f:
                    json.dump([], f)
                with open('anpr_log.json', 'w', encoding='utf-8') as f:
                    json.dump([], f)
                log_audit("ADMIN", "СИСТЕМА", "Очищен кэш ИИ (Тревоги и Номера)")
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "success", "message": "Кэш ИИ успешно очищен!"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
            return

        self.send_response(404)
        self.end_headers()

print(f"Сервер VMS (RBAC + Clients & Operators Admin Core + Settings & Photos) запущен на порту {PORT}")
with ThreadingHTTPServer(("", PORT), VMSHandler) as httpd:
    httpd.serve_forever()