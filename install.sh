#!/bin/bash
set -e

# ==========================================================
#   FARMBOT SNIPER PRO v5.0 TURBO - UNIVERSAL INSTALLER
#   Работает навсегда на любом чистом сервере (Ubuntu/Debian)
# ==========================================================

TARGET_BRANCH="${1:-main}"
REPO_URL="https://github.com/worcatoxx/farm-panel.git"
INSTALL_DIR="/root/farm_panel"
DATA_DIR="/root/farm_data"

if [ "$EUID" -ne 0 ]; then
    echo "[!] Ошибка: запустите установку под root (sudo -i)!"
    exit 1
fi

echo "=========================================================="
echo "  🚀 УСТАНОВКА FARMBOT SNIPER PRO v5.0 (Branch: $TARGET_BRANCH)"
echo "=========================================================="

echo "[*] [1/6] Подготовка системы и снятие блокировок apt/dpkg..."
killall -9 apt apt-get dpkg unattended-upgr 2>/dev/null || true
rm -f /var/lib/dpkg/lock* /var/lib/apt/lists/lock* /var/cache/apt/archives/lock*
dpkg --configure -a 2>/dev/null || true

export DEBIAN_FRONTEND=noninteractive
apt-get update -y >/dev/null 2>&1 || true
apt-get install -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
    git python3 python3-pip python3-venv curl ufw iptables nginx unzip libgl1 libglib2.0-0 libgomp1 gcc python3-dev >/dev/null 2>&1 || true

echo "[*] [2/6] Настройка сети и открытие портов (80 HTTP, 8080 Web)..."
iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
if command -v ufw >/dev/null 2>&1; then
    ufw allow 80/tcp >/dev/null 2>&1 || true
    ufw allow 8080/tcp >/dev/null 2>&1 || true
fi

echo "[*] [3/6] Сохранение пользовательских сессий и выгрузка кода..."
mkdir -p "$DATA_DIR/trash" "$DATA_DIR/tmp_web_sessions"
if [ -d "$INSTALL_DIR" ]; then
    cp -n "$INSTALL_DIR"/*.session "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/accounts.json "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/config.json "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/trash.json "$DATA_DIR"/ 2>/dev/null || true
    cp -n "$INSTALL_DIR"/vouchers_history.json "$DATA_DIR"/ 2>/dev/null || true
fi

if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git fetch --all
    git checkout -B "$TARGET_BRANCH" "origin/$TARGET_BRANCH"
    git reset --hard "origin/$TARGET_BRANCH"
else
    rm -rf "$INSTALL_DIR"
    git clone -b "$TARGET_BRANCH" "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

# Возвращаем сессии в рабочую папку, если нужно
if [ -d "$DATA_DIR" ]; then
    cp -n "$DATA_DIR"/*.session "$INSTALL_DIR"/ 2>/dev/null || true
fi

echo "[*] [4/6] Настройка Python venv и установка библиотек..."
if [ ! -d "$INSTALL_DIR/venv" ]; then
    python3 -m venv "$INSTALL_DIR/venv"
fi
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip >/dev/null 2>&1 || true
"$INSTALL_DIR/venv/bin/pip" install --no-cache-dir \
    flask telethon requests pillow numpy opencv-python-headless rapidocr-onnxruntime cryptg >/dev/null 2>&1 || true

# Проверка и разогрев OCR движка
"$INSTALL_DIR/venv/bin/python3" -c "
import cv2, numpy as np
from rapidocr_onnxruntime import RapidOCR
ocr = RapidOCR()
res, _ = ocr(np.zeros((100, 100, 3), dtype=np.uint8))
print('[OK] Движок RapidOCR успешно инициализирован и проверен!')
" 2>/dev/null || true

if [ ! -f "$DATA_DIR/config.json" ]; then
    cat << 'EOF_CFG' > "$DATA_DIR/config.json"
{
    "admin_user": "admin",
    "admin_pass": "superpass123",
    "source_channel": "vaycher_virtnumber",
    "target_bot": "Virtnumber_buy_bot",
    "custom_domain": "",
    "site_withdraw_login": "",
    "min_withdraw_amount": 30.0,
    "auto_withdraw_enabled": true,
    "withdraw_delay_min_sec": 3.5,
    "withdraw_delay_max_sec": 6.5,
    "enable_smart_jitter": false,
    "jitter_min_ms": 20,
    "jitter_max_ms": 80,
    "nodes": []
}
EOF_CFG
fi

[ ! -f "$DATA_DIR/accounts.json" ] && echo "[]" > "$DATA_DIR/accounts.json"
[ ! -f "$DATA_DIR/trash.json" ] && echo "[]" > "$DATA_DIR/trash.json"
[ ! -f "$DATA_DIR/vouchers_history.json" ] && echo "[]" > "$DATA_DIR/vouchers_history.json"

echo "[*] [5/6] Настройка Nginx и команды управления 'farm'..."
cat << 'EOF_NGINX' > /etc/nginx/sites-available/default
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
EOF_NGINX
systemctl restart nginx >/dev/null 2>&1 || true

if [ -f "$INSTALL_DIR/farm.sh" ]; then
    sed -i 's/\r$//' "$INSTALL_DIR/farm.sh" 2>/dev/null || true
    cp -f "$INSTALL_DIR/farm.sh" /usr/local/bin/farm
    chmod +x /usr/local/bin/farm
fi

echo "[*] [6/6] Настройка фоновых служб (systemd)..."
cat << 'EOF_WEB_SVC' > /etc/systemd/system/farmweb.service
[Unit]
Description=FarmBot Web Panel v5.0
After=network.target
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
EOF_WEB_SVC

cat << 'EOF_BOT_SVC' > /etc/systemd/system/farmbot.service
[Unit]
Description=FarmBot Sniper Daemon v5.0
After=network.target
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
EOF_BOT_SVC

systemctl daemon-reload
systemctl enable farmweb farmbot >/dev/null 2>&1 || true
systemctl restart farmweb farmbot

IP=$(curl -s https://api.ipify.org 2>/dev/null || echo "IP_СЕРВЕРА")
echo ""
echo "=========================================================="
echo "  [OK] УСТАНОВКА FARMBOT SNIPER PRO v5.0 ЗАВЕРШЕНА!"
echo "  - Веб-панель: http://$IP (или порт 8080)"
echo "  - Логин: admin | Пароль: superpass123"
echo "  - Консольное меню управления: команда 'farm'"
echo "  - Все данные и сессии защищены в: /root/farm_data"
echo "=========================================================="
