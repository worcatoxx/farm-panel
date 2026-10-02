import asyncio
import json
import os
import re
from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityCode, MessageEntityPre

CONFIG_FILE = "/root/farm_panel/accounts.json"
SETTINGS_FILE = "/root/farm_panel/config.json"

clients = []

def load_settings():
    default_settings = {
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

def load_accounts():
    if not os.path.exists(CONFIG_FILE):
        return []
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

async def send_to_bot(client: TelegramClient, target_bot: str, code: str):
    try:
        await client.send_message(target_bot, code)
        me = await client.get_me()
        print(f"[OK] {me.first_name} (@{me.username}) отправил код в @{target_bot}: {code}")
    except Exception as e:
        print(f"[ERR] Ошибка отправки: {e}")

async def keep_alive():
    while True:
        await asyncio.sleep(30)
        for c in clients:
            try:
                if c.is_connected():
                    await c.get_me()
            except Exception:
                pass

def solve_math_equation(text: str) -> str | None:
    # Ищем выражение со знаком равенства
    match = re.search(r"([xх\d\s\+\-\*\/·×÷:()]+=\s*[xх\d\s\+\-\*\/·×÷:()]+)", text, re.IGNORECASE)
    if not match:
        return None

    raw_eq = match.group(1).strip()
    parts = raw_eq.split("=")
    if len(parts) != 2:
        return None

    def normalize(expr: str) -> str:
        expr = expr.replace("·", "*").replace("×", "*")
        expr = expr.replace(":", "/").replace("÷", "/")
        expr = re.sub(r"[хХ]", "x", expr)
        return expr

    left = normalize(parts[0].strip())
    right = normalize(parts[1].strip())

    # Перебор потерянной цифры от 0 до 9
    for digit in range(10):
        test_left = re.sub(r"\bx\b|x", str(digit), left, flags=re.IGNORECASE)
        test_right = re.sub(r"\bx\b|x", str(digit), right, flags=re.IGNORECASE)
        try:
            val_left = eval(test_left)
            val_right = eval(test_right)
            if abs(val_left - val_right) < 0.0001:
                return str(digit)
        except ZeroDivisionError:
            continue
        except Exception:
            continue

    return None

async def main():
    settings = load_settings()
    source_channel = settings.get("source_channel", "vaycher_virtnumber")
    target_bot = settings.get("target_bot", "Virtnumber_buy_bot")

    accounts = load_accounts()
    if not accounts:
        print("[!] Список аккаунтов пуст.")
        return

    for acc in accounts:
        session_name = acc["session"]
        session_path = f"/root/farm_panel/{session_name}"
        bind_ip = acc.get("bind_ip")

        kwargs = {
            "session": session_path,
            "api_id": acc["api_id"],
            "api_hash": acc["api_hash"],
            "auto_reconnect": True,
            "connection_retries": -1,
            "retry_delay": 1,
            "timeout": 15
        }

        if bind_ip and str(bind_ip).strip():
            target_ip = str(bind_ip).strip()
            kwargs["local_addr"] = (target_ip, 0)
            print(f"[*] {session_name} -> Биндится на IP: {target_ip}")
        else:
            print(f"[*] {session_name} -> Основной IP сервера")

        c = TelegramClient(**kwargs)
        try:
            await c.connect()
            if await c.is_user_authorized():
                me = await c.get_me()
                clients.append(c)
                print(f"[+] Сессия {session_name} онлайн: {me.first_name}")
            else:
                print(f"[-] Сессия {session_name} НЕ авторизована!")
        except Exception as e:
            print(f"[!] Ошибка подключения {session_name}: {e}")

    print(f"\n[✓] Итого активных клиентов: {len(clients)}")
    print(f"[✓] Канал мониторинга: @{source_channel}")
    print(f"[✓] Целевой бот: @{target_bot}")

    if not clients:
        return

    listener = clients[0]

    @listener.on(events.NewMessage(chats=source_channel))
    async def handler(event):
        msg = event.message
        voucher_code = None
        raw_text = msg.text or ""

        # 1. Поиск ваучера с поддержкой символа маски *
        match = re.search(r"va[a-zA-Z0-9*]{2,10}-[a-zA-Z0-9*]{2,10}-[a-zA-Z0-9*]{2,10}", raw_text)
        if match:
            voucher_code = match.group(0).strip()

        # 2. Поиск в блоках monospace, если регулярка текста не сработала
        if not voucher_code and msg.entities:
            for ent in msg.entities:
                if isinstance(ent, (MessageEntityCode, MessageEntityPre)):
                    encoded = raw_text.encode("utf-16-le")
                    sub = encoded[ent.offset * 2 : (ent.offset + ent.length) * 2]
                    extracted = sub.decode("utf-16-le").strip("`'\" \n\r\t“”«»")
                    if "-" in extracted:
                        voucher_code = extracted
                        break

        # 3. Обработка найденного кода
        if voucher_code:
            if "*" in voucher_code:
                digit = solve_math_equation(raw_text)
                if digit is not None:
                    voucher_code = voucher_code.replace("*", digit)
                    print(f"[⚡] Решено уравнение: x = {digit}! Итоговый ваучер: {voucher_code}")
                else:
                    print(f"[!] Не удалось решить уравнение для ваучера {voucher_code}")

            print(f"\n[!] ПОЙМАН ВАУЧЕР: {voucher_code}. Залп со всех {len(clients)} аккаунтов...")
            await asyncio.gather(*(send_to_bot(client, target_bot, voucher_code) for client in clients))

    asyncio.create_task(keep_alive())

    while True:
        try:
            await listener.run_until_disconnected()
        except Exception as e:
            print(f"[WARN] Реконнект: {e}")
            await asyncio.sleep(1)
            try:
                if not listener.is_connected():
                    await listener.connect()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
