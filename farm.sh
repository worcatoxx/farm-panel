#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
SKY='\033[0;36m'
PLAIN='\033[0m'

DIR="/root/farm_panel"

show_menu() {
    clear
    echo -e "${SKY}==================================================${PLAIN}"
    echo -e "${SKY}        FarmBot Sniper - Панель управления        ${PLAIN}"
    echo -e "${SKY}==================================================${PLAIN}"
    
    BOT_STATUS=$(systemctl is-active farmbot 2>/dev/null || echo "not-found")
    WEB_STATUS=$(systemctl is-active farmweb 2>/dev/null || echo "not-found")

    if [ "$BOT_STATUS" = "active" ]; then
        echo -e " Статус Бота:       ${GREEN}● Работает (active)${PLAIN}"
    else
        echo -e " Статус Бота:       ${RED}● Остановлен ($BOT_STATUS)${PLAIN}"
    fi

    if [ "$WEB_STATUS" = "active" ]; then
        echo -e " Статус Веб-панели: ${GREEN}● Работает (active)${PLAIN}"
    else
        echo -e " Статус Веб-панели: ${RED}● Остановлен ($WEB_STATUS)${PLAIN}"
    fi

    echo -e "${SKY}--------------------------------------------------${PLAIN}"
    echo -e "  ${GREEN}1.${PLAIN} Обновить код с GitHub (данные в /root/farm_data)"
    echo -e "  ${GREEN}2.${PLAIN} Перезапустить все службы (Bot + Web)"
    echo -e "  ${GREEN}3.${PLAIN} Запустить все службы"
    echo -e "  ${GREEN}4.${PLAIN} Остановить все службы"
    echo -e "  ${GREEN}5.${PLAIN} Смотреть live-логи бота (снайпера)"
    echo -e "  ${GREEN}6.${PLAIN} Смотреть live-логи веб-панели"
    echo -e "  ${GREEN}7.${PLAIN} Детальный статус systemd"
    echo -e "  ${GREEN}8.${PLAIN} Перепроверить аккаунты через /start бота"
    echo -e "  ${YELLOW}9.${PLAIN} Полная переустановка"
    echo -e "  ${RED}0.${PLAIN} Выход"
    echo -e "${SKY}==================================================${PLAIN}"
    echo -n " Выберите пункт [0-9]: "
    read -r choice
}

while true; do
    show_menu
    case "$choice" in
        1)
            echo -e "\n${YELLOW}[*] Обновление кода репозитория...${PLAIN}"
            cd "$DIR" || exit 1
            git fetch --all
            git reset --hard origin/main
            cp -f "$DIR/farm.sh" /usr/local/bin/farm 2>/dev/null && chmod +x /usr/local/bin/farm || true
            systemctl restart farmbot farmweb
            echo -e "${GREEN}[✓] Код обновлен! Все настройки и сессии в безопасности в /root/farm_data.${PLAIN}"
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
            echo -e "\n${YELLOW}[*] Перезапуск с проверкой аккаунтов...${PLAIN}"
            systemctl restart farmbot
            echo -e "${GREEN}[✓] Бот запущен, нерабочие аккаунты уйдут в Корзину.${PLAIN}"
            read -p "Нажмите Enter для продолжения..."
            ;;
        9)
            echo -e "\n${RED}[!] Будет выполнена переустановка компонентов.${PLAIN}"
            echo -n "Продолжить? (y/N): "
            read -r confirm
            if [[ "$confirm" =~ ^[yY]$ ]]; then
                REPO_REMOTE=$(cd "$DIR" && git config --get remote.origin.url)
                TOKEN=$(echo "$REPO_REMOTE" | sed -n 's#.*https://\([^@]*\)@.*#\1#p')
                curl -sSL -H "Authorization: token $TOKEN" https://raw.githubusercontent.com/worcatoxx/farm-panel/main/install.sh | bash -s -- "$TOKEN"
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
