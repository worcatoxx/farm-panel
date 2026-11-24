#!/bin/bash
set -e

REPO_TOKEN="$1"
BRANCH="main"
if [ -n "$REPO_TOKEN" ] && [ "$REPO_TOKEN" != "local" ]; then
    REPO_URL="https://${REPO_TOKEN}@github.com/worcatoxx/farm-panel.git"
else
    REPO_URL="https://github.com/worcatoxx/farm-panel.git"
fi
INSTALL_DIR="/root/farm_panel"
DATA_DIR="/root/farm_data"
XRAY_DIR="/etc/xray-farm"

echo "=== [1/7] Подготовка системы и снятие блокировок ==="
killall -9 apt apt-get dpkg unattended-upgr 2>/dev/null || true
rm -f /var/lib/dpkg/lock* /var/lib/apt/lists/lock* /var/cache/apt/archives/lock*
dpkg --configure -a

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
    git python3 python3-pip python3-venv curl ufw iptables nginx unzip ca-certificates libgl1 libglib2.0-0 libgomp1

echo "=== [2/7] Установка Xray-core ==="
ARCH=$(uname -m)
case "$ARCH" in
    aarch64|arm64)
        XRAY_ZIP="Xray-linux-arm64-v8a.zip"
        ;;
    x86_64|amd64)
        XRAY_ZIP="Xray-linux-64.zip"
        ;;
    *)
        echo "[!] Неподдерживаемая архитектура: $ARCH"
        exit 1
        ;;
esac

XRAY_TMP=$(mktemp -d)
curl -fSL "https://github.com/XTLS/Xray-core/releases/latest/download/${XRAY_ZIP}" -o "${XRAY_TMP}/xray.zip"
unzip -o "${XRAY_TMP}/xray.zip" -d "${XRAY_TMP}" >/dev/null
install -m 755 "${XRAY_TMP}/xray" /usr/local/bin/xray
rm -rf "${XRAY_TMP}"
/usr/local/bin/xray version || true

echo "=== [3/7] Настройка сети и портов ==="
iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
if command -v ufw >/dev/null; then
    ufw allow 80/tcp || true
    ufw allow 8080/tcp || true
fi

echo "=== [4/7] Резервное копирование сессий и установка файлов ==="
mkdir -p "$XRAY_DIR" "$DATA_DIR/trash" "$DATA_DIR/tmp_web_sessions"
if [ -d "$INSTALL_DIR" ]; then
    cp -n "$INSTALL_DIR"/*.session* "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/accounts.json "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/config.json "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/trash.json "$DATA_DIR"/ 2>/dev/null || true
fi

if [ "$REPO_TOKEN" = "local" ] && [ -f "$INSTALL_DIR/bot.py" ]; then
    echo "[*] Используются локально загруженные файлы в $INSTALL_DIR"
    cd "$INSTALL_DIR"
elif [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git fetch --all
    git checkout -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || git checkout "$BRANCH" 2>/dev/null || true
    git reset --hard "origin/$BRANCH"
elif [ -f "$INSTALL_DIR/bot.py" ] && [ -z "$REPO_TOKEN" ]; then
    echo "[*] Найдены готовые файлы в $INSTALL_DIR"
    cd "$INSTALL_DIR"
else
    rm -rf "$INSTALL_DIR"
    git clone -b "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo "=== [5/7] Установка зависимостей и изоляция данных в /root/farm_data ==="
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r "$INSTALL_DIR/requirements.txt"

if [ ! -f "$DATA_DIR/config.json" ]; then
    cat << 'EOF' > "$DATA_DIR/config.json"
{
    "admin_user": "admin",
    "admin_pass": "superpass123",
    "source_channel": "vaycher_virtnumber",
    "target_bot": "Virtnumber_buy_bot",
    "custom_domain": "",
    "site_withdraw_login": "",
    "min_withdraw_amount": 30.0,
    "enable_smart_jitter": false,
    "jitter_min_ms": 20,
    "jitter_max_ms": 80,
    "nodes": []
}
EOF
fi

[ ! -f "$DATA_DIR/accounts.json" ] && echo "[]" > "$DATA_DIR/accounts.json"
[ ! -f "$DATA_DIR/trash.json" ] && echo "[]" > "$DATA_DIR/trash.json"
[ ! -f "$DATA_DIR/vouchers_history.json" ] && echo "[]" > "$DATA_DIR/vouchers_history.json"

echo "=== [6/7] Настройка Nginx и регистрация команды farm ==="
cat << 'EOF' > /etc/nginx/sites-available/default
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 360s;
        proxy_read_timeout 360s;
    }
}
EOF
systemctl restart nginx

if [ -f "$INSTALL_DIR/farm.sh" ]; then
    sed -i 's/\r$//' "$INSTALL_DIR/farm.sh" 2>/dev/null || true
    cp -f "$INSTALL_DIR/farm.sh" /usr/local/bin/farm
    chmod +x /usr/local/bin/farm
fi

echo "=== [7/7] Настройка 4 фоновых служб (farmweb, farmbot, xray-farm, xray-watchdog) ==="
cat << 'EOF' > /etc/systemd/system/farmweb.service
[Unit]
Description=FarmBot Web Panel v5.0
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/root/farm_panel
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/farm_panel/venv/bin/python3 /root/farm_panel/web.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' > /etc/systemd/system/farmbot.service
[Unit]
Description=FarmBot Sniper Daemon v5.0
After=network-online.target xray-farm.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/root/farm_panel
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/farm_panel/venv/bin/python3 /root/farm_panel/bot.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' > /etc/systemd/system/xray-farm.service
[Unit]
Description=FarmBot Xray SOCKS5 Proxy Hub v5.0
After=network-online.target
Wants=network-online.target
ConditionFileNotEmpty=/etc/xray-farm/config.json
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/etc/xray-farm
ExecStart=/usr/local/bin/xray run -c /etc/xray-farm/config.json
Restart=always
RestartSec=3
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

cat << 'EOF' > /etc/systemd/system/xray-watchdog.service
[Unit]
Description=FarmBot Xray Subscription Watchdog v5.0
After=network-online.target xray-farm.service
Wants=network-online.target
StartLimitIntervalSec=0

[Service]
Type=simple
User=root
WorkingDirectory=/root/farm_panel
Environment=PYTHONUNBUFFERED=1
ExecStart=/root/farm_panel/venv/bin/python3 /root/farm_panel/xray_sub_sync.py --daemon --tier 0 --interval 45 --recheck 15
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable farmweb farmbot xray-farm xray-watchdog
systemctl restart farmweb farmbot xray-watchdog
if [ -s "$XRAY_DIR/config.json" ]; then
    systemctl restart xray-farm || true
fi

IP=$(curl -s https://api.ipify.org || echo "IP_СЕРВЕРА")
echo "=================================================="
echo " Установка FarmBot Sniper v5.0 ($BRANCH) завершена!"
echo " Веб-панель: http://$IP (или :8080)"
echo " Логин: admin | Пароль: superpass123"
echo " Консольное меню управления: команда 'farm'"
echo " Службы: farmbot, farmweb, xray-farm, xray-watchdog"
echo " Все данные защищены в: /root/farm_data"
echo "=================================================="
