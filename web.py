import asyncio
import base64
import hashlib
import html
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from flask import (
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    session,
)
from telethon import TelegramClient
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    UserRestrictedError,
)
from telethon.tl.functions import PingRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest, SendReactionRequest
from telethon.tl.types import ReactionEmoji
from telethon.tl.types.auth import (
    SentCodeTypeApp,
    SentCodeTypeCall,
    SentCodeTypeFlashCall,
    SentCodeTypeSms,
)

from bot import (
    DEVICE_PROFILES,
    MOBILE_EVENTS_FILE,
    WARMUP_TRIGGER_FILE,
    _atomic_write_json,
    extract_voucher_from_image,
    extract_voucher_from_message,
    get_device_fingerprint,
    is_fatal_account_error,
    parse_balance_from_text,
    parse_bot_activation_response,
    parse_warmup_channels,
    push_mobile_event,
    solve_math_equation,
)

BASE_DIR = "/root/farm_panel" if os.name != "nt" else os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "/root/farm_data" if os.name != "nt" else os.path.join(BASE_DIR, "farm_data")
os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(DATA_DIR, "accounts.json")
MIRROR_CONFIG_FILE = os.path.join(BASE_DIR, "accounts.json")

SETTINGS_FILE = os.path.join(DATA_DIR, "config.json")
MIRROR_SETTINGS_FILE = os.path.join(BASE_DIR, "config.json")

TRASH_FILE = os.path.join(DATA_DIR, "trash.json")
MIRROR_TRASH_FILE = os.path.join(BASE_DIR, "trash.json")

VOUCHERS_HISTORY_FILE = os.path.join(DATA_DIR, "vouchers_history.json")

TRASH_DIR = os.path.join(DATA_DIR, "trash")
MIRROR_TRASH_DIR = os.path.join(BASE_DIR, "trash")
TMP_SESSION_DIR = os.path.join(DATA_DIR, "tmp_web_sessions")
os.makedirs(TRASH_DIR, exist_ok=True)
os.makedirs(MIRROR_TRASH_DIR, exist_ok=True)
os.makedirs(TMP_SESSION_DIR, exist_ok=True)

tg_loop = asyncio.new_event_loop()


def _start_tg_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=_start_tg_loop, args=(tg_loop,), daemon=True).start()


def run_tg(coro, timeout=180):
    future = asyncio.run_coroutine_threadsafe(coro, tg_loop)
    return future.result(timeout=timeout)


def _load_or_create_secret_key() -> bytes:
    sec_file = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(sec_file):
        try:
            with open(sec_file, "rb") as f:
                key = f.read()
                if len(key) >= 24:
                    return key
        except Exception:
            pass
    key = os.urandom(32)
    try:
        with open(sec_file, "wb") as f:
            f.write(key)
    except Exception:
        pass
    return key


app = Flask(
    __name__,
    template_folder=os.path.join(BASE_DIR, "templates"),
    static_folder=os.path.join(BASE_DIR, "static"),
)
app.secret_key = _load_or_create_secret_key()

login_sessions = {}


def sanitize_session_name(name: str) -> str:
    clean = re.sub(r"[^a-zA-Z0-9_\-\u0400-\u04FF]", "", str(name or "").strip())
    return clean[:48]


def resolve_session_file(session_name: str) -> str:
    s_clean = sanitize_session_name(session_name)
    data_path = os.path.join(DATA_DIR, s_clean)
    base_path = os.path.join(BASE_DIR, s_clean)

    if os.path.exists(f"{data_path}.session"):
        return data_path
    if os.path.exists(f"{base_path}.session"):
        try:
            shutil.copy2(f"{base_path}.session", f"{data_path}.session")
            return data_path
        except Exception:
            return base_path
    return data_path


def create_temp_web_session(session_name: str) -> tuple[str | None, str]:
    s_clean = sanitize_session_name(session_name)
    src_base = resolve_session_file(s_clean)
    src_file = f"{src_base}.session"
    if not os.path.exists(src_file):
        return None, "Файл .session не найден"

    uid = uuid.uuid4().hex[:8]
    tmp_base = os.path.join(TMP_SESSION_DIR, f"{s_clean}_{uid}")
    try:
        shutil.copy2(src_file, f"{tmp_base}.session")
        return tmp_base, ""
    except Exception as e:
        return src_base, str(e)


def cleanup_temp_web_session(tmp_base: str | None):
    if not tmp_base or TMP_SESSION_DIR not in os.path.abspath(tmp_base):
        return
    for ext in [".session", ".session-journal", ".session-wal", ".session-shm"]:
        p = f"{tmp_base}{ext}"
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass


def build_client_kwargs(acc: dict) -> dict:
    s_name = sanitize_session_name(acc.get("session", ""))
    dev = get_device_fingerprint(s_name, acc)
    kwargs = {
        "api_id": int(acc.get("api_id", 2040)),
        "api_hash": str(acc.get("api_hash", "b18441a1ff607e10a989891a5462e627")),
        "loop": tg_loop,
        "device_model": dev["device_model"],
        "system_version": dev["system_version"],
        "app_version": dev["app_version"],
        "lang_code": "ru",
        "system_lang_code": "ru-RU",
        "timeout": 7,
        "connection_retries": 1,
    }
    bind_ip = str(acc.get("bind_ip", "") or "").strip()
    if bind_ip:
        if ":" in bind_ip:
            kwargs["use_ipv6"] = True
        kwargs["local_addr"] = (bind_ip, 0)
    return kwargs


def load_settings() -> dict:
    default_settings = {
        "admin_user": "admin",
        "admin_pass": "superpass123",
        "source_channel": "vaycher_virtnumber",
        "target_bot": "Virtnumber_buy_bot",
        "custom_domain": "",
        "site_withdraw_login": "",
        "min_withdraw_amount": 30.0,
        "auto_withdraw_enabled": True,
        "total_withdrawn_rub": 0.0,
        "total_withdrawn_count": 0,
        "enable_smart_jitter": False,
        "jitter_min_ms": 20,
        "jitter_max_ms": 80,
        "enable_stealth_warmup": True,
        "warmup_channels": "telegram, durov",
        "last_warmup_at": "",
        "nodes": [],
    }
    for path in [SETTINGS_FILE, MIRROR_SETTINGS_FILE]:
        if os.path.exists(path) and os.path.getsize(path) > 5:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in default_settings.items():
                            data.setdefault(k, v)
                        return data
            except Exception:
                pass
    return default_settings


def save_settings(settings: dict):
    for path in [SETTINGS_FILE, MIRROR_SETTINGS_FILE]:
        try:
            _atomic_write_json(path, settings)
        except Exception:
            pass



def load_accounts() -> list:
    for path in [CONFIG_FILE, MIRROR_CONFIG_FILE]:
        if os.path.exists(path) and os.path.getsize(path) >= 2:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        for a in content:
                            s_name = a.get("session", "")
                            if s_name and not a.get("device_model"):
                                dev = get_device_fingerprint(s_name, a)
                                a["device_model"] = dev["device_model"]
                                a["system_version"] = dev["system_version"]
                                a["app_version"] = dev["app_version"]
                        return content
            except Exception:
                pass
    return []


def save_accounts(accounts: list):
    for path in [CONFIG_FILE, MIRROR_CONFIG_FILE]:
        try:
            _atomic_write_json(path, accounts)
        except Exception:
            pass


def load_trash() -> list:
    for path in [TRASH_FILE, MIRROR_TRASH_FILE]:
        if os.path.exists(path) and os.path.getsize(path) >= 2:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        return content
            except Exception:
                pass
    return []


def save_trash(trash: list):
    for path in [TRASH_FILE, MIRROR_TRASH_FILE]:
        try:
            _atomic_write_json(path, trash)
        except Exception:
            pass


def load_vouchers_history() -> list:
    if os.path.exists(VOUCHERS_HISTORY_FILE):
        try:
            with open(VOUCHERS_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def load_mobile_events() -> list:
    if os.path.exists(MOBILE_EVENTS_FILE):
        try:
            with open(MOBILE_EVENTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def move_acc_to_trash(session_name: str, reason: str = "Удален вручную"):
    s_target = sanitize_session_name(session_name)
    if not s_target:
        return
    accs = load_accounts()
    target = None
    new_accs = []
    for a in accs:
        if sanitize_session_name(a.get("session", "")) == s_target:
            target = a
        else:
            new_accs.append(a)
    save_accounts(new_accs)

    if not target:
        target = {"session": s_target, "phone": "-"}

    target["trash_reason"] = reason
    target["deleted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    trash = load_trash()
    trash = [t for t in trash if sanitize_session_name(t.get("session", "")) != s_target]
    trash.append(target)
    save_trash(trash)

    for base in [DATA_DIR, BASE_DIR]:
        t_dir = os.path.join(base, "trash")
        os.makedirs(t_dir, exist_ok=True)
        for ext in [".session", ".session-journal", ".session-wal", ".session-shm"]:
            src = os.path.join(base, f"{s_target}{ext}")
            dst = os.path.join(t_dir, f"{s_target}{ext}")
            if os.path.exists(src):
                try:
                    shutil.move(src, dst)
                except Exception:
                    pass


def get_server_ips() -> list[str]:
    primary_ip = None
    try:
        req = urllib.request.Request("https://api.ipify.org", headers={"User-Agent": "curl/7.68.0"})
        with urllib.request.urlopen(req, timeout=2.0) as r:
            primary_ip = r.read().decode("utf-8").strip()
    except Exception:
        pass

    all_ips = []
    if os.name != "nt":
        try:
            out = subprocess.check_output(["ip", "-4", "addr", "show", "scope", "global"], text=True, timeout=2)
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("inet "):
                    ip = line.split()[1].split("/")[0]
                    if not ip.startswith(("172.17.", "172.18.", "172.19.", "127.", "169.254.", "10.", "192.168.")):
                        if ip not in all_ips:
                            all_ips.append(ip)
        except Exception:
            pass

    if not primary_ip and all_ips:
        primary_ip = all_ips[0]
    elif not primary_ip:
        primary_ip = "127.0.0.1"

    if primary_ip in all_ips:
        all_ips.remove(primary_ip)
    all_ips.insert(0, primary_ip)
    return all_ips


def get_service_status() -> str:
    if os.name == "nt":
        return "active"
    try:
        res = subprocess.run(["systemctl", "is-active", "farmbot"], capture_output=True, text=True, timeout=2)
        st = res.stdout.strip()
        return st if st else "inactive"
    except Exception:
        return "unknown"


def send_remote_request(node: dict, endpoint: str):
    url = f"{node['url'].rstrip('/')}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, data=b"", method="POST")
    if node.get("user") and node.get("pass"):
        auth = base64.b64encode(f"{node['user']}:{node['pass']}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=6):
            pass
    except Exception:
        pass


# --- БЛОК 1.2: Watchdog мониторинг доступности нод (с замером пинга в мс) ---
def fetch_remote_node_stats(node: dict) -> dict:
    url = f"{node['url'].rstrip('/')}/api/node-stats"
    req = urllib.request.Request(url, headers={"User-Agent": "FarmBot-Master-Watchdog/3.5"})
    if node.get("user") and node.get("pass"):
        auth = base64.b64encode(f"{node['user']}:{node['pass']}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            ping_ms = max(1, int((time.perf_counter() - t0) * 1000))
            data["online"] = True
            data["ping_ms"] = ping_ms
            data["status_text"] = f"Online ({ping_ms} ms)"
            return data
    except Exception:
        return {
            "balance": 0.0,
            "accounts_count": 0,
            "online": False,
            "ping_ms": None,
            "status_text": "Offline (Таймаут)",
            "service_status": "offline",
        }


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

    allowed = ["/login", "/static", "/manifest.json", "/sw.js", "/pwa-icon.svg"]
    if any(request.path.startswith(p) for p in allowed):
        return
    if not session.get("logged_in"):
        return redirect("/login")


# --- БЛОК 1.1: Режим PWA (Установка как мобильное приложение на iOS / Android) ---
@app.route("/pwa-icon.svg")
def pwa_icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
    <defs>
        <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#1e1b4b"/>
            <stop offset="50%" stop-color="#09090b"/>
            <stop offset="100%" stop-color="#0f172a"/>
        </linearGradient>
        <linearGradient id="bolt" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#a78bfa"/>
            <stop offset="50%" stop-color="#7c3aed"/>
            <stop offset="100%" stop-color="#4f46e5"/>
        </linearGradient>
    </defs>
    <rect width="512" height="512" rx="115" fill="url(#bg)"/>
    <rect x="16" y="16" width="480" height="480" rx="100" fill="none" stroke="#7c3aed" stroke-opacity="0.35" stroke-width="8"/>
    <path d="M292 76L124 284h120l-24 152 168-208H268L292 76z" fill="url(#bolt)"/>
</svg>"""
    return Response(svg, mimetype="image/svg+xml", headers={"Cache-Control": "public, max-age=86400"})


@app.route("/manifest.json")
def pwa_manifest():
    manifest = {
        "name": "FarmBot Sniper PRO",
        "short_name": "FarmBot",
        "description": "Автоматизированная панель управления фермой перехвата ваучеров Telegram",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "portrait-primary",
        "background_color": "#09090b",
        "theme_color": "#09090b",
        "icons": [
            {
                "src": "/pwa-icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any maskable",
            },
            {
                "src": "/pwa-icon.svg",
                "sizes": "192x192",
                "type": "image/svg+xml",
            },
            {
                "src": "/pwa-icon.svg",
                "sizes": "512x512",
                "type": "image/svg+xml",
            },
        ],
    }
    return Response(json.dumps(manifest, ensure_ascii=False), mimetype="application/manifest+json")


@app.route("/sw.js")
def pwa_service_worker():
    sw_code = """const CACHE_NAME = 'farmbot-pwa-v3.7';
self.addEventListener('install', (event) => {
    self.skipWaiting();
});
self.addEventListener('activate', (event) => {
    event.waitUntil(clients.claim());
});
self.addEventListener('fetch', (event) => {
    if (event.request.method !== 'GET') return;
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windowClients) => {
            for (const client of windowClients) {
                if ('focus' in client) return client.focus();
            }
            if (clients.openWindow) return clients.openWindow('/');
        })
    );
});
"""
    return Response(sw_code, mimetype="application/javascript", headers={"Service-Worker-Allowed": "/"})


@app.route("/api/mobile-events")
def api_mobile_events():
    events_list = load_mobile_events()
    vouchers_history = load_vouchers_history()
    accounts = load_accounts()
    settings = load_settings()
    local_bal = 0.0
    for a in accounts:
        try:
            local_bal += float(str(a.get("balance", "0")).replace(",", "."))
        except ValueError:
            pass

    activated_total = sum(int(v.get("activated_count", 0)) for v in vouchers_history)
    earned_total = sum(float(v.get("earned_rub", 0.0)) for v in vouchers_history)

    return jsonify({
        "events": events_list[:25],
        "latest_id": events_list[0]["id"] if events_list else 0,
        "local_balance": f"{local_bal:.2f}",
        "vouchers_total": len(vouchers_history),
        "activated_total": activated_total,
        "earned_total": f"{earned_total:.2f}",
        "auto_withdraw_enabled": bool(settings.get("auto_withdraw_enabled", True)),
        "min_withdraw": float(settings.get("min_withdraw_amount", 30.0)),
        "service_status": get_service_status(),
    })


@app.route("/api/mobile-events/test", methods=["POST"])
def api_mobile_events_test():
    ev = push_mobile_event(
        "voucher_hit",
        "🎯 Тест Push: Ваучер +50.00 ₽!",
        "Мобильное приложение FarmBot Sniper PRO v3.7 подключено и готово к уведомлениям.",
        badge="+50₽",
    )
    return jsonify({"ok": True, "event": ev})


@app.route("/login", methods=["GET", "POST"])
def login():
    settings = load_settings()
    error = None
    if request.method == "POST":
        user = request.form.get("username", "").strip()
        pwd = request.form.get("password", "").strip()
        if user == settings["admin_user"] and pwd == settings["admin_pass"]:
            session["logged_in"] = True
            return redirect("/")
        error = "Неверный логин или пароль администратора"

    login_template_path = os.path.join(BASE_DIR, "templates", "login.html")
    if os.path.exists(login_template_path):
        return render_template("login.html", error=error)
    return "Файл templates/login.html отсутствует", 500


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/api/node-stats")
def api_node_stats():
    accounts = load_accounts()
    b_sum = 0.0
    for a in accounts:
        try:
            b_sum += float(str(a.get("balance", "0")).replace(",", "."))
        except ValueError:
            pass
    return jsonify({
        "balance": round(b_sum, 2),
        "accounts_count": len(accounts),
        "online": True,
        "service_status": get_service_status(),
    })


@app.route("/api/nodes-watchdog")
def api_nodes_watchdog():
    settings = load_settings()
    nodes = settings.get("nodes", [])
    results = []
    for idx, n in enumerate(nodes):
        st = fetch_remote_node_stats(n)
        b_val = float(st.get("balance", 0.0))
        results.append({
            "index": idx,
            "name": n.get("name", f"VPS #{idx + 1}"),
            "url": n.get("url", ""),
            "online": bool(st.get("online", False)),
            "ping_ms": st.get("ping_ms"),
            "status_text": st.get("status_text", "Offline (Таймаут)"),
            "balance": f"{b_val:.2f}",
            "accounts_count": int(st.get("accounts_count", 0)),
            "service_status": st.get("service_status", "unknown"),
        })
    return jsonify({"nodes": results, "checked_at": datetime.now().strftime("%H:%M:%S")})


@app.route("/proxy/<int:node_idx>/", defaults={"subpath": ""}, methods=["GET", "POST"])
@app.route("/proxy/<int:node_idx>/<path:subpath>", methods=["GET", "POST"])
def master_proxy_node(node_idx, subpath):
    settings = load_settings()
    nodes = settings.get("nodes", [])
    if node_idx < 0 or node_idx >= len(nodes):
        return "Нода не найдена", 404

    node = nodes[node_idx]
    target_url = f"{node['url'].rstrip('/')}/{subpath}"
    if request.query_string:
        target_url += f"?{request.query_string.decode('utf-8')}"

    auth_str = base64.b64encode(f"{node.get('user', 'admin')}:{node.get('pass', '')}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_str}",
        "User-Agent": request.headers.get("User-Agent", "Mozilla/5.0"),
    }
    if request.content_type:
        headers["Content-Type"] = request.content_type

    data = request.get_data() if request.method == "POST" else None
    req = urllib.request.Request(target_url, data=data, headers=headers, method=request.method)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read()
            content_type = resp.headers.get("Content-Type", "text/html")

            if "text/html" in content_type:
                html_text = content.decode("utf-8", errors="ignore")
                base_tag = f'<base href="/proxy/{node_idx}/">'
                if "<head>" in html_text:
                    html_text = html_text.replace("<head>", f"<head>{base_tag}", 1)
                content = html_text.encode("utf-8")

            return Response(content, status=resp.status, content_type=content_type)
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type=e.headers.get("Content-Type", "text/plain"))
    except Exception as e:
        err_escaped = html.escape(str(e))
        return (
            f"<div style='color:#f4f4f5;background:#09090b;padding:24px;font-family:sans-serif;'>"
            f"<h3 style='color:#f43f5e;'>[!] Ошибка соединения с нодой {html.escape(node.get('name', ''))}:</h3>"
            f"<p style='color:#a1a1aa;'>{err_escaped}</p></div>",
            502,
        )


@app.route("/")
def index():
    settings = load_settings()
    server_ips = get_server_ips()
    primary_ip = server_ips[0] if server_ips else "127.0.0.1"
    accounts = load_accounts()
    trash_accs = load_trash()
    vouchers_history = load_vouchers_history()
    mobile_events = load_mobile_events()

    min_withdraw = float(settings.get("min_withdraw_amount", 30.0))
    local_balance = 0.0
    ready_to_withdraw_count = 0
    ready_to_withdraw_sum = 0.0

    for a in accounts:
        try:
            val = float(str(a.get("balance", "0")).replace(",", "."))
        except ValueError:
            val = 0.0
        a["balance_float"] = val
        local_balance += val
        if val >= min_withdraw:
            ready_to_withdraw_count += 1
            ready_to_withdraw_sum += val

    vouchers_activated_count = sum(int(v.get("activated_count", 0)) for v in vouchers_history)
    vouchers_earned_rub = sum(float(v.get("earned_rub", 0.0)) for v in vouchers_history)

    nodes = settings.get("nodes", [])
    nodes_stats = []
    network_nodes_balance = 0.0
    network_nodes_accs = 0

    for idx, n in enumerate(nodes):
        st = fetch_remote_node_stats(n)
        b_val = float(st.get("balance", 0.0))
        network_nodes_balance += b_val
        network_nodes_accs += int(st.get("accounts_count", 0))
        nodes_stats.append({
            "index": idx,
            "name": n["name"],
            "url": n["url"],
            "balance": f"{b_val:.2f}",
            "accounts_count": st.get("accounts_count", 0),
            "online": st.get("online", False),
            "ping_ms": st.get("ping_ms"),
            "status_text": st.get("status_text", "Offline (Таймаут)"),
        })

    total_farm_balance = local_balance + network_nodes_balance
    total_farm_accounts = len(accounts) + network_nodes_accs

    ip_groups = []
    assigned_ips = set(server_ips)
    for idx, ip in enumerate(server_ips):
        if idx == 0:
            accs = [
                a for a in accounts
                if not a.get("bind_ip") or a.get("bind_ip") == ip or a.get("bind_ip") not in assigned_ips
            ]
            title = f"ОСНОВНОЙ IP: {ip}"
            color = "amber"
        else:
            accs = [a for a in accounts if a.get("bind_ip") == ip]
            title = f"ДОПОЛНИТЕЛЬНЫЙ IP: {ip}"
            color = "blue"

        group_bal = sum(a.get("balance_float", 0.0) for a in accs)
        ip_groups.append({
            "ip": ip,
            "title": title,
            "color": color,
            "accounts": accs,
            "group_balance": f"{group_bal:.2f}",
        })

    available_ips = [{"val": "", "label": f"Основной IP ({primary_ip})"}]
    for ip in server_ips[1:]:
        available_ips.append({"val": ip, "label": f"Дополнительный IP ({ip})"})

    current_display_host = settings.get("custom_domain") if settings.get("custom_domain") else primary_ip

    return render_template(
        "index.html",
        service_status=get_service_status(),
        current_display_host=current_display_host,
        ip_groups=ip_groups,
        available_ips=available_ips,
        settings=settings,
        linked_nodes=nodes,
        nodes_stats=nodes_stats,
        trash_accs=trash_accs,
        vouchers_history=vouchers_history[:30],
        vouchers_total_count=len(vouchers_history),
        vouchers_activated_count=vouchers_activated_count,
        vouchers_earned_rub=f"{vouchers_earned_rub:.2f}",
        mobile_events=mobile_events[:15],
        device_profiles_count=len(DEVICE_PROFILES),
        local_balance=f"{local_balance:.2f}",
        total_farm_balance=f"{total_farm_balance:.2f}",
        total_accounts_count=len(accounts),
        total_farm_accounts=total_farm_accounts,
        ready_to_withdraw_count=ready_to_withdraw_count,
        ready_to_withdraw_sum=f"{ready_to_withdraw_sum:.2f}",
        min_withdraw=min_withdraw,
    )


# --- Speed Benchmark (Замер пинга до Telegram DC) ---
async def _measure_acc_ping(acc: dict, sem: asyncio.Semaphore) -> tuple[str, str]:
    s_name = acc["session"]
    async with sem:
        tmp_session, _ = create_temp_web_session(s_name)
        if not tmp_session:
            return s_name, "no-file"
        kwargs = build_client_kwargs(acc)
        kwargs["timeout"] = 5
        client = TelegramClient(tmp_session, **kwargs)
        ping_res = acc.get("ping", "-")
        try:
            t0 = time.perf_counter()
            await client.connect()
            if await client.is_user_authorized():
                await client(PingRequest(ping_id=random.randint(1000, 999999)))
                elapsed = (time.perf_counter() - t0) * 1000
                ping_res = f"{int(elapsed)} ms"
            else:
                ping_res = "unauth"
        except Exception:
            ping_res = "err"
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            cleanup_temp_web_session(tmp_session)
        return s_name, ping_res


async def _benchmark_all(target_session: str | None = None):
    accs = load_accounts()
    sem = asyncio.Semaphore(5)
    targets = [a for a in accs if not target_session or a.get("session") == target_session]
    tasks = [_measure_acc_ping(a, sem) for a in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    accs_map = {a["session"]: a for a in accs}
    for res in results:
        if isinstance(res, tuple):
            s_name, p_val = res
            if s_name in accs_map:
                accs_map[s_name]["ping"] = p_val
    save_accounts(list(accs_map.values()))


def _is_ajax_request() -> bool:
    return (
        request.headers.get("X-Requested-With") == "fetch"
        or "application/json" in request.headers.get("Accept", "")
    )


def _build_dashboard_ajax_summary(message: str = "Готово", ok: bool = True, extra: dict | None = None) -> dict:
    settings = load_settings()
    accs = load_accounts()
    min_w = float(settings.get("min_withdraw_amount", 30.0))
    local_bal = 0.0
    ready_cnt = 0
    ready_sum = 0.0
    accs_payload = {}
    for a in accs:
        try:
            bf = float(str(a.get("balance", "0.00")).replace(",", "."))
        except ValueError:
            bf = 0.0
        local_bal += bf
        if bf >= min_w:
            ready_cnt += 1
            ready_sum += bf
        s_name = a.get("session", "")
        if s_name:
            accs_payload[s_name] = {
                "balance": f"{bf:.2f}",
                "ping": a.get("ping", "-"),
                "device_model": a.get("device_model", ""),
                "system_version": a.get("system_version", ""),
                "app_version": a.get("app_version", ""),
                "last_checked": a.get("last_checked", ""),
                "last_withdrawn_at": a.get("last_withdrawn_at", ""),
                "ready_to_withdraw": bf >= min_w,
            }
    data = {
        "ok": ok,
        "message": message,
        "service_status": get_service_status(),
        "local_balance": f"{local_bal:.2f}",
        "total_accounts_count": len(accs),
        "ready_to_withdraw_count": ready_cnt,
        "ready_to_withdraw_sum": f"{ready_sum:.2f}",
        "min_withdraw": min_w,
        "auto_withdraw_enabled": bool(settings.get("auto_withdraw_enabled", True)),
        "site_withdraw_login": settings.get("site_withdraw_login", ""),
        "total_withdrawn_rub": f"{float(settings.get('total_withdrawn_rub', 0.0)):.2f}",
        "total_withdrawn_count": int(settings.get("total_withdrawn_count", 0)),
        "last_warmup_at": settings.get("last_warmup_at", ""),
        "trash_count": len(load_trash()),
        "accounts": accs_payload,
    }
    if extra:
        data.update(extra)
    return data


@app.route("/accounts/benchmark", methods=["POST"])
def benchmark_route():
    target_session = request.form.get("session_name", "").strip() or None
    run_tg(_benchmark_all(target_session))
    if _is_ajax_request():
        msg = f"Пинг DC обновлен ({target_session})" if target_session else "Пинг до Telegram DC замерен"
        return jsonify(_build_dashboard_ajax_summary(msg, ok=True))
    return redirect("/")


# --- БЛОК 2.1: Ручной и фоновый запуск «Стелс-прогрева» ---
async def _direct_stealth_warmup(target_session: str | None = None):
    settings = load_settings()
    channels = parse_warmup_channels(settings.get("warmup_channels", "telegram, durov"))
    accs = load_accounts()
    targets = [a for a in accs if not target_session or a.get("session") == target_session]
    sem = asyncio.Semaphore(4)

    async def _warm_one(acc: dict):
        s_name = acc["session"]
        async with sem:
            tmp_session, _ = create_temp_web_session(s_name)
            if not tmp_session:
                return
            kwargs = build_client_kwargs(acc)
            client = TelegramClient(tmp_session, **kwargs)
            target_ch = random.choice(channels)
            try:
                await client.connect()
                if not await client.is_user_authorized():
                    return
                entity = await client.get_input_entity(target_ch)
                msgs = await client.get_messages(entity, limit=5)
                valid_msgs = [m for m in msgs if m and getattr(m, "id", None)]
                if not valid_msgs:
                    return
                await client.send_read_acknowledge(entity, max_id=valid_msgs[0].id)
                try:
                    await client(GetMessagesViewsRequest(peer=entity, id=[m.id for m in valid_msgs[:3]], increment=True))
                except Exception:
                    pass
                if random.random() < 0.55:
                    chosen_msg = random.choice(valid_msgs[:3])
                    emoji = random.choice(["👍", "🔥", "❤️"])
                    try:
                        await client(
                            SendReactionRequest(
                                peer=entity,
                                msg_id=chosen_msg.id,
                                reaction=[ReactionEmoji(emoticon=emoji)],
                            )
                        )
                    except Exception:
                        pass
            except Exception as e:
                print(f"[WARN] [СТЕЛС-ПРОГРЕВ] {s_name}: {e}", flush=True)
            finally:
                try:
                    await client.disconnect()
                except Exception:
                    pass
                cleanup_temp_web_session(tmp_session)

    await asyncio.gather(*[_warm_one(a) for a in targets], return_exceptions=True)
    settings["last_warmup_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_settings(settings)


@app.route("/accounts/stealth-warmup", methods=["POST"])
def stealth_warmup_route():
    target_session = request.form.get("session_name", "").strip() or None
    if not target_session and get_service_status() == "active":
        # Делегируем фоновому процессу farmbot через сигнальный файл (без блокировки веб-панели)
        try:
            with open(WARMUP_TRIGGER_FILE, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        settings = load_settings()
        settings["last_warmup_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        save_settings(settings)
    else:
        run_tg(_direct_stealth_warmup(target_session), timeout=90)
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary("Стелс-прогрев запущен (чтение лент и реакции)", ok=True))
    return redirect("/")


# --- Загрузка готовых .session файлов ---
async def _verify_uploaded_session(session_name: str, api_id: int, api_hash: str, bind_ip: str) -> tuple[bool, str, str]:
    dev_info = get_device_fingerprint(session_name)
    acc_stub = {
        "session": session_name,
        "api_id": api_id,
        "api_hash": api_hash,
        "bind_ip": bind_ip,
        **dev_info,
    }
    tmp_session, _ = create_temp_web_session(session_name)
    if not tmp_session:
        return False, "", "Файл не сохранен"
    kwargs = build_client_kwargs(acc_stub)
    client = TelegramClient(tmp_session, **kwargs)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return False, "-", "Сессия не авторизована"
        me = await client.get_me()
        phone = f"+{me.phone}" if getattr(me, "phone", None) else "-"
        return True, phone, ""
    except Exception as e:
        return False, "-", str(e)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass
        cleanup_temp_web_session(tmp_session)


@app.route("/upload-session", methods=["POST"])
def upload_session_route():
    files = request.files.getlist("session_files")
    bind_ip = request.form.get("bind_ip", "").strip()
    api_id_str = request.form.get("api_id", "2040").strip()
    api_id = int(api_id_str) if api_id_str.isdigit() else 2040
    api_hash = request.form.get("api_hash", "b18441a1ff607e10a989891a5462e627").strip() or "b18441a1ff607e10a989891a5462e627"

    accs = load_accounts()
    accs_map = {sanitize_session_name(a.get("session", "")): a for a in accs}

    for f in files:
        if not f or not f.filename or not f.filename.endswith(".session"):
            continue
        raw_name = os.path.splitext(os.path.basename(f.filename))[0]
        s_name = sanitize_session_name(raw_name)
        if not s_name:
            continue

        data_target = os.path.join(DATA_DIR, f"{s_name}.session")
        base_target = os.path.join(BASE_DIR, f"{s_name}.session")
        f.save(data_target)
        try:
            shutil.copy2(data_target, base_target)
        except Exception:
            pass

        ok, phone, _ = run_tg(_verify_uploaded_session(s_name, api_id, api_hash, bind_ip), timeout=25)
        if ok:
            dev_info = get_device_fingerprint(s_name)
            accs_map[s_name] = {
                "session": s_name,
                "phone": phone,
                "api_id": api_id,
                "api_hash": api_hash,
                "bind_ip": bind_ip,
                "device_model": dev_info["device_model"],
                "system_version": dev_info["system_version"],
                "app_version": dev_info["app_version"],
                "balance": "0.00",
                "ping": "-",
                "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
        else:
            for p in [data_target, base_target]:
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    save_accounts(list(accs_map.values()))
    return redirect("/")


# --- Логика Telegram Login ---
def _render_code_verification_page(session_name: str, phone: str, delivery: str, error_msg: str = ""):
    err_block = ""
    if error_msg:
        err_block = f"""
        <div class="bg-rose-500/10 border border-rose-500/30 text-rose-400 px-4 py-3 rounded-xl text-xs mb-4 flex items-center gap-2.5">
            <svg class="w-4 h-4 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/></svg>
            <span>{html.escape(error_msg)}</span>
        </div>
        """
    return f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Подтверждение Telegram — FarmBot</title>
        <link rel="manifest" href="/manifest.json">
        <link rel="icon" type="image/svg+xml" href="/pwa-icon.svg">
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            :root {{
                --bg-main: #09090b;
                --bg-card: #111115;
                --text-main: #f4f4f5;
                --text-muted: #a1a1aa;
                --border: #27272a;
                --accent: #7c3aed;
            }}
            [data-theme="amoled-blue"] {{ --bg-main: #000000; --bg-card: #090A0F; --text-main: #FFFFFF; --text-muted: #7E8B9B; --border: #1A2035; --accent: #2563EB; }}
            [data-theme="dark-blue"] {{ --bg-main: #0B0F17; --bg-card: #131B2E; --text-main: #F1F5F9; --text-muted: #94A3B8; --border: #233252; --accent: #38BDF8; }}
            [data-theme="full-blue"] {{ --bg-main: #04152D; --bg-card: #072247; --text-main: #E2F1FF; --text-muted: #7FA6CC; --border: #123E7A; --accent: #00F2FE; }}
            [data-theme="amoled-emerald"] {{ --bg-main: #000000; --bg-card: #070D0A; --text-main: #ECFDF5; --text-muted: #6EE7B7; --border: #143325; --accent: #10B981; }}
            [data-theme="sunset-dark"] {{ --bg-main: #0F0A0C; --bg-card: #1A1115; --text-main: #FFF5F5; --text-muted: #A3858E; --border: #331F28; --accent: #F97316; }}
            [data-theme="light-tech"] {{ --bg-main: #F8FAFC; --bg-card: #FFFFFF; --text-main: #0F172A; --text-muted: #64748B; --border: #E2E8F0; --accent: #2563EB; }}
            [data-theme="warm-craft"] {{ --bg-main: #EAE6DF; --bg-card: #F4F1EA; --text-main: #1C1B18; --text-muted: #78736A; --border: #D6D0C4; --accent: #FF4F00; }}
            body {{ background-color: var(--bg-main); color: var(--text-main); font-family: 'Inter', sans-serif; }}
            .font-mono {{ font-family: 'JetBrains Mono', monospace; }}
        </style>
        <script>
            (function() {{
                const t = localStorage.getItem('farm_theme') || 'default-violet';
                if (t !== 'default-violet') document.documentElement.setAttribute('data-theme', t);
            }})();
        </script>
    </head>
    <body class="flex items-center justify-center min-h-screen p-4">
        <div style="background: var(--bg-card); border: 1px solid var(--border);" class="p-6 sm:p-8 rounded-2xl max-w-md w-full shadow-2xl">
            <div class="flex items-center gap-3.5 mb-5">
                <div style="background: var(--accent);" class="w-11 h-11 rounded-xl flex items-center justify-center text-white">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>
                </div>
                <div>
                    <h2 class="text-base font-bold">Подтверждение входа</h2>
                    <p style="color: var(--text-muted);" class="text-xs">Сессия: <span class="font-mono font-semibold" style="color: var(--text-main);">{html.escape(session_name)}</span></p>
                </div>
            </div>
            <div style="background: var(--bg-main); border: 1px solid var(--border);" class="rounded-xl p-3.5 mb-4 text-xs space-y-1">
                <p style="color: var(--text-muted);">Номер телефона: <span style="color: var(--accent);" class="font-mono font-semibold">{html.escape(phone)}</span></p>
                <p class="text-amber-500">Способ доставки: <b>{html.escape(delivery)}</b></p>
            </div>
            {err_block}
            <form action="/finish-phone-login" method="post" class="flex flex-col gap-4">
                <input type="hidden" name="session_name" value="{html.escape(session_name)}">
                <div>
                    <label style="color: var(--text-muted);" class="text-xs font-medium block mb-1.5">Код из Telegram или SMS</label>
                    <input name="code" placeholder="12345" autofocus style="background: var(--bg-main); border: 1px solid var(--border); color: var(--text-main);" class="w-full rounded-xl p-3 text-sm outline-none font-mono tracking-widest">
                </div>
                <div>
                    <label style="color: var(--text-muted);" class="text-xs font-medium block mb-1.5">Облачный пароль 2FA (если установлен)</label>
                    <input name="password" type="password" placeholder="Введите пароль 2FA при наличии" style="background: var(--bg-main); border: 1px solid var(--border); color: var(--text-main);" class="w-full rounded-xl p-3 text-xs outline-none">
                </div>
                <button type="submit" style="background-color: var(--accent);" class="mt-1 w-full hover:opacity-95 font-semibold py-3 rounded-xl text-xs text-white transition shadow-lg cursor-pointer">
                    Подтвердить и подключить
                </button>
                <a href="/" style="color: var(--text-muted);" class="text-center text-xs hover:underline mt-1 block">Вернуться в панель управления</a>
            </form>
        </div>
    </body>
    </html>
    """


async def _do_start(session_path: str, api_id: int, api_hash: str, phone: str, bind_ip: str, device_info: dict):
    kwargs = {
        "api_id": api_id,
        "api_hash": api_hash,
        "loop": tg_loop,
        "device_model": device_info["device_model"],
        "system_version": device_info["system_version"],
        "app_version": device_info["app_version"],
        "lang_code": "ru",
        "system_lang_code": "ru-RU",
    }
    if bind_ip:
        if ":" in bind_ip:
            kwargs["use_ipv6"] = True
        kwargs["local_addr"] = (bind_ip, 0)

    for ext in [".session", ".session-journal", ".session-wal", ".session-shm"]:
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
        delivery = "по SMS на телефон"
    elif isinstance(sent.type, SentCodeTypeCall):
        delivery = "звонком на телефон"
    elif isinstance(sent.type, SentCodeTypeFlashCall):
        delivery = "через Flash Call"

    return client, sent.phone_code_hash, delivery


async def _do_finish(client: TelegramClient, phone: str, code: str, code_hash: str, pwd: str, already_code_ok: bool = False):
    if not already_code_ok:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=code_hash)
        except SessionPasswordNeededError:
            if pwd:
                await client.sign_in(password=pwd)
            else:
                raise SessionPasswordNeededError("Требуется облачный пароль 2FA")
    else:
        await client.sign_in(password=pwd)

    me = await client.get_me()
    await client.disconnect()
    return me


@app.route("/start-phone-login", methods=["POST"])
def start_phone_login():
    phone = request.form.get("phone", "").strip().replace(" ", "").replace("-", "")
    raw_session_name = request.form.get("session_name", "").strip()
    session_name = sanitize_session_name(raw_session_name)
    if not session_name:
        clean_phone = re.sub(r"[^0-9]", "", phone)
        session_name = f"acc_{clean_phone[-4:] if len(clean_phone) >= 4 else 'new'}"

    api_id_str = request.form.get("api_id", "").strip() or "2040"
    api_id = int(api_id_str) if api_id_str.isdigit() else 2040
    api_hash = request.form.get("api_hash", "").strip() or "b18441a1ff607e10a989891a5462e627"
    bind_ip = request.form.get("bind_ip", "").strip()

    if not phone:
        return "Укажите номер телефона", 400

    device_info = get_device_fingerprint(session_name)
    session_path = os.path.join(DATA_DIR, session_name)
    try:
        client, code_hash, delivery = run_tg(
            _do_start(session_path, api_id, api_hash, phone, bind_ip, device_info),
            timeout=35,
        )
        login_sessions[session_name] = {
            "client": client,
            "session_name": session_name,
            "phone": phone,
            "api_id": api_id,
            "api_hash": api_hash,
            "bind_ip": bind_ip,
            "device_info": device_info,
            "phone_code_hash": code_hash,
            "delivery": delivery,
            "code_accepted_need_2fa": False,
        }
    except FloodWaitError as e:
        return _render_code_verification_page(
            session_name, phone, "-", f"FloodWait от Telegram: подождите {e.seconds} сек."
        ), 429
    except (PhoneNumberInvalidError, PhoneNumberBannedError) as e:
        return _render_code_verification_page(
            session_name, phone, "-", f"Номер телефона заблокирован или некорректен: {e}"
        ), 400
    except Exception as e:
        return _render_code_verification_page(
            session_name, phone, "-", f"Ошибка при запросе кода: {e}"
        ), 400

    return _render_code_verification_page(session_name, phone, delivery)


@app.route("/finish-phone-login", methods=["POST"])
def finish_phone_login():
    session_name = sanitize_session_name(request.form.get("session_name", ""))
    code = request.form.get("code", "").strip()
    pwd = request.form.get("password", "").strip()

    data = login_sessions.get(session_name)
    if not data:
        return "Сессия авторизации устарела или не найдена. <a href='/'>Вернуться в панель</a>", 400

    client = data["client"]
    try:
        me = run_tg(
            _do_finish(
                client,
                data["phone"],
                code,
                data["phone_code_hash"],
                pwd,
                already_code_ok=data.get("code_accepted_need_2fa", False),
            ),
            timeout=35,
        )
    except SessionPasswordNeededError:
        data["code_accepted_need_2fa"] = True
        return _render_code_verification_page(
            session_name,
            data["phone"],
            data.get("delivery", ""),
            "Код принят! На аккаунте включен 2FA — введите облачный пароль в поле ниже.",
        ), 200
    except PhoneCodeInvalidError:
        return _render_code_verification_page(
            session_name,
            data["phone"],
            data.get("delivery", ""),
            "Неверный код подтверждения. Попробуйте ввести еще раз.",
        ), 200
    except PasswordHashInvalidError:
        data["code_accepted_need_2fa"] = True
        return _render_code_verification_page(
            session_name,
            data["phone"],
            data.get("delivery", ""),
            "Неверный облачный пароль 2FA. Проверьте раскладку и введите снова.",
        ), 200
    except PhoneCodeExpiredError:
        del login_sessions[session_name]
        return _render_code_verification_page(
            session_name,
            data["phone"],
            data.get("delivery", ""),
            "Срок действия кода истек. Вернитесь назад и запросите код заново.",
        ), 400
    except Exception as e:
        return _render_code_verification_page(
            session_name,
            data["phone"],
            data.get("delivery", ""),
            f"Ошибка авторизации: {e}",
        ), 400

    del login_sessions[session_name]

    final_name = session_name
    if session_name.startswith("acc_") and me and me.first_name:
        clean_first = sanitize_session_name(me.first_name)
        if clean_first:
            final_name = clean_first
            for ext in [".session", ".session-journal"]:
                s_old = os.path.join(DATA_DIR, f"{session_name}{ext}")
                s_new = os.path.join(DATA_DIR, f"{final_name}{ext}")
                if os.path.exists(s_old):
                    try:
                        shutil.move(s_old, s_new)
                    except Exception:
                        pass

    for ext in [".session", ".session-journal"]:
        src = os.path.join(DATA_DIR, f"{final_name}{ext}")
        dst = os.path.join(BASE_DIR, f"{final_name}{ext}")
        if os.path.exists(src):
            try:
                shutil.copy2(src, dst)
            except Exception:
                pass

    dev_info = get_device_fingerprint(final_name, data.get("device_info"))
    accs = load_accounts()
    accs = [a for a in accs if sanitize_session_name(a.get("session", "")) != final_name]
    accs.append({
        "session": final_name,
        "phone": data["phone"],
        "api_id": data["api_id"],
        "api_hash": data["api_hash"],
        "bind_ip": data["bind_ip"],
        "device_model": dev_info["device_model"],
        "system_version": dev_info["system_version"],
        "app_version": dev_info["app_version"],
        "balance": "0.00",
        "ping": "-",
        "added_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    save_accounts(accs)
    return redirect("/")


# --- Баланс (/balance) ---
def _parse_balance_from_text(txt: str) -> str | None:
    if not txt:
        return None
    clean = txt.replace("**", "").replace("__", "").replace("`", "")
    m = re.search(r"баланс[^\d\n]{0,20}([\d]+(?:[.,]\d+)?)", clean, re.IGNORECASE)
    if m:
        return f"{float(m.group(1).replace(',', '.')):.2f}"
    m2 = re.search(r"([\d]+(?:[.,]\d+)?)\s*(?:₽|руб|rub)", clean, re.IGNORECASE)
    if m2:
        return f"{float(m2.group(1).replace(',', '.')):.2f}"
    return None


async def _fetch_acc_balance(acc: dict, target_bot: str, sem: asyncio.Semaphore) -> tuple[str, str]:
    s_name = acc["session"]
    balance_val = str(acc.get("balance", "0.00"))
    async with sem:
        tmp_session, _ = create_temp_web_session(s_name)
        if not tmp_session:
            return s_name, balance_val

        kwargs = build_client_kwargs(acc)
        client = TelegramClient(tmp_session, **kwargs)
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return s_name, balance_val

            peer = await client.get_input_entity(target_bot)
            cmd = await client.send_message(peer, "/balance")

            parsed_new = None
            for _ in range(22):
                await asyncio.sleep(0.14)
                msgs = await client.get_messages(peer, limit=4)
                for m in msgs:
                    if m.out or m.id <= cmd.id:
                        continue
                    parsed_new = _parse_balance_from_text(m.text or "")
                    if parsed_new is not None:
                        balance_val = parsed_new
                        break
                if parsed_new is not None:
                    break
        except Exception as e:
            print(f"[!] Ошибка запроса баланса {s_name}: {e}", flush=True)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            cleanup_temp_web_session(tmp_session)

        return s_name, balance_val


async def _check_all_balances(target_session: str | None = None, trigger_autowithdraw: bool = True):
    settings = load_settings()
    target_bot = settings.get("target_bot", "Virtnumber_buy_bot").lstrip("@").strip()
    accs = load_accounts()
    sem = asyncio.Semaphore(6)
    targets = [a for a in accs if not target_session or a.get("session") == target_session]
    tasks = [_fetch_acc_balance(a, target_bot, sem) for a in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    accs_map = {a["session"]: a for a in accs}
    ready_for_auto = []
    min_w = float(settings.get("min_withdraw_amount", 30.0))
    auto_w = bool(settings.get("auto_withdraw_enabled", True))
    site_login = str(settings.get("site_withdraw_login", "") or "").strip()

    for res in results:
        if isinstance(res, tuple):
            s_name, b_val = res
            if s_name in accs_map:
                accs_map[s_name]["balance"] = b_val
                accs_map[s_name]["last_checked"] = datetime.now().strftime("%H:%M:%S")
                try:
                    if trigger_autowithdraw and auto_w and site_login and float(str(b_val).replace(",", ".")) >= min_w:
                        ready_for_auto.append(s_name)
                except ValueError:
                    pass
    save_accounts(list(accs_map.values()))

    if ready_for_auto:
        for s_auto in ready_for_auto:
            await _run_all_withdrawals(s_auto)


@app.route("/accounts/check-balance", methods=["POST"])
def check_balance_route():
    target_session = request.form.get("session_name", "").strip() or None
    run_tg(_check_all_balances(target_session))
    if _is_ajax_request():
        msg = f"Баланс {target_session} обновлен" if target_session else "Балансы всех аккаунтов обновлены"
        return jsonify(_build_dashboard_ajax_summary(msg, ok=True))
    return redirect("/")


@app.route("/accounts/check-balance-all", methods=["POST"])
def check_balance_all_route():
    run_tg(_check_all_balances())
    settings = load_settings()
    for node in settings.get("nodes", []):
        threading.Thread(target=send_remote_request, args=(node, "/accounts/check-balance"), daemon=True).start()
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary("Балансы всех серверов обновлены", ok=True))
    return redirect("/")


# --- Авто-вывод баланса на сайт (Ускоренный турбо-поллинг) ---
async def _withdraw_acc(acc: dict, target_bot: str, site_login: str, min_withdraw: float, sem: asyncio.Semaphore) -> tuple[str, bool, str, float, str | None]:
    s_name = acc["session"]
    if not site_login:
        return s_name, False, "Логин для вывода не указан", 0.0, None

    async with sem:
        tmp_session, _ = create_temp_web_session(s_name)
        if not tmp_session:
            return s_name, False, "Нет файла сессии", 0.0, None

        kwargs = build_client_kwargs(acc)
        client = TelegramClient(tmp_session, **kwargs)
        current_bal_str = None
        try:
            await client.connect()
            if not await client.is_user_authorized():
                return s_name, False, "Не авторизован", 0.0, None

            peer = await client.get_input_entity(target_bot)
            cmd = await client.send_message(peer, "/balance")

            balance_msg = None
            current_bal = None
            for _ in range(24):
                await asyncio.sleep(0.14)
                msgs = await client.get_messages(peer, limit=4)
                for m in msgs:
                    if m.id > cmd.id and not m.out:
                        b_str = _parse_balance_from_text(m.text or "")
                        if b_str is not None:
                            current_bal_str = b_str
                            current_bal = float(b_str)
                        if m.buttons:
                            for row in m.buttons:
                                for btn in row:
                                    if "перенести" in (btn.text or "").lower():
                                        balance_msg = m
                                        break
                    if balance_msg:
                        break
                if balance_msg or (current_bal is not None and current_bal < min_withdraw):
                    break

            if current_bal is not None and current_bal < min_withdraw:
                print(
                    f"[{s_name}] Баланс {current_bal:.2f} ₽ < минималки ({min_withdraw:.0f} ₽), пропуск",
                    flush=True,
                )
                return s_name, False, f"Меньше {min_withdraw:.0f} ₽ ({current_bal:.2f} ₽)", 0.0, current_bal_str

            if not balance_msg:
                print(f"[{s_name}] Кнопка 'Перенести баланс' не найдена", flush=True)
                return s_name, False, "Нет кнопки переноса", 0.0, current_bal_str

            clicked_transfer = False
            for row_idx, row in enumerate(balance_msg.buttons):
                for col_idx, btn in enumerate(row):
                    if "перенести" in (btn.text or "").lower():
                        await balance_msg.click(row_idx, col_idx)
                        clicked_transfer = True
                        break
                if clicked_transfer:
                    break

            print(f"[{s_name}] Нажата кнопка 'Перенести баланс'. Ожидаем запрос ID/логина...", flush=True)

            prompt_ready = False
            bot_error_reason = ""
            for _ in range(24):
                await asyncio.sleep(0.14)
                msgs = await client.get_messages(peer, limit=4)
                for m in msgs:
                    if m.out or m.id < balance_msg.id:
                        continue
                    txt_low = (m.text or "").lower()
                    if any(w in txt_low for w in ["минимальн", "недостаточно", "ошибка", "недоступ"]):
                        bot_error_reason = (m.text or "").strip()
                        break
                    if m.id > balance_msg.id or m.edit_date:
                        if any(w in txt_low for w in ["введите id", "введите логин", "укажите id", "id пользователя"]):
                            prompt_ready = True
                            break
                if prompt_ready or bot_error_reason:
                    break

            if bot_error_reason:
                print(f"[{s_name}] Отказ бота при переносе: {bot_error_reason}", flush=True)
                return s_name, False, bot_error_reason, 0.0, current_bal_str

            if not prompt_ready:
                print(f"[{s_name}] Форма ввода ID не открылась, отмена отправки логина", flush=True)
                return s_name, False, "Форма ввода ID не открылась", 0.0, current_bal_str

            sent_login = await client.send_message(peer, site_login)
            print(f"[{s_name}] Отправлен логин '{site_login}'. Ожидаем кнопку подтверждения...", flush=True)

            confirm_msg = None
            for _ in range(24):
                await asyncio.sleep(0.14)
                msgs = await client.get_messages(peer, limit=4)
                for m in msgs:
                    if m.id > sent_login.id and not m.out and m.buttons:
                        for row in m.buttons:
                            for btn in row:
                                if "подтвердить" in (btn.text or "").lower():
                                    confirm_msg = m
                                    break
                    if confirm_msg:
                        break
                if confirm_msg:
                    break

            if not confirm_msg:
                print(f"[{s_name}] Кнопка подтверждения не появилась", flush=True)
                return s_name, False, "Нет кнопки подтверждения", 0.0, current_bal_str

            for row_idx, row in enumerate(confirm_msg.buttons):
                for col_idx, btn in enumerate(row):
                    if "подтвердить" in (btn.text or "").lower():
                        await confirm_msg.click(row_idx, col_idx)
                        break

            withdrawn_amount = current_bal if current_bal is not None else min_withdraw
            print(f"[OK] [{s_name}] Перенос {withdrawn_amount:.2f} ₽ на '{site_login}' успешно подтвержден!", flush=True)
            await asyncio.sleep(0.25)
            return s_name, True, "Успешно выведено", float(withdrawn_amount), "0.00"

        except Exception as e:
            print(f"[!] Ошибка вывода на {s_name}: {e}", flush=True)
            return s_name, False, str(e), 0.0, current_bal_str
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            cleanup_temp_web_session(tmp_session)


async def _run_all_withdrawals(target_session: str | None = None) -> dict:
    settings = load_settings()
    target_bot = settings.get("target_bot", "Virtnumber_buy_bot").lstrip("@").strip()
    site_login = settings.get("site_withdraw_login", "").strip()
    min_withdraw = float(settings.get("min_withdraw_amount", 30.0))
    if not site_login:
        print("[!] Логин для вывода на сайт не настроен в конфигурации!", flush=True)
        return {"ok": False, "message": "Сначала укажите ID / логин сайта для вывода!", "withdrawn_rub": 0.0, "count": 0}

    accs = load_accounts()
    sem = asyncio.Semaphore(6)
    targets = [a for a in accs if not target_session or a.get("session") == target_session]
    tasks = [_withdraw_acc(a, target_bot, site_login, min_withdraw, sem) for a in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    accs_map = {a["session"]: a for a in accs}
    total_withdrawn_now = 0.0
    withdrawn_sessions = []
    reasons = []
    now_str = datetime.now().strftime("%H:%M:%S")
    for res in results:
        if isinstance(res, tuple) and len(res) >= 3:
            s_name, ok, reason = res[0], res[1], res[2]
            w_amt = float(res[3]) if len(res) >= 4 else 0.0
            det_bal = res[4] if len(res) >= 5 else None
            if s_name in accs_map:
                accs_map[s_name]["last_checked"] = now_str
                if ok:
                    accs_map[s_name]["balance"] = "0.00"
                    accs_map[s_name]["last_withdrawn_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                    total_withdrawn_now += w_amt
                    withdrawn_sessions.append(s_name)
                else:
                    if det_bal is not None:
                        accs_map[s_name]["balance"] = det_bal
                    if reason:
                        reasons.append(f"{s_name}: {reason}")
    save_accounts(list(accs_map.values()))

    if withdrawn_sessions:
        settings = load_settings()
        settings["total_withdrawn_rub"] = round(float(settings.get("total_withdrawn_rub", 0.0)) + total_withdrawn_now, 2)
        settings["total_withdrawn_count"] = int(settings.get("total_withdrawn_count", 0)) + len(withdrawn_sessions)
        save_settings(settings)
        push_mobile_event(
            "auto_withdraw",
            f"💸 Вывод выполнен: {total_withdrawn_now:.2f} ₽",
            f"Средства ({total_withdrawn_now:.2f} ₽) переведены на ID {site_login} ({', '.join(withdrawn_sessions)})",
            badge=f"{total_withdrawn_now:.0f}₽",
        )
        return {
            "ok": True,
            "message": f"Выведено {total_withdrawn_now:.2f} ₽ на {site_login} ({len(withdrawn_sessions)} акк.)",
            "withdrawn_rub": total_withdrawn_now,
            "count": len(withdrawn_sessions),
        }

    short_reason = reasons[0] if reasons else f"Нет аккаунтов с балансом ≥ {min_withdraw:.0f} ₽"
    return {
        "ok": True,
        "message": f"Вывод завершен: {short_reason}",
        "withdrawn_rub": 0.0,
        "count": 0,
    }


@app.route("/accounts/withdraw", methods=["POST"])
def withdraw_route():
    target_session = request.form.get("session_name", "").strip() or None
    res_info = run_tg(_run_all_withdrawals(target_session)) or {}
    if _is_ajax_request():
        return jsonify(
            _build_dashboard_ajax_summary(
                res_info.get("message", "Операция вывода завершена"),
                ok=bool(res_info.get("ok", True)),
                extra={"withdraw_result": res_info},
            )
        )
    return redirect("/")


@app.route("/accounts/withdraw-all", methods=["POST"])
def withdraw_all_route():
    res_info = run_tg(_run_all_withdrawals()) or {}
    settings = load_settings()
    for node in settings.get("nodes", []):
        threading.Thread(target=send_remote_request, args=(node, "/accounts/withdraw"), daemon=True).start()
    if _is_ajax_request():
        return jsonify(
            _build_dashboard_ajax_summary(
                res_info.get("message", "Глобальный вывод выполнен"),
                ok=bool(res_info.get("ok", True)),
                extra={"withdraw_result": res_info},
            )
        )
    return redirect("/")


# --- Проверка аккаунтов (/start) ---
async def _health_check_acc(acc: dict, target_bot: str, sem: asyncio.Semaphore) -> tuple[str, bool, str]:
    s_name = acc["session"]
    async with sem:
        tmp_session, _ = create_temp_web_session(s_name)
        if not tmp_session:
            return s_name, True, ""

        kwargs = build_client_kwargs(acc)
        client = TelegramClient(tmp_session, **kwargs)
        is_alive = True
        death_reason = ""
        try:
            await client.connect()
            if not await client.is_user_authorized():
                is_alive = False
                death_reason = "Сессия сброшена / не авторизована"
            else:
                me = await client.get_me()
                if not me or getattr(me, "deleted", False):
                    is_alive = False
                    death_reason = "Аккаунт удален в Telegram"
                else:
                    try:
                        await client.send_message(target_bot, "/start")
                    except Exception as e:
                        if is_fatal_account_error(e):
                            is_alive = False
                            death_reason = f"Бан Telegram: {e}"
        except Exception as e:
            if is_fatal_account_error(e):
                is_alive = False
                death_reason = f"Бан / Сброс: {e}"
            else:
                print(f"[WARN] [{s_name}] Таймаут при проверке здоровья: {e}", flush=True)
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass
            cleanup_temp_web_session(tmp_session)

        return s_name, is_alive, death_reason


async def _run_health_checks(target_session: str | None = None) -> list[str]:
    settings = load_settings()
    target_bot = settings.get("target_bot", "Virtnumber_buy_bot").lstrip("@").strip()
    accs = load_accounts()
    sem = asyncio.Semaphore(6)
    targets = [a for a in accs if not target_session or a.get("session") == target_session]
    tasks = [_health_check_acc(a, target_bot, sem) for a in targets]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    removed = []
    for res in results:
        if isinstance(res, tuple):
            s_name, is_alive, reason = res
            if not is_alive:
                move_acc_to_trash(s_name, reason)
                removed.append(s_name)
    return removed


@app.route("/accounts/check-health", methods=["POST"])
def check_health_route():
    target_session = request.form.get("session_name", "").strip() or None
    removed = run_tg(_run_health_checks(target_session)) or []
    if _is_ajax_request():
        msg = (
            f"Проверка завершена: в корзину перемещено {len(removed)} акк."
            if removed
            else f"Аккаунт {target_session or 'все'} активен (/start отправлен)"
        )
        return jsonify(_build_dashboard_ajax_summary(msg, ok=True, extra={"removed_sessions": removed}))
    return redirect("/")


@app.route("/accounts/check-health-all", methods=["POST"])
def check_health_all_route():
    removed = run_tg(_run_health_checks()) or []
    settings = load_settings()
    for node in settings.get("nodes", []):
        threading.Thread(target=send_remote_request, args=(node, "/accounts/check-health"), daemon=True).start()
    if _is_ajax_request():
        msg = f"Проверка завершена (удалено невалидных: {len(removed)})" if removed else "Все аккаунты живы и активны!"
        return jsonify(_build_dashboard_ajax_summary(msg, ok=True, extra={"removed_sessions": removed}))
    return redirect("/")


# --- Тестер парсера ваучеров, уравнений и ответов целевого бота ---
@app.route("/api/test-voucher", methods=["POST"])
def api_test_voucher():
    ocr_time_ms = None
    ocr_text = ""
    is_photo = False
    code = None
    raw_text = ""

    if "photo" in request.files:
        photo_file = request.files["photo"]
        photo_bytes = photo_file.read()
        if photo_bytes:
            is_photo = True
            t0 = time.perf_counter()
            code, ocr_text = extract_voucher_from_image(photo_bytes)
            ocr_time_ms = round((time.perf_counter() - t0) * 1000, 1)
            raw_text = ocr_text
    else:
        payload = request.get_json(silent=True) or {}
        image_b64 = payload.get("image_base64")
        if image_b64:
            is_photo = True
            if "," in image_b64:
                image_b64 = image_b64.split(",", 1)[1]
            try:
                photo_bytes = base64.b64decode(image_b64)
                t0 = time.perf_counter()
                code, ocr_text = extract_voucher_from_image(photo_bytes)
                ocr_time_ms = round((time.perf_counter() - t0) * 1000, 1)
                raw_text = ocr_text
            except Exception as e:
                return jsonify({"error": f"Base64 error: {e}"}), 400
        else:
            raw_text = str(payload.get("text", "") or "")
            code = extract_voucher_from_message(raw_text, None)

    solved_x = solve_math_equation(raw_text)
    final_code = code
    if final_code and "*" in final_code and solved_x is not None:
        final_code = final_code.replace("*", solved_x)

    bot_reply_parsed = parse_bot_activation_response(raw_text)
    balance_parsed = parse_balance_from_text(raw_text)

    return jsonify({
        "raw_code": code,
        "solved_x": solved_x,
        "final_code": final_code,
        "is_photo": is_photo,
        "ocr_time_ms": ocr_time_ms,
        "ocr_text": ocr_text if is_photo else None,
        "success": bool(final_code and "*" not in final_code),
        "bot_reply_status": bot_reply_parsed.get("status"),
        "bot_reply_amount": bot_reply_parsed.get("amount"),
        "bot_reply_balance": bot_reply_parsed.get("balance") or balance_parsed,
        "bot_reply_summary": bot_reply_parsed.get("summary"),
    })


@app.route("/api/vouchers-history/clear", methods=["POST"])
def clear_vouchers_history():
    try:
        _atomic_write_json(VOUCHERS_HISTORY_FILE, [])
    except Exception:
        pass
    return redirect("/?tab=vouchers")


@app.route("/accounts/randomize-devices", methods=["POST"])
def randomize_devices_route():
    accs = load_accounts()
    for a in accs:
        chosen = random.choice(DEVICE_PROFILES)
        a["device_model"] = chosen["device_model"]
        a["system_version"] = chosen["system_version"]
        a["app_version"] = chosen["app_version"]
    save_accounts(accs)
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary(f"Назначены новые отпечатки устройств ({len(DEVICE_PROFILES)} профилей)", ok=True))
    return redirect("/")


@app.route("/settings/quick-autowithdraw", methods=["POST"])
def quick_autowithdraw_route():
    settings = load_settings()
    settings["auto_withdraw_enabled"] = bool(request.form.get("auto_withdraw_enabled"))
    if "min_withdraw_amount" in request.form:
        try:
            settings["min_withdraw_amount"] = max(1.0, float(request.form.get("min_withdraw_amount", 30.0)))
        except ValueError:
            pass
    if "site_withdraw_login" in request.form:
        login_val = request.form.get("site_withdraw_login", "").strip()
        if login_val:
            settings["site_withdraw_login"] = login_val
    save_settings(settings)
    if _is_ajax_request():
        state_lbl = "ВКЛ" if settings["auto_withdraw_enabled"] else "ВЫКЛ"
        return jsonify(
            _build_dashboard_ajax_summary(
                f"Авто-вывод сохранен ({state_lbl}, от {settings['min_withdraw_amount']:.0f} ₽ → {settings.get('site_withdraw_login') or 'не задан'})",
                ok=True,
            )
        )
    return redirect("/")


@app.route("/node/add", methods=["POST"])
def add_node():
    settings = load_settings()
    name = request.form.get("node_name", "").strip()
    raw_url = request.form.get("node_url", "").strip()
    user = request.form.get("node_user", "").strip() or "admin"
    pwd = request.form.get("node_pass", "").strip()

    if name and raw_url:
        clean_url = raw_url.rstrip("/")
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = f"http://{clean_url}"
        host_part = clean_url.split("://", 1)[1]
        if ":" not in host_part and "/" not in host_part:
            clean_url = f"{clean_url}:8080"

        nodes = settings.get("nodes", [])
        nodes.append({"name": name, "url": clean_url, "user": user, "pass": pwd})
        settings["nodes"] = nodes
        save_settings(settings)

    return redirect("/?tab=settings")


@app.route("/node/delete", methods=["POST"])
def delete_node():
    settings = load_settings()
    try:
        idx = int(request.form.get("node_index", -1))
    except ValueError:
        idx = -1
    nodes = settings.get("nodes", [])
    if 0 <= idx < len(nodes):
        nodes.pop(idx)
        settings["nodes"] = nodes
        save_settings(settings)
    return redirect("/?tab=settings")


@app.route("/settings/save", methods=["POST"])
def save_settings_route():
    settings = load_settings()
    settings["admin_user"] = request.form.get("admin_user", "admin").strip() or "admin"
    settings["admin_pass"] = request.form.get("admin_pass", "superpass123").strip() or "superpass123"
    settings["source_channel"] = request.form.get("source_channel", "").strip().lstrip("@")
    settings["target_bot"] = request.form.get("target_bot", "").strip().lstrip("@")
    settings["custom_domain"] = request.form.get("custom_domain", "").strip()
    settings["site_withdraw_login"] = request.form.get("site_withdraw_login", "").strip()

    try:
        settings["min_withdraw_amount"] = max(1.0, float(request.form.get("min_withdraw_amount", 30.0)))
    except ValueError:
        settings["min_withdraw_amount"] = 30.0

    try:
        settings["jitter_min_ms"] = max(0, int(request.form.get("jitter_min_ms", 20)))
        settings["jitter_max_ms"] = max(settings["jitter_min_ms"], int(request.form.get("jitter_max_ms", 80)))
    except ValueError:
        settings["jitter_min_ms"] = 20
        settings["jitter_max_ms"] = 80

    settings["auto_withdraw_enabled"] = bool(request.form.get("auto_withdraw_enabled"))
    settings["enable_smart_jitter"] = bool(request.form.get("enable_smart_jitter"))
    settings["enable_stealth_warmup"] = bool(request.form.get("enable_stealth_warmup"))
    settings["warmup_channels"] = request.form.get("warmup_channels", "telegram, durov").strip() or "telegram, durov"
    settings["push_notifications_enabled"] = bool(request.form.get("push_notifications_enabled"))
    settings["radar_teaser_enabled"] = bool(request.form.get("radar_teaser_enabled"))
    save_settings(settings)
    return redirect("/?tab=settings")


@app.route("/api/settings/toggle-push", methods=["POST"])
def api_toggle_push():
    settings = load_settings()
    req_data = request.get_json(silent=True) or {}
    if "enabled" in req_data:
        settings["push_notifications_enabled"] = bool(req_data["enabled"])
    else:
        settings["push_notifications_enabled"] = not settings.get("push_notifications_enabled", True)
    save_settings(settings)
    return jsonify({"ok": True, "push_notifications_enabled": settings["push_notifications_enabled"]})


@app.route("/service/restart", methods=["POST"])
def restart_service():
    if os.name != "nt":
        subprocess.run(["systemctl", "restart", "farmbot"], check=False)
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary("Служба снайпера перезапущена", ok=True))
    return redirect("/")


@app.route("/service/start", methods=["POST"])
def start_service():
    if os.name != "nt":
        subprocess.run(["systemctl", "start", "farmbot"], check=False)
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary("Служба снайпера запущена", ok=True))
    return redirect("/")


@app.route("/service/stop", methods=["POST"])
def stop_service():
    if os.name != "nt":
        subprocess.run(["systemctl", "stop", "farmbot"], check=False)
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary("Служба снайпера остановлена", ok=True))
    return redirect("/")


@app.route("/service/restart-all", methods=["POST"])
def restart_all_services():
    if os.name != "nt":
        subprocess.run(["systemctl", "restart", "farmbot"], check=False)
    settings = load_settings()
    for node in settings.get("nodes", []):
        threading.Thread(target=send_remote_request, args=(node, "/service/restart"), daemon=True).start()
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary("Перезапуск отправлен на все серверы", ok=True))
    return redirect("/")


@app.route("/delete", methods=["POST"])
def delete_acc():
    sess_name = sanitize_session_name(request.form.get("session_name", ""))
    if sess_name:
        move_acc_to_trash(sess_name, "Удален вручную")
    if _is_ajax_request():
        return jsonify(_build_dashboard_ajax_summary(f"Аккаунт {sess_name} перемещен в корзину", ok=True, extra={"removed_sessions": [sess_name]}))
    return redirect("/")


@app.route("/trash/restore", methods=["POST"])
def restore_acc():
    sess_name = sanitize_session_name(request.form.get("session_name", ""))
    trash = load_trash()
    target = None
    new_trash = []
    for t in trash:
        if sanitize_session_name(t.get("session", "")) == sess_name:
            target = t
        else:
            new_trash.append(t)
    save_trash(new_trash)

    if target:
        accs = load_accounts()
        accs = [a for a in accs if sanitize_session_name(a.get("session", "")) != sess_name]
        target.pop("trash_reason", None)
        target.pop("deleted_at", None)
        accs.append(target)
        save_accounts(accs)

        for base in [DATA_DIR, BASE_DIR]:
            t_dir = os.path.join(base, "trash")
            for ext in [".session", ".session-journal", ".session-wal", ".session-shm"]:
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
    sess_name = sanitize_session_name(request.form.get("session_name", ""))
    trash = load_trash()
    trash = [t for t in trash if sanitize_session_name(t.get("session", "")) != sess_name]
    save_trash(trash)

    for base in [DATA_DIR, BASE_DIR]:
        t_dir = os.path.join(base, "trash")
        for ext in [".session", ".session-journal", ".session-wal", ".session-shm"]:
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
    for base in [DATA_DIR, BASE_DIR]:
        t_dir = os.path.join(base, "trash")
        if os.path.exists(t_dir):
            try:
                shutil.rmtree(t_dir)
                os.makedirs(t_dir, exist_ok=True)
            except Exception:
                pass
    return redirect("/?tab=trash")


def _clean_journal_output(raw_out: str) -> str:
    cleaned_lines = []
    noise_patterns = (
        "Consumed ",
        "Scheduled restart job",
        "Deactivated successfully",
        "Stopped FarmBot",
        "Started FarmBot",
        "Starting FarmBot",
        "Stopping FarmBot",
    )
    for line in raw_out.splitlines():
        if any(np in line for np in noise_patterns):
            continue
        m = re.match(r"^[A-Z][a-z]{2}\s+\d+\s+(\d{2}:\d{2}:\d{2})\s+\S+\s+[^:]+:\s*(.*)$", line)
        if m:
            cleaned_lines.append(f"{m.group(1)}  {m.group(2)}")
        else:
            cleaned_lines.append(line)
    return "\n".join(cleaned_lines[-80:])


@app.route("/api/logs")
def get_logs():
    unit = request.args.get("unit", "farmbot")
    show_raw = request.args.get("raw", "0") == "1"
    if unit not in ("farmbot", "farmweb"):
        unit = "farmbot"

    if os.name == "nt":
        return jsonify({
            "logs": "[12:00:01] [*] Запуск FarmBot Sniper PRO v5.0 (Ultra ONNX OCR)\n[12:00:02] [✓] Служба активна и ожидает ваучеры..."
        })

    try:
        cmd = ["journalctl", "-u", unit, "-n", "80", "--no-pager"]
        if not show_raw:
            cmd.append("-o")
            cmd.append("cat")
        out = subprocess.check_output(
            cmd,
            text=True,
            stderr=subprocess.STDOUT,
            timeout=8,
        )
        final_logs = out if show_raw else _clean_journal_output(out)
        return jsonify({"logs": final_logs or "Журнал пуст — ожидаем события..."})
    except subprocess.TimeoutExpired:
        return jsonify({
            "logs": f"[{datetime.now().strftime('%H:%M:%S')}] [INFO] Процессор занят обработкой ваучера (1 vCPU) — служба активна, журнал обновится через несколько секунд."
        })
    except Exception as e:
        return jsonify({"logs": f"Ошибка чтения логов ({unit}): {e}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

