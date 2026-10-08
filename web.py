import os
import json
import subprocess
import threading
import urllib.request
import base64
import shutil
import asyncio
from datetime import datetime
from flask import Flask, render_template, render_template_string, request, redirect, session, jsonify
from telethon import TelegramClient
from telethon.tl.types.auth import SentCodeTypeApp, SentCodeTypeSms, SentCodeTypeCall, SentCodeTypeFlashCall
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneNumberBannedError
)

BASE_DIR = "/root/farm_panel"
DATA_DIR = "/root/farm_data"
os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(BASE_DIR, "accounts.json")
BACKUP_CONFIG_FILE = os.path.join(DATA_DIR, "accounts.json")

SETTINGS_FILE = os.path.join(BASE_DIR, "config.json")
BACKUP_SETTINGS_FILE = os.path.join(DATA_DIR, "config.json")

TRASH_FILE = os.path.join(BASE_DIR, "trash.json")
BACKUP_TRASH_FILE = os.path.join(DATA_DIR, "trash.json")

TRASH_DIR = os.path.join(BASE_DIR, "trash")
BACKUP_TRASH_DIR = os.path.join(DATA_DIR, "trash")
os.makedirs(TRASH_DIR, exist_ok=True)
os.makedirs(BACKUP_TRASH_DIR, exist_ok=True)

tg_loop = asyncio.new_event_loop()
def _start_tg_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=_start_tg_loop, args=(tg_loop,), daemon=True).start()

def run_tg(coro):
    future = asyncio.run_coroutine_threadsafe(coro, tg_loop)
    return future.result(timeout=60)

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
app.secret_key = os.urandom(24)

login_sessions = {}

def load_settings():
    default_settings = {
        "admin_user": "admin",
        "admin_pass": "superpass123",
        "source_channel": "vaycher_virtnumber",
        "target_bot": "Virtnumber_buy_bot",
        "custom_domain": "farm.696752.xyz",
        "nodes": []
    }
    target_f = SETTINGS_FILE if (os.path.exists(SETTINGS_FILE) and os.path.getsize(SETTINGS_FILE) > 10) else BACKUP_SETTINGS_FILE
    if not os.path.exists(target_f):
        return default_settings
    try:
        with open(target_f, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in default_settings.items():
                data.setdefault(k, v)
            return data
    except Exception:
        return default_settings

def save_settings(settings):
    for path in [SETTINGS_FILE, BACKUP_SETTINGS_FILE]:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

def load_accounts():
    accs = []
    for path in [CONFIG_FILE, BACKUP_CONFIG_FILE]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if len(content) > len(accs):
                        accs = content
            except Exception:
                pass
    return accs

def save_accounts(accounts):
    for path in [CONFIG_FILE, BACKUP_CONFIG_FILE]:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(accounts, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

def load_trash():
    trash = []
    for path in [TRASH_FILE, BACKUP_TRASH_FILE]:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if len(content) > len(trash):
                        trash = content
            except Exception:
                pass
    return trash

def save_trash(trash):
    for path in [TRASH_FILE, BACKUP_TRASH_FILE]:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(trash, f, indent=4, ensure_ascii=False)
        except Exception:
            pass

def move_acc_to_trash(session_name: str, reason: str = "Удален вручную"):
    s_target = str(session_name).strip()
    accs = load_accounts()
    target = None
    new_accs = []
    for a in accs:
        if str(a.get("session")).strip() == s_target:
            target = a
        else:
            new_accs.append(a)
    save_accounts(new_accs)

    if not target:
        target = {"session": s_target, "phone": "-"}

    target["trash_reason"] = reason
    target["deleted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    trash = load_trash()
    trash = [t for t in trash if str(t.get("session")).strip() != s_target]
    trash.append(target)
    save_trash(trash)

    for base in [BASE_DIR, DATA_DIR]:
        t_dir = os.path.join(base, "trash")
        for ext in [".session", ".session-journal"]:
            src = os.path.join(base, f"{s_target}{ext}")
            dst = os.path.join(t_dir, f"{s_target}{ext}")
            if os.path.exists(src):
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass

def get_server_ips():
    primary_ip = None
    secondary_ip = None

    try:
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=2.5) as r:
            primary_ip = r.read().decode("utf-8").strip()
    except Exception:
        pass

    local_ips = []
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show", "scope", "global"], text=True)
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip = line.split()[1].split("/")[0]
                if not ip.startswith(("172.17.", "172.18.", "172.19.", "127.", "169.254.")):
                    local_ips.append(ip)
    except Exception:
        pass

    if not primary_ip:
        primary_ip = local_ips[0] if local_ips else "127.0.0.1"

    for ip in local_ips:
        if ip != primary_ip and not ip.startswith("10.") and not ip.startswith("192.168."):
            secondary_ip = ip
            break

    return primary_ip, secondary_ip

def get_service_status():
    try:
        res = subprocess.run(["systemctl", "is-active", "farmbot"], capture_output=True, text=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"

def trigger_remote_restart(node):
    url = f"{node['url'].rstrip('/')}/service/restart"
    req = urllib.request.Request(url, data=b"", method="POST")
    if node.get("user") and node.get("pass"):
        auth = base64.b64encode(f"{node['user']}:{node['pass']}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            pass
    except Exception:
        pass

@app.before_request
def require_login():
    settings = load_settings()
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Basic "):
        try:
            auth_decoded = base64.b64decode(auth_header.split(" ")[1]).decode("utf-8")
            u, p = auth_decoded.split(":", 1)
            if u == settings["admin_user"] and p == settings["admin_pass"]:
                return
        except Exception:
            pass

    allowed = ["/login", "/static"]
    if any(request.path.startswith(p) for p in allowed):
        return
    if not session.get("logged_in"):
        return redirect("/login")

@app.route("/login", methods=["GET", "POST"])
def login():
    settings = load_settings()
    error = None
    if request.method == "POST":
        user = request.form.get("username")
        pwd = request.form.get("password")
        if user == settings["admin_user"] and pwd == settings["admin_pass"]:
            session["logged_in"] = True
            return redirect("/")
        error = "Неверный логин или пароль"

    login_template_path = os.path.join(BASE_DIR, "templates", "login.html")
    if os.path.exists(login_template_path):
        return render_template("login.html", error=error)
    
    fallback_html = """
    <!DOCTYPE html>
    <html lang="ru"><head><meta charset="UTF-8"><title>Вход</title><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-slate-950 text-white flex items-center justify-center min-h-screen">
        <div class="bg-slate-900 border border-slate-800 p-8 rounded-2xl max-w-sm w-full">
            <h1 class="text-lg font-bold mb-4">Вход в FarmBot</h1>
            {% if error %}<div class="bg-rose-500/20 text-rose-400 p-2.5 rounded-lg text-xs mb-4">{{ error }}</div>{% endif %}
            <form method="post" class="flex flex-col gap-3">
                <input name="username" placeholder="Логин" required class="bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white outline-none">
                <input name="password" type="password" placeholder="Пароль" required class="bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white outline-none">
                <button type="submit" class="mt-2 bg-sky-600 hover:bg-sky-500 py-2.5 rounded-lg text-xs font-medium">Войти</button>
            </form>
        </div>
    </body></html>
    """
    return render_template_string(fallback_html, error=error)

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
def index():
    settings = load_settings()
    primary_ip, secondary_ip = get_server_ips()
    accounts = load_accounts()
    trash_accs = load_trash()

    primary_accs = [a for a in accounts if not a.get("bind_ip") or a.get("bind_ip") == primary_ip]
    secondary_accs = [a for a in accounts if a.get("bind_ip") == secondary_ip] if secondary_ip else []

    available_ips = [{"val": "", "label": f"Основной IP ({primary_ip})"}]
    if secondary_ip:
        available_ips.append({"val": secondary_ip, "label": f"Дополнительный IP ({secondary_ip})"})

    nodes = settings.get("nodes", [])
    current_display_host = settings.get("custom_domain") if settings.get("custom_domain") else primary_ip

    return render_template(
        "index.html",
        service_status=get_service_status(),
        primary_ip=primary_ip,
        secondary_ip=secondary_ip,
        current_display_host=current_display_host,
        primary_accs=primary_accs,
        secondary_accs=secondary_accs,
        available_ips=available_ips,
        settings=settings,
        linked_nodes=nodes,
        trash_accs=trash_accs
    )

async def _do_start(session_path, api_id, api_hash, phone, bind_ip):
    kwargs = {
        "api_id": api_id,
        "api_hash": api_hash,
        "loop": tg_loop,
        "device_model": "Desktop x64",
        "system_version": "Windows 11",
        "app_version": "5.5.1 x64",
        "lang_code": "ru",
        "system_lang_code": "ru-RU"
    }
    if bind_ip:
        if ":" in bind_ip:
            kwargs["use_ipv6"] = True
        kwargs["local_addr"] = (bind_ip, 0)

    for ext in [".session", ".session-journal"]:
        p = f"{session_path}{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    client = TelegramClient(session_path, **kwargs)
    await client.connect()
    sent = await client.send_code_request(phone)
    
    delivery = "в приложение Telegram"
    if isinstance(sent.type, SentCodeTypeSms):
        delivery = "по SMS на телефон (проверь СМС)"
    elif isinstance(sent.type, SentCodeTypeCall):
        delivery = "звонком на телефон"
    elif isinstance(sent.type, SentCodeTypeFlashCall):
        delivery = "через Flash Call"

    return client, sent.phone_code_hash, delivery

async def _do_finish(client, phone, code, code_hash, pwd):
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
    except SessionPasswordNeededError:
        if pwd:
            await client.sign_in(password=pwd)
        else:
            raise SessionPasswordNeededError("Требуется облачный 2FA пароль")
    await client.disconnect()

@app.route("/start-phone-login", methods=["POST"])
def start_phone_login():
    session_name = request.form.get("session_name", "").strip()
    phone = request.form.get("phone", "").strip().replace(" ", "").replace("-", "")
    api_id = request.form.get("api_id", "").strip()
    api_hash = request.form.get("api_hash", "").strip()
    bind_ip = request.form.get("bind_ip", "").strip()

    if not session_name or not phone or not api_id or not api_hash:
        return "Заполните все поля", 400

    session_path = os.path.join(BASE_DIR, session_name)
    try:
        client, code_hash, delivery = run_tg(_do_start(session_path, int(api_id), api_hash, phone, bind_ip))
        login_sessions[session_name] = {
            "client": client,
            "session_name": session_name,
            "phone": phone,
            "api_id": int(api_id),
            "api_hash": api_hash,
            "bind_ip": bind_ip,
            "phone_code_hash": code_hash
        }
    except FloodWaitError as e:
        return f"<h3>Telegram временно ограничил отправку кодов (FloodWait). Подождите {e.seconds} секунд.</h3><br><a href='/'>Назад</a>", 429
    except (PhoneNumberInvalidError, PhoneNumberBannedError) as e:
        return f"<h3>Номер заблокирован или неверен: {e}</h3><br><a href='/'>Назад</a>", 400
    except Exception as e:
        return f"<h3>Ошибка при запросе кода: {e}</h3><br><a href='/'>Назад</a>", 400

    return f"""
    <!DOCTYPE html>
    <html lang="ru"><head><meta charset="UTF-8"><title>Подтверждение Telegram</title><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-slate-950 text-white flex items-center justify-center min-h-screen">
        <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl max-w-sm w-full">
            <h2 class="text-base font-bold mb-2">Код подтверждения</h2>
            <p class="text-xs text-slate-400 mb-1">Код отправлен на <span class="text-sky-400 font-mono">{phone}</span></p>
            <p class="text-xs text-amber-400 mb-4">Способ доставки: <b>{delivery}</b></p>
            <form action="/finish-phone-login" method="post" class="flex flex-col gap-3">
                <input type="hidden" name="session_name" value="{session_name}">
                <div>
                    <label class="text-xs text-slate-400 block mb-1">Код подтверждения</label>
                    <input name="code" placeholder="12345" required autofocus class="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white outline-none font-mono">
                </div>
                <div>
                    <label class="text-xs text-slate-400 block mb-1">2FA пароль (если установлен)</label>
                    <input name="password" type="password" placeholder="Пароль 2FA" class="w-full bg-slate-950 border border-slate-800 rounded-lg p-2.5 text-xs text-white outline-none">
                </div>
                <button type="submit" class="mt-2 w-full bg-sky-600 hover:bg-sky-500 font-medium py-2.5 rounded-lg text-xs transition">Подтвердить</button>
            </form>
        </div>
    </body></html>
    """

@app.route("/finish-phone-login", methods=["POST"])
def finish_phone_login():
    session_name = request.form.get("session_name", "").strip()
    code = request.form.get("code", "").strip()
    pwd = request.form.get("password", "").strip()

    data = login_sessions.get(session_name)
    if not data:
        return "Сессия авторизации не найдена. Попробуйте снова с главной страницы. <br><a href='/'>Назад</a>", 400

    client = data["client"]
    try:
        run_tg(_do_finish(client, data["phone"], code, data["phone_code_hash"], pwd))
    except SessionPasswordNeededError:
        return "На аккаунте установлен 2FA пароль. Введите его в поле формы. <br><a href='javascript:history.back()'>Назад</a>", 400
    except Exception as e:
        return f"Ошибка при вводе кода: {e} <br><a href='javascript:history.back()'>Назад</a>", 400

    del login_sessions[session_name]

    for ext in [".session", ".session-journal"]:
        src = os.path.join(BASE_DIR, f"{session_name}{ext}")
        dst = os.path.join(DATA_DIR, f"{session_name}{ext}")
        if os.path.exists(src):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

    accs = load_accounts()
    accs = [a for a in accs if str(a.get("session")).strip() != session_name]
    accs.append({
        "session": session_name,
        "phone": data["phone"],
        "api_id": data["api_id"],
        "api_hash": data["api_hash"],
        "bind_ip": data["bind_ip"]
    })
    save_accounts(accs)
    return redirect("/")

@app.route("/trash/restore", methods=["POST"])
def restore_acc():
    sess_name = str(request.form.get("session_name", "")).strip()
    trash = load_trash()
    target = None
    new_trash = []
    for t in trash:
        if str(t.get("session")).strip() == sess_name:
            target = t
        else:
            new_trash.append(t)
    save_trash(new_trash)

    if target:
        accs = load_accounts()
        accs = [a for a in accs if str(a.get("session")).strip() != sess_name]
        target.pop("trash_reason", None)
        target.pop("deleted_at", None)
        accs.append(target)
        save_accounts(accs)

        for base in [BASE_DIR, DATA_DIR]:
            t_dir = os.path.join(base, "trash")
            for ext in [".session", ".session-journal"]:
                src = os.path.join(t_dir, f"{sess_name}{ext}")
                dst = os.path.join(base, f"{sess_name}{ext}")
                if os.path.exists(src):
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        pass

    return redirect("/?tab=trash")

@app.route("/trash/delete", methods=["POST"])
def delete_trash_permanent():
    sess_name = str(request.form.get("session_name", "")).strip()
    trash = load_trash()
    trash = [t for t in trash if str(t.get("session")).strip() != sess_name]
    save_trash(trash)

    for base in [BASE_DIR, DATA_DIR]:
        t_dir = os.path.join(base, "trash")
        for ext in [".session", ".session-journal"]:
            fpath = os.path.join(t_dir, f"{sess_name}{ext}")
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
    return redirect("/?tab=trash")

@app.route("/trash/clear", methods=["POST"])
def clear_all_trash():
    save_trash([])
    for base in [BASE_DIR, DATA_DIR]:
        t_dir = os.path.join(base, "trash")
        if os.path.exists(t_dir):
            try:
                shutil.rmtree(t_dir)
                os.makedirs(t_dir, exist_ok=True)
            except Exception:
                pass
    return redirect("/?tab=trash")

@app.route("/delete", methods=["POST"])
def delete_acc():
    sess_name = request.form.get("session_name")
    if sess_name:
        move_acc_to_trash(sess_name, "Удален вручную")
    return redirect("/")

@app.route("/node/add", methods=["POST"])
def add_node():
    settings = load_settings()
    name = request.form.get("node_name", "").strip()
    url = request.form.get("node_url", "").strip()
    user = request.form.get("node_user", "").strip() or "admin"
    pwd = request.form.get("node_pass", "").strip()

    if name and url:
        if not url.startswith("http://") and not url.startswith("https://"):
            url = f"http://{url}"
        nodes = settings.get("nodes", [])
        nodes.append({"name": name, "url": url, "user": user, "pass": pwd})
        settings["nodes"] = nodes
        save_settings(settings)

    return redirect("/?tab=settings")

@app.route("/node/delete", methods=["POST"])
def delete_node():
    settings = load_settings()
    idx = int(request.form.get("node_index", -1))
    nodes = settings.get("nodes", [])
    if 0 <= idx < len(nodes):
        nodes.pop(idx)
        settings["nodes"] = nodes
        save_settings(settings)
    return redirect("/?tab=settings")

@app.route("/settings/save", methods=["POST"])
def save_settings_route():
    settings = load_settings()
    settings["admin_user"] = request.form.get("admin_user", "").strip()
    settings["admin_pass"] = request.form.get("admin_pass", "").strip()
    settings["source_channel"] = request.form.get("source_channel", "").strip()
    settings["target_bot"] = request.form.get("target_bot", "").strip()
    settings["custom_domain"] = request.form.get("custom_domain", "").strip()
    save_settings(settings)
    return redirect("/?tab=settings")

@app.route("/service/restart", methods=["POST"])
def restart_service():
    subprocess.run(["systemctl", "restart", "farmbot"])
    return redirect("/")

@app.route("/service/restart-all", methods=["POST"])
def restart_all_services():
    subprocess.run(["systemctl", "restart", "farmbot"])
    settings = load_settings()
    nodes = settings.get("nodes", [])
    for node in nodes:
        threading.Thread(target=trigger_remote_restart, args=(node,), daemon=True).start()
    return redirect("/")

@app.route("/service/start", methods=["POST"])
def start_service():
    subprocess.run(["systemctl", "start", "farmbot"])
    return redirect("/")

@app.route("/api/logs")
def get_logs():
    try:
        out = subprocess.check_output(["journalctl", "-u", "farmbot", "-n", "80", "--no-pager"], text=True)
        return jsonify({"logs": out})
    except Exception as e:
        return jsonify({"logs": f"Ошибка чтения логов: {e}"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
