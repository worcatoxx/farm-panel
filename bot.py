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
    if not text:
        return None

    # Приводим любые варианты x (русские и заглавные) к английскому x
    normalized_text = re.sub(r"[хХX]", "x", text)

    for line in normalized_text.splitlines():
        line = line.strip()
        if not line or "=" not in line or "x" not in line:
            continue

        # Пропускаем строку подсказки вроде x=* или x = *
        if re.search(r"x\s*=\s*[\*\?]", line):
            continue

        parts = line.split("=")
        if len(parts) != 2:
            continue

        def parse_side(side: str) -> str:
            # Знаки деления и вычитания
            side = re.sub(r"[:÷⁄]", "/", side)
            side = re.sub(r"[−–—]", "-", side)
            # Любая точка, буллит, звездочка или спецсимвол между x и числом -> знак умножения *
            side = re.sub(r"x\s*[^0-9+\-/()=]\s*(\d)", r"x*\1", side)
            side = re.sub(r"(\d)\s*[^0-9+\-/()=]\s*x", r"\1*x", side)
            # Неявное умножение вроде 9x или x9 -> 9*x или x*9
            side = re.sub(r"(\d)x", r"\1*x", side)
            side = re.sub(r"x(\d)", r"x*\1", side)
            # Убираем кавычки цитат и лишний мусор
            return re.sub(r"[^0-9x+\-*/().]", "", side)

        left = parse_side(parts[0])
        right = parse_side(parts[1])

        if not left or not right or "x" not in (left + right):
            continue

        # Подбираем цифру от 0 до 9
        for digit in range(10):
            t_left = re.sub(r"x", str(digit), left)
            t_right = re.sub(r"x", str(digit), right)
            try:
                vl = eval(t_left)
                vr = eval(t_right)
                if abs(vl - vr) < 0.0001:
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

        # 1. Поиск ваучера (включая маску *)
        match = re.search(r"va[a-zA-Z0-9*]{2,10}-[a-zA-Z0-9*]{2,10}-[a-zA-Z0-9*]{2,10}", raw_text)
        if match:
            voucher_code = match.group(0).strip()

        # 2. Поиск в monospace блоках
        if not voucher_code and msg.entities:
            for ent in msg.entities:
                if isinstance(ent, (MessageEntityCode, MessageEntityPre)):
                    encoded = raw_text.encode("utf-16-le")
                    sub = encoded[ent.offset * 2 : (ent.offset + ent.length) * 2]
                    extracted = sub.decode("utf-16-le").strip("`'\" \n\r\t“”«»")
                    if "-" in extracted:
                        voucher_code = extracted
                        break

        # 3. Обработка ваучера
        if voucher_code:
            print(f"[DEBUG] Получен текст поста:\n{raw_text}")
            if "*" in voucher_code:
                digit = solve_math_equation(raw_text)
                if digit is not None:
                    voucher_code = voucher_code.replace("*", digit)
                    print(f"[⚡] Решено: x = {digit}! Итоговый ваучер: {voucher_code}")
                else:
                    print(f"[ERR] Не удалось решить пример для {voucher_code}. Отправка отменена!")
                    return

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
