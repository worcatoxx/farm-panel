import json
import os
import secrets
import subprocess
import urllib.request
from fastapi import FastAPI, Request, Form, Cookie
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

CONFIG_FILE = "/root/farm_panel/accounts.json"
SETTINGS_FILE = "/root/farm_panel/config.json"
SESSION_TOKEN = secrets.token_hex(16)

app = FastAPI()
templates = Jinja2Templates(directory="/root/farm_panel/templates")

active_logins = {}

def get_server_ips():
    ips = []
    # 1. Внешний IP через ipify (для серверов за 1-to-1 NAT, как Oracle Cloud)
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=2) as resp:
            pub_ip = resp.read().decode("utf-8").strip()
            if pub_ip:
                ips.append(pub_ip)
    except Exception:
        pass

    # 2. Локальные интерфейсы
    try:
        out = subprocess.check_output(["ip", "-4", "-o", "addr", "show", "scope", "global"], text=True)
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 4:
                ip = parts[3].split("/")[0]
                if not ip.startswith("127.") and not ip.startswith("172.17.") and not ip.startswith("172.18."):
                    if ip not in ips:
                        ips.append(ip)
    except Exception:
        pass

    if not ips:
        ips = ["127.0.0.1"]
    return ips

def load_settings():
    default_settings = {
        "admin_user": "admin",
        "admin_pass": "superpass123",
        "source_channel": "vaycher_virtnumber",
        "target_bot": "Virtnumber_buy_bot"
    }
    if not os.path.exists(SETTINGS_FILE):
        return default_settings
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default_settings

def save_settings(data):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def is_auth(auth_token: str = Cookie(None)):
    return auth_token == SESSION_TOKEN

def load_accounts():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_accounts(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_service_status():
    res = subprocess.run(["systemctl", "is-active", "farmbot"], capture_output=True, text=True)
    return res.stdout.strip()

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    st = load_settings()
    if username == st["admin_user"] and password == st["admin_pass"]:
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(key="auth_token", value=SESSION_TOKEN, httponly=True)
        return resp
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Неверный логин или пароль"})

@app.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("auth_token")
    return resp

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, auth_token: str = Cookie(None)):
    if not is_auth(auth_token):
        return RedirectResponse("/login", status_code=303)
    accounts = load_accounts()
    service_status = get_service_status()
    settings = load_settings()

    server_ips = get_server_ips()
    primary_ip = server_ips[0]
    secondary_ip = server_ips[1] if len(server_ips) > 1 else None

    available_ips = [{"label": f"Основной IP ({primary_ip})", "val": ""}]
    if secondary_ip:
        available_ips.append({"label": f"Доп. IP ({secondary_ip})", "val": secondary_ip})

    primary_accs = [a for a in accounts if not a.get("bind_ip") or a.get("bind_ip") == primary_ip]
    secondary_accs = [a for a in accounts if secondary_ip and a.get("bind_ip") == secondary_ip]

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "accounts": accounts,
            "primary_accs": primary_accs,
            "secondary_accs": secondary_accs,
            "service_status": service_status,
            "settings": settings,
            "available_ips": available_ips,
            "primary_ip": primary_ip,
            "secondary_ip": secondary_ip
        }
    )

@app.post("/settings/save")
async def save_general_settings(
    admin_user: str = Form(...),
    admin_pass: str = Form(...),
    source_channel: str = Form(...),
    target_bot: str = Form(...),
    auth_token: str = Cookie(None)
):
    if not is_auth(auth_token):
        return RedirectResponse("/login", status_code=303)

    settings = {
        "admin_user": admin_user.strip(),
        "admin_pass": admin_pass.strip(),
        "source_channel": source_channel.strip().replace("@", ""),
        "target_bot": target_bot.strip().replace("@", "")
    }
    save_settings(settings)
    subprocess.run(["systemctl", "restart", "farmbot"])
    return RedirectResponse("/?tab=settings", status_code=303)

@app.post("/account/update-ip")
async def update_account_ip(
    session_name: str = Form(...),
    bind_ip: str = Form(""),
    auth_token: str = Cookie(None)
):
    if not is_auth(auth_token):
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    accounts = load_accounts()
    clean_ip = bind_ip.strip() if bind_ip.strip() else None
    for acc in accounts:
        if acc["session"] == session_name:
            acc["bind_ip"] = clean_ip
            break
    save_accounts(accounts)
    subprocess.run(["systemctl", "restart", "farmbot"])
    return JSONResponse({"status": "ok", "bind_ip": clean_ip})

@app.post("/start-phone-login")
async def start_phone_login(
    session_name: str = Form(...),
    phone: str = Form(...),
    api_id: int = Form(...),
    api_hash: str = Form(...),
    bind_ip: str = Form(""),
    auth_token: str = Cookie(None)
):
    if not is_auth(auth_token):
        return RedirectResponse("/login", status_code=303)

    clean_ip = bind_ip.strip() if bind_ip.strip() else None
    kwargs = {
        "session": f"/root/farm_panel/{session_name.strip()}",
        "api_id": api_id,
        "api_hash": api_hash.strip(),
        "auto_reconnect": True,
        "connection_retries": -1,
        "retry_delay": 1,
        "timeout": 15
    }
    if clean_ip:
        kwargs["local_addr"] = (clean_ip, 0)

    client = TelegramClient(**kwargs)
    await client.connect()

    try:
        sent_code = await client.send_code_request(phone.strip())
    except Exception as e:
        await client.disconnect()
        return HTMLResponse(f"<div style='color:red;padding:20px;background:#111'>Ошибка: {e} <br><a href='/' style='color:#38bdf8'>Вернуться</a></div>")

    active_logins[session_name] = {
        "client": client,
        "phone": phone.strip(),
        "phone_code_hash": sent_code.phone_code_hash,
        "api_id": api_id,
        "api_hash": api_hash.strip(),
        "bind_ip": clean_ip
    }

    return HTMLResponse(f"""
        <!DOCTYPE html>
        <html>
        <head><meta charset="utf-8"><title>Ввод кода</title><script src="https://cdn.tailwindcss.com"></script></head>
        <body class="bg-slate-950 text-slate-100 flex flex-col items-center justify-center min-h-screen">
            <div class="bg-slate-900 border border-slate-800 p-8 rounded-xl w-96 shadow-2xl">
                <h2 class="text-xl font-bold mb-2">Код из Telegram</h2>
                <p class="text-xs text-slate-400 mb-6">Код отправлен на {phone}</p>
                <form action="/finish-phone-login" method="post" class="flex flex-col gap-4">
                    <input type="hidden" name="session_name" value="{session_name}">
                    <div>
                        <label class="text-xs text-slate-400 block mb-1">Код подтверждения</label>
                        <input name="code" placeholder="12345" required class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-sm text-white focus:border-sky-500 outline-none font-mono">
                    </div>
                    <div>
                        <label class="text-xs text-slate-400 block mb-1">2FA пароль (если включен)</label>
                        <input name="password_2fa" type="password" placeholder="••••••••" class="w-full bg-slate-950 border border-slate-700 rounded-lg p-2.5 text-sm text-white focus:border-sky-500 outline-none">
                    </div>
                    <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-500 font-medium py-2.5 rounded-lg text-sm transition">Подтвердить и сохранить</button>
                    <a href="/" class="text-center text-xs text-slate-400 hover:text-slate-200 mt-2">Отмена</a>
                </form>
            </div>
        </body>
        </html>
    """)

@app.post("/finish-phone-login")
async def finish_phone_login(
    session_name: str = Form(...),
    code: str = Form(...),
    password_2fa: str = Form(None),
    auth_token: str = Cookie(None)
):
    if not is_auth(auth_token):
        return RedirectResponse("/login", status_code=303)

    login_data = active_logins.get(session_name)
    if not login_data:
        return RedirectResponse("/", status_code=303)

    client = login_data["client"]
    try:
        await client.sign_in(
            phone=login_data["phone"],
            code=code.strip(),
            phone_code_hash=login_data["phone_code_hash"]
        )
    except SessionPasswordNeededError:
        if password_2fa:
            await client.sign_in(password=password_2fa.strip())
        else:
            return HTMLResponse("<div style='color:red;padding:20px;background:#111'>Требуется 2FA пароль. <a href='/' style='color:#38bdf8'>Назад</a></div>")
    except Exception as e:
        await client.disconnect()
        return HTMLResponse(f"<div style='color:red;padding:20px;background:#111'>Ошибка: {e} <a href='/' style='color:#38bdf8'>Назад</a></div>")

    await client.disconnect()

    accounts = load_accounts()
    accounts.append({
        "session": session_name,
        "phone": login_data["phone"],
        "api_id": login_data["api_id"],
        "api_hash": login_data["api_hash"],
        "bind_ip": login_data["bind_ip"],
        "proxy": None
    })
    save_accounts(accounts)
    del active_logins[session_name]
    subprocess.run(["systemctl", "restart", "farmbot"])

    return RedirectResponse("/", status_code=303)

@app.post("/service/{action}")
async def control_service(action: str, auth_token: str = Cookie(None)):
    if not is_auth(auth_token):
        return RedirectResponse("/login", status_code=303)
    if action in ["start", "stop", "restart"]:
        subprocess.run(["systemctl", action, "farmbot"])
    return RedirectResponse("/", status_code=303)

@app.get("/api/logs")
async def get_logs(auth_token: str = Cookie(None)):
    if not is_auth(auth_token):
        return JSONResponse({"logs": "Unauthorized"}, status_code=401)
    res = subprocess.run(["journalctl", "-u", "farmbot", "-n", "80", "--no-pager"], capture_output=True, text=True)
    return JSONResponse({"logs": res.stdout})

@app.post("/delete")
async def delete_account(session_name: str = Form(...), auth_token: str = Cookie(None)):
    if not is_auth(auth_token):
        return RedirectResponse("/login", status_code=303)
    accounts = load_accounts()
    accounts = [acc for acc in accounts if acc["session"] != session_name]
    save_accounts(accounts)
    try:
        os.remove(f"/root/farm_panel/{session_name}.session")
    except OSError:
        pass
    subprocess.run(["systemctl", "restart", "farmbot"])
    return RedirectResponse("/", status_code=303)
