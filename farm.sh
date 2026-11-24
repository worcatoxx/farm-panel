#!/bin/bash

RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
SKY="\033[0;36m"
PLAIN="\033[0m"

DIR="/root/farm_panel"
DATA_DIR="/root/farm_data"
BRANCH="main"

show_service_line() {
    local svc="$1"
    local label="$2"
    if systemctl is-active --quiet "$svc"; then
        echo -e " ${label} ${GREEN}● Работает (active)${PLAIN}"
    else
        local st
        st=$(systemctl is-active "$svc" 2>/dev/null || echo "not-found")
        echo -e " ${label} ${RED}● Остановлен ($st)${PLAIN}"
    fi
}

show_menu() {
    clear
    echo -e "${SKY}==================================================${PLAIN}"
    echo -e "${SKY}     FarmBot Sniper v5.0 TURBO - Панель управления      ${PLAIN}"
    echo -e "${SKY}==================================================${PLAIN}"

    show_service_line "farmbot"       "Статус Снайпера:     "
    show_service_line "farmweb"       "Статус Веб-панели:   "
    show_service_line "xray-farm"     "Статус Xray Hub:     "
    show_service_line "xray-watchdog" "Статус Xray Watchdog:"

    if [ -f "$DATA_DIR/accounts.json" ] && [ -x "$DIR/venv/bin/python3" ]; then
        STATS=$("$DIR/venv/bin/python3" -c '
import json, os
try:
    with open("/root/farm_data/accounts.json", "r", encoding="utf-8") as f:
        accs = json.load(f)
    bal = sum(float(str(a.get("balance", 0)).replace(",", ".")) for a in accs)
    prx = 0
    xp = "/root/farm_data/xray_proxies.json"
    if os.path.exists(xp):
        with open(xp, "r", encoding="utf-8") as xf:
            prx = len(json.load(xf).get("slots", {}))
    print(f"{len(accs)} акк. | Касса: {bal:.2f} ₽ | VPN-слотов: {prx}")
except Exception:
    print("н/д")
' 2>/dev/null)
        echo -e " Боевой состав:       ${YELLOW}${STATS}${PLAIN}"
    fi

    echo -e "${SKY}--------------------------------------------------${PLAIN}"
    echo -e "  ${GREEN}1.${PLAIN} Обновить код с GitHub ($BRANCH, данные в /root/farm_data)"
    echo -e "  ${GREEN}2.${PLAIN} Перезапустить все службы (Bot + Web + Xray + Watchdog)"
    echo -e "  ${GREEN}3.${PLAIN} Запустить все службы"
    echo -e "  ${GREEN}4.${PLAIN} Остановить все службы"
    echo -e "  ${GREEN}5.${PLAIN} Смотреть live-логи снайпера (farmbot)"
    echo -e "  ${GREEN}6.${PLAIN} Смотреть live-логи веб-панели (farmweb)"
    echo -e "  ${GREEN}7.${PLAIN} Смотреть live-логи Xray Watchdog (xray-watchdog)"
    echo -e "  ${GREEN}8.${PLAIN} Смотреть live-логи Xray Hub (xray-farm)"
    echo -e "  ${GREEN}9.${PLAIN} Перезапустить Xray Hub + Xray Watchdog"
    echo -e "  ${GREEN}10.${PLAIN} Детальный статус systemd (все 4 службы)"
    echo -e "  ${GREEN}11.${PLAIN} Перепроверить аккаунты через /start бота"
    echo -e "  ${YELLOW}12.${PLAIN} Полная переустановка"
    echo -e "  ${RED}0.${PLAIN} Выход"
    echo -e "${SKY}==================================================${PLAIN}"
    echo -n " Выберите пункт [0-12]: "
    read -r choice
}

while true; do
    show_menu
    case "$choice" in
        1)
            echo -e "\n${YELLOW}[*] Сохранение сессий и обновление кода из GitHub ($BRANCH)...${PLAIN}"
            mkdir -p "$DATA_DIR"
            cp -n "$DIR"/*.session* "$DATA_DIR"/ 2>/dev/null || true
            cd "$DIR" || exit 1
            git fetch --all
            git checkout -B "$BRANCH" "origin/$BRANCH" 2>/dev/null || true
            git reset --hard "origin/$BRANCH"
            if [ -f "$DIR/requirements.txt" ] && [ -x "$DIR/venv/bin/pip" ]; then
                "$DIR/venv/bin/pip" install -r "$DIR/requirements.txt" --quiet || true
            fi
            sed -i 's/\r$//' "$DIR/farm.sh" 2>/dev/null || true
            cp -f "$DIR/farm.sh" /usr/local/bin/farm 2>/dev/null && chmod +x /usr/local/bin/farm || true
            systemctl restart farmbot farmweb xray-watchdog
            if [ -s /etc/xray-farm/config.json ]; then
                systemctl restart xray-farm || true
            fi
            echo -e "${GREEN}[✓] Код обновлен! Все данные в безопасности в /root/farm_data.${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        2)
            echo -e "\n${YELLOW}[*] Перезапуск всех служб...${PLAIN}"
            systemctl restart farmbot farmweb xray-watchdog
            if [ -s /etc/xray-farm/config.json ]; then
                systemctl restart xray-farm || true
            fi
            echo -e "${GREEN}[✓] Все службы перезапущены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        3)
            echo -e "\n${YELLOW}[*] Запуск всех служб...${PLAIN}"
            systemctl start farmbot farmweb xray-watchdog
            if [ -s /etc/xray-farm/config.json ]; then
                systemctl start xray-farm || true
            fi
            echo -e "${GREEN}[✓] Все службы запущены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        4)
            echo -e "\n${YELLOW}[*] Остановка всех служб...${PLAIN}"
            systemctl stop farmbot farmweb xray-watchdog xray-farm 2>/dev/null || true
            echo -e "${RED}[✓] Все службы остановлены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        5)
            clear
            echo -e "${SKY}[*] Логи снайпера farmbot (Ctrl+C для выхода):${PLAIN}\n"
            journalctl -u farmbot -f -n 50
            ;;
        6)
            clear
            echo -e "${SKY}[*] Логи веб-панели farmweb (Ctrl+C для выхода):${PLAIN}\n"
            journalctl -u farmweb -f -n 50
            ;;
        7)
            clear
            echo -e "${SKY}[*] Логи Xray Watchdog (Ctrl+C для выхода):${PLAIN}\n"
            journalctl -u xray-watchdog -f -n 50
            ;;
        8)
            clear
            echo -e "${SKY}[*] Логи Xray Hub (Ctrl+C для выхода):${PLAIN}\n"
            journalctl -u xray-farm -f -n 50
            ;;
        9)
            echo -e "\n${YELLOW}[*] Перезапуск Xray Hub и Xray Watchdog...${PLAIN}"
            if [ -s /etc/xray-farm/config.json ]; then
                systemctl restart xray-farm || true
            else
                echo -e "${YELLOW}[!] /etc/xray-farm/config.json ещё не создан (вставьте VPN-подписку в панели).${PLAIN}"
            fi
            systemctl restart xray-watchdog
            echo -e "${GREEN}[✓] Службы Xray перезапущены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        10)
            echo -e "\n${SKY}=== Статус FarmBot ===${PLAIN}"
            systemctl status farmbot --no-pager
            echo -e "\n${SKY}=== Статус FarmWeb ===${PLAIN}"
            systemctl status farmweb --no-pager
            echo -e "\n${SKY}=== Статус Xray Hub ===${PLAIN}"
            systemctl status xray-farm --no-pager
            echo -e "\n${SKY}=== Статус Xray Watchdog ===${PLAIN}"
            systemctl status xray-watchdog --no-pager
            read -p "Нажмите Enter для продолжения..."
            ;;
        11)
            echo -e "\n${YELLOW}[*] Проверка всех аккаунтов через отправку /start целевому боту...${PLAIN}"
            cd "$DIR" && "$DIR/venv/bin/python3" -c "import web; web.run_tg(web._run_health_checks())"
            systemctl restart farmbot
            echo -e "${GREEN}[✓] Проверка завершена, снайпер перезапущен!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        12)
            echo -e "\n${RED}[!] Будет выполнена переустановка компонентов ($BRANCH).${PLAIN}"
            echo -n "Продолжить? (y/N): "
            read -r confirm
            if [[ "$confirm" =~ ^[yY]$ ]]; then
                REPO_REMOTE=$(cd "$DIR" && git config --get remote.origin.url)
                TOKEN=$(echo "$REPO_REMOTE" | sed -n "s#.*https://\([^@]*\)@.*#\1#p")
                if [ -n "$TOKEN" ]; then
                    curl -sSL -H "Authorization: token $TOKEN" "https://raw.githubusercontent.com/worcatoxx/farm-panel/${BRANCH}/install.sh" | bash -s -- "$TOKEN"
                else
                    curl -sSL "https://raw.githubusercontent.com/worcatoxx/farm-panel/${BRANCH}/install.sh" | bash
                fi
                echo -e "${GREEN}[✓] Переустановка завершена!${PLAIN}"
            fi
            read -p "Нажмите Enter для продолжения..."
            ;;
        0)
            clear
            exit 0
            ;;
        *)
            echo -e "\n${RED}Неверный ввод!${PLAIN}"
            sleep 1
            ;;
    esac
done
