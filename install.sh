#!/bin/bash
set -e

REPO_TOKEN="$1"
REPO_URL="https://${REPO_TOKEN}@github.com/worcatoxx/farm-panel.git"
INSTALL_DIR="/root/farm_panel"

echo "=== [1/6] Подготовка системы и снятие блокировок ==="
killall -9 apt apt-get dpkg unattended-upgr 2>/dev/null || true
rm -f /var/lib/dpkg/lock* /var/lib/apt/lists/lock* /var/cache/apt/archives/lock*
dpkg --configure -a

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" \
    git python3 python3-pip python3-venv curl ufw iptables nginx

echo "=== [2/6] Настройка сети и файрвола (Oracle + UFW) ==="
# Открываем порты 80, 443 и 8080 для веб-панели и Cloudflare
iptables -I INPUT -p tcp --dport 80 -j ACCEPT 2>/dev/null || true
iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
if command -v ufw >/dev/null; then
    ufw allow 80/tcp || true
    ufw allow 8080/tcp || true
fi

echo "=== [3/6] Клонирование / Обновление репозитория ==="
if [ -d "$INSTALL_DIR/.git" ]; then
    cd "$INSTALL_DIR"
    git fetch --all
    git reset --hard origin/main
else
    rm -rf "$INSTALL_DIR"
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
fi

echo "=== [4/6] Установка Python окружения и зависимостей ==="
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Создаем начальные файлы только если их еще не существует
if [ ! -f "config.json" ]; then
    cat << 'EOF' > config.json
{
    "admin_user": "admin",
    "admin_pass": "superpass123",
    "source_channel": "vaycher_virtnumber",
    "target_bot": "Virtnumber_buy_bot",
    "custom_domain": "",
    "nodes": []
}
EOF
fi

if [ ! -f "accounts.json" ]; then
    echo "[]" > accounts.json
fi

echo "=== [5/6] Настройка Nginx (проксирование 80 -> 8080 для Cloudflare) ==="
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
    }
}
EOF
systemctl restart nginx

echo "=== [6/6] Создание и запуск системных служб ==="
cat << 'EOF' > /etc/systemd/system/farmweb.service
[Unit]
Description=FarmBot Web Panel
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
EOF

cat << 'EOF' > /etc/systemd/system/farmbot.service
[Unit]
Description=FarmBot Daemon
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
EOF

systemctl daemon-reload
systemctl enable farmweb farmbot
systemctl restart farmweb farmbot

IP=$(curl -s https://api.ipify.org || echo "IP_СЕРВЕРА")
echo "=========================================="
echo " Установка успешно завершена!"
echo " Панель доступна по адресу: http://$IP (или :8080)"
echo " Логин: admin | Пароль: superpass123"
echo "=========================================="
