import os
import json
import subprocess
import threading
import urllib.request
import base64
from flask import Flask, render_template, request, redirect, session, jsonify

app = Flask(__name__)
app.secret_key = os.urandom(24)

CONFIG_FILE = "/root/farm_panel/accounts.json"
SETTINGS_FILE = "/root/farm_panel/config.json"

def load_settings():
    default_settings = {
        "admin_user": "admin",
        "admin_pass": "superpass123",
        "source_channel": "vaycher_virtnumber",
        "target_bot": "Virtnumber_buy_bot",
        "custom_domain": "",
        "linked_nodes": ""
    }
    if not os.path.exists(SETTINGS_FILE):
        return default_settings
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in default_settings.items():
                data.setdefault(k, v)
            return data
    except Exception:
        return default_settings

def save_settings(settings):
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=4, ensure_ascii=False)

def load_accounts():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_accounts(accounts):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(accounts, f, indent=4, ensure_ascii=False)

def get_server_ips():
    primary_ip = "127.0.0.1"
    secondary_ip = None
    try:
        out = subprocess.check_output(["ip", "-4", "addr", "show", "scope", "global"], text=True)
        ips = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip = line.split()[1].split("/")[0]
                ips.append(ip)
        if ips:
            primary_ip = ips[0]
            if len(ips) > 1:
                secondary_ip = ips[1]
    except Exception:
        pass
    return primary_ip, secondary_ip

def get_service_status():
    try:
        res = subprocess.run(["systemctl", "is-active", "farmbot"], capture_output=True, text=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"

def parse_linked_nodes(raw_text):
    nodes = []
    if not raw_text:
        return nodes
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        name = parts[0]
        url = parts[1].rstrip("/")
        user = parts[2] if len(parts) > 2 else "admin"
        pwd = parts[3] if len(parts) > 3 else ""
        nodes.append({"name": name, "url": url, "user": user, "pass": pwd})
    return nodes

def trigger_remote_restart(node):
    url = f"{node['url']}/service/restart"
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
    if request.method == "POST":
        user = request.form.get("username")
        pwd = request.form.get("password")
        if user == settings["admin_user"] and pwd == settings["admin_pass"]:
            session["logged_in"] = True
            return redirect("/")
        return render_template("login.html", error="Неверный логин или пароль")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")

@app.route("/")
def index():
    settings = load_settings()
    primary_ip, secondary_ip = get_server_ips()
    accounts = load_accounts()

    primary_accs = [a for a in accounts if not a.get("bind_ip") or a.get("bind_ip") == primary_ip]
    secondary_accs = [a for a in accounts if a.get("bind_ip") == secondary_ip] if secondary_ip else []

    available_ips = [{"val": "", "label": f"Основной IP ({primary_ip})"}]
    if secondary_ip:
        available_ips.append({"val": secondary_ip, "label": f"Дополнительный IP ({secondary_ip})"})

    linked_nodes = parse_linked_nodes(settings.get("linked_nodes", ""))

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
        linked_nodes=linked_nodes
    )

@app.route("/settings/save", methods=["POST"])
def save_settings_route():
    settings = load_settings()
    settings["admin_user"] = request.form.get("admin_user", "").strip()
    settings["admin_pass"] = request.form.get("admin_pass", "").strip()
    settings["source_channel"] = request.form.get("source_channel", "").strip()
    settings["target_bot"] = request.form.get("target_bot", "").strip()
    settings["custom_domain"] = request.form.get("custom_domain", "").strip()
    settings["linked_nodes"] = request.form.get("linked_nodes", "").strip()
    save_settings(settings)
    return redirect("/?tab=settings")

@app.route("/service/restart", methods=["POST"])
def restart_service():
    subprocess.run(["systemctl", "restart", "farmbot"])
    return redirect("/")

@app.route("/service/restart-all", methods=["POST"])
def restart_all_services():
    # 1. Рестарт текущего сервера
    subprocess.run(["systemctl", "restart", "farmbot"])
    # 2. Параллельный сигнал остальным VPS
    settings = load_settings()
    nodes = parse_linked_nodes(settings.get("linked_nodes", ""))
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

@app.route("/delete", methods=["POST"])
def delete_acc():
    sess_name = request.form.get("session_name")
    accs = load_accounts()
    accs = [a for a in accs if a["session"] != sess_name]
    save_accounts(accs)
    sess_file = f"/root/farm_panel/{sess_name}.session"
    if os.path.exists(sess_file):
        try:
            os.remove(sess_file)
        except Exception:
            pass
    return redirect("/")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
