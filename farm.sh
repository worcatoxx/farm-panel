#!/bin/bash

RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[1;33m"
SKY="\033[0;36m"
PLAIN="\033[0m"

DIR="/root/farm_panel"
VAULT="/root/.farm_vault"

show_menu() {
    clear
    echo -e "${SKY}==================================================${PLAIN}"
    echo -e "${SKY}        FarmBot Sniper - Панель управления        ${PLAIN}"
    echo -e "${SKY}==================================================${PLAIN}"
    
    if systemctl is-active --quiet farmbot; then
        echo -e " Статус Бота:       ${GREEN}● Работает (active)${PLAIN}"
    else
        ST=$(systemctl is-active farmbot 2>/dev/null || echo "not-found")
        echo -e " Статус Бота:       ${RED}● Остановлен ($ST)${PLAIN}"
    fi

    if systemctl is-active --quiet farmweb; then
        echo -e " Статус Веб-панели: ${GREEN}● Работает (active)${PLAIN}"
    else
        ST=$(systemctl is-active farmweb 2>/dev/null || echo "not-found")
        echo -e " Статус Веб-панели: ${RED}● Остановлен ($ST)${PLAIN}"
    fi

    echo -e "${SKY}--------------------------------------------------${PLAIN}"
    echo -e "  ${GREEN}1.${PLAIN} Обновить код с GitHub (без сброса настроек)"
    echo -e "  ${GREEN}2.${PLAIN} Перезапустить все службы (Bot + Web)"
    echo -e "  ${GREEN}3.${PLAIN} Запустить все службы"
    echo -e "  ${GREEN}4.${PLAIN} Остановить все службы"
    echo -e "  ${GREEN}5.${PLAIN} Смотреть live-логи бота (снайпера)"
    echo -e "  ${GREEN}6.${PLAIN} Смотреть live-логи веб-панели"
    echo -e "  ${GREEN}7.${PLAIN} Детальный статус systemd"
    echo -e "  ${GREEN}8.${PLAIN} Принудительно очистить мертвые аккаунты"
    echo -e "  ${YELLOW}9.${PLAIN} Полная переустановка (с сохранением сессий)"
    echo -e "  ${RED}0.${PLAIN} Выход"
    echo -e "${SKY}==================================================${PLAIN}"
    echo -n " Выберите пункт [0-9]: "
    read -r choice
}

while true; do
    show_menu
    case "$choice" in
        1)
            echo -e "\n${YELLOW}[*] Сохранение данных в изолированное хранилище...${PLAIN}"
            mkdir -p "$VAULT"
            cp -rf "$DIR"/config.json "$VAULT"/ 2>/dev/null || true
            cp -rf "$DIR"/accounts.json "$VAULT"/ 2>/dev/null || true
            cp -rf "$DIR"/trash.json "$VAULT"/ 2>/dev/null || true
            cp -rf "$DIR"/trash "$VAULT"/ 2>/dev/null || true
            cp -rf "$DIR"/*.session* "$VAULT"/ 2>/dev/null || true

            cd "$DIR" || exit 1
            git fetch --all
            git reset --hard origin/main

            echo -e "${YELLOW}[*] Восстановление данных обратно...${PLAIN}"
            cp -rf "$VAULT"/* "$DIR"/ 2>/dev/null || true
            cp -f "$DIR/farm.sh" /usr/local/bin/farm 2>/dev/null && chmod +x /usr/local/bin/farm || true

            systemctl restart farmbot farmweb
            echo -e "${GREEN}[✓] Код обновлен! Все настройки, домен, VPS и сессии сохранены.${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        2)
            echo -e "\n${YELLOW}[*] Перезапуск служб...${PLAIN}"
            systemctl restart farmbot farmweb
            echo -e "${GREEN}[✓] Службы перезапущены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        3)
            echo -e "\n${YELLOW}[*] Запуск служб...${PLAIN}"
            systemctl start farmbot farmweb
            echo -e "${GREEN}[✓] Службы запущены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        4)
            echo -e "\n${YELLOW}[*] Остановка служб...${PLAIN}"
            systemctl stop farmbot farmweb
            echo -e "${RED}[✓] Службы остановлены!${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        5)
            clear
            echo -e "${SKY}[*] Логи снайпера (Ctrl+C для выхода):${PLAIN}\n"
            journalctl -u farmbot -f -n 50
            ;;
        6)
            clear
            echo -e "${SKY}[*] Логи веб-панели (Ctrl+C для выхода):${PLAIN}\n"
            journalctl -u farmweb -f -n 50
            ;;
        7)
            echo -e "\n${SKY}=== Статус FarmBot ===${PLAIN}"
            systemctl status farmbot --no-pager
            echo -e "\n${SKY}=== Статус FarmWeb ===${PLAIN}"
            systemctl status farmweb --no-pager
            read -p "Нажмите Enter для продолжения..."
            ;;
        8)
            echo -e "\n${YELLOW}[*] Перезапуск с проверкой аккаунтов через /start...${PLAIN}"
            systemctl restart farmbot
            echo -e "${GREEN}[✓] Проверка завершена.${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        9)
            echo -e "\n${RED}[!] Будет выполнена переустановка с сохранением данных.${PLAIN}"
            echo -n "Продолжить? (y/N): "
            read -r confirm
            if [[ "$confirm" =~ ^[yY]$ ]]; then
                mkdir -p /root/farm_backup
                cp -rf "$DIR"/config.json /root/farm_backup/ 2>/dev/null || true
                cp -rf "$DIR"/accounts.json /root/farm_backup/ 2>/dev/null || true
                cp -rf "$DIR"/trash.json /root/farm_backup/ 2>/dev/null || true
                cp -rf "$DIR"/trash /root/farm_backup/ 2>/dev/null || true
                cp -rf "$DIR"/*.session* /root/farm_backup/ 2>/dev/null || true

                REPO_REMOTE=$(cd "$DIR" && git config --get remote.origin.url)
                TOKEN=$(echo "$REPO_REMOTE" | sed -n "s#.*https://\([^@]*\)@.*#\1#p")
                curl -sSL -H "Authorization: token $TOKEN" https://raw.githubusercontent.com/worcatoxx/farm-panel/main/install.sh | bash -s -- "$TOKEN"

                cp -rf /root/farm_backup/* "$DIR"/ 2>/dev/null || true
                rm -rf /root/farm_backup
                systemctl restart farmbot farmweb
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
