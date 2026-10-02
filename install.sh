#!/bin/bash
set -e

# Цвета для терминала
C_RESET="\033[0m"
C_CYAN="\033[1;36m"
C_GREEN="\033[1;32m"
C_YELLOW="\033[1;33m"
C_BLUE="\033[1;34m"
C_RED="\033[1;31m"

# Токен передается первым аргументом, либо берется дефолтный
GH_TOKEN="${1:-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}"
REPO_NAME="worcatoxx/farm-panel"
INSTALL_DIR="/root/farm_panel"

clear
echo -e "${C_CYAN}"
echo "  ╔════════════════════════════════════════════════════════════════╗"
echo "  ║                   FARMBOT SNIPER DASHBOARD                    ║"
echo "  ║                 Автоматический развертыватель                  ║"
echo "  ╚════════════════════════════════════════════════════════════════╝"
echo -e "${C_RESET}"

echo -e "${C_BLUE}[1/5]${C_RESET} Обновление системы и установка утилит..."
apt update -qq && apt install -y -qq git python3 python3-pip python3-venv curl ufw >/dev/null 2>&1

echo -e "${C_BLUE}[2/5]${C_RESET} Остановка старых процессов и клонирование репозитория..."
systemctl stop farmweb farmbot 2>/dev/null || true
rm -rf "$INSTALL_DIR"
git clone --quiet "https://${GH_TOKEN}@github.com/${REPO_NAME}.git" "$INSTALL_DIR"

echo -e "${C_BLUE}[3/5]${C_RESET} Сборка изолированного окружения Python..."
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/venv/bin/pip" install fastapi uvicorn jinja2 python-multipart telethon -q

# Создаем конфиги, если в репозитории их нет
[ ! -f "$INSTALL_DIR/accounts.json" ] && echo "[]" > "$INSTALL_DIR/accounts.json"
if [ ! -f "$INSTALL_DIR/config.json" ]; then
cat << 'CFG' > "$INSTALL_DIR/config.json"
{
    "admin_user": "admin",
    "admin_pass": "superpass123",
    "source_channel": "vaycher_virtnumber",
    "target_bot": "Virtnumber_buy_bot"
}
CFG
fi

echo -e "${C_BLUE}[4/5]${C_RESET} Создание фоновых служб systemd..."
cat << 'SERVICE' > /etc/systemd/system/farmweb.service
[Unit]
Description=FarmBot Web Dashboard
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/farm_panel
ExecStart=/root/farm_panel/venv/bin/uvicorn web:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
SERVICE

cat << 'SERVICE' > /etc/systemd/system/farmbot.service
[Unit]
Description=FarmBot Daemon
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/farm_panel
ExecStart=/root/farm_panel/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable --now farmweb farmbot >/dev/null 2>&1

echo -e "${C_BLUE}[5/5]${C_RESET} Настройка портов и файрвола..."
ufw allow 8080/tcp >/dev/null 2>&1 || true
iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true

# Определение внешнего IP
SERVER_IP=$(curl -s https://api.ipify.org || hostname -I | awk '{print $1}')

echo ""
echo -e "${C_GREEN}  ┌──────────────────────────────────────────────────────────────┐${C_RESET}"
echo -e "${C_GREEN}  │${C_RESET}                 ${C_CYAN}УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА${C_RESET}                  ${C_GREEN}│${C_RESET}"
echo -e "${C_GREEN}  ├──────────────────────────────────────────────────────────────┤${C_RESET}"
echo -e "${C_GREEN}  │${C_RESET}  ${C_YELLOW}Веб-панель:${C_RESET}   http://${SERVER_IP}:8080"
echo -e "${C_GREEN}  │${C_RESET}  ${C_YELLOW}Службы:${C_RESET}       farmweb [OK] | farmbot [OK]"
echo -e "${C_GREEN}  │${C_RESET}  ${C_YELLOW}Каталог:${C_RESET}      /root/farm_panel"
echo -e "${C_GREEN}  │${C_RESET}  ${C_YELLOW}Управление:${C_RESET}   systemctl restart farmweb"
echo -e "${C_GREEN}  └──────────────────────────────────────────────────────────────┘${C_RESET}"
echo ""
