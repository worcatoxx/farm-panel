import asyncio
import json
import os
import re
import random
import hashlib
import shutil
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityCode, MessageEntityPre

BASE_DIR = "/root/farm_panel"
DATA_DIR = "/root/farm_data"
os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

TRASH_DIR = os.path.join(DATA_DIR, "trash")
os.makedirs(TRASH_DIR, exist_ok=True)

clients = []
client_sessions = {}
target_peers = {}
processed_vouchers = set()

def load_settings():
    default_settings = {
        "source_channel": "vaycher_virtnumber",
        "target_bot": "Virtnumber_buy_bot",
        "enable_smart_jitter": False
    }
    for p in [os.path.join(DATA_DIR, "config.json"), os.path.join(BASE_DIR, "config.json")]:
        if os.path.exists(p) and os.path.getsize(p) > 5:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for k, v in default_settings.items():
                        data.setdefault(k, v)
                    return data
            except Exception:
                pass
    return default_settings

def load_accounts():
    for p in [os.path.join(DATA_DIR, "accounts.json"), os.path.join(BASE_DIR, "accounts.json")]:
        if os.path.exists(p) and os.path.getsize(p) > 2:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    accs = json.load(f)
                    if accs:
                        return accs
            except Exception:
                pass
    return []

def save_accounts(accounts):
    for d in [DATA_DIR, BASE_DIR]:
        try:
            with open(os.path.join(d, "accounts.json"), "w", encoding="utf-8") as f:
                json.dump(accounts, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Ошибка сохранения accounts.json: {e}", flush=True)

def load_trash():
    for p in [os.path.join(DATA_DIR, "trash.json"), os.path.join(BASE_DIR, "trash.json")]:
        if os.path.exists(p) and os.path.getsize(p) > 2:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return []

def save_trash(trash):
    for d in [DATA_DIR, BASE_DIR]:
        try:
            with open(os.path.join(d, "trash.json"), "w", encoding="utf-8") as f:
                json.dump(trash, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"[!] Ошибка сохранения trash.json: {e}", flush=True)

def move_to_trash(session_name: str, reason: str = ""):
    try:
        s_target = str(session_name).strip()
        accs = load_accounts()
        target_acc = None
        new_accs = []
        for a in accs:
            if str(a.get("session")).strip() == s_target:
                target_acc = a
            else:
                new_accs.append(a)
        save_accounts(new_accs)

        if not target_acc:
            target_acc = {"session": s_target, "phone": "-"}

        target_acc["trash_reason"] = reason
        target_acc["deleted_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

        trash = load_trash()
        trash = [t for t in trash if str(t.get("session")).strip() != s_target]
        trash.append(target_acc)
        save_trash(trash)

        for base in [DATA_DIR, BASE_DIR]:
            t_dir = os.path.join(base, "trash")
            os.makedirs(t_dir, exist_ok=True)
            for ext in [".session", ".session-journal"]:
                src = os.path.join(base, f"{s_target}{ext}")
                dst = os.path.join(t_dir, f"{s_target}{ext}")
                if os.path.exists(src):
                    try: shutil.move(src, dst)
                    except Exception: pass

        print(f"[🗑️ В КОРЗИНУ] Сессия {s_target} перемещена: {reason}", flush=True)
    except Exception as e:
        print(f"[!] Ошибка перемещения в корзину {session_name}: {e}", flush=True)

def get_device_fingerprint(session_name: str):
    devices = [
        {"device_model": "Samsung Galaxy S23", "system_version": "Android 14", "app_version": "10.14.0"},
        {"device_model": "Xiaomi Redmi Note 13", "system_version": "Android 13", "app_version": "10.13.1"},
        {"device_model": "Desktop x64", "system_version": "Windows 11", "app_version": "5.5.1 x64"},
        {"device_model": "Desktop x64", "system_version": "Windows 10", "app_version": "5.4.0 x64"},
        {"device_model": "Google Pixel 8", "system_version": "Android 14", "app_version": "10.14.2"}
    ]
    idx = int(hashlib.md5(str(session_name).encode()).hexdigest(), 16) % len(devices)
    return devices[idx]

async def init_client_target(client: TelegramClient, target_bot: str):
    try:
        peer = await client.get_input_entity(target_bot)
        target_peers[client] = peer
        return peer
    except Exception as e:
        print(f"[!] Не удалось закэшировать бота: {e}", flush=True)
        return None

async def send_to_bot(client: TelegramClient, target_bot: str, code: str, delay_ms: float = 0):
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    sess_name = client_sessions.get(client, "unknown")
    try:
        peer = target_peers.get(client, target_bot)
        await client.send_message(peer, code)
        me = await client.get_me()
        print(f"[OK] {me.first_name} (@{me.username}) отправил код: {code} (задержка: {int(delay_ms)}мс)", flush=True)
    except Exception as e:
        err_msg = str(e)
        print(f"[x] Ошибка отправки на {sess_name}: {err_msg}", flush=True)
        if any(w in err_msg.lower() for w in ["frozen", "deactivated", "banned", "restricted", "forbidden", "auth_key"]):
            if client in clients:
                clients.remove(client)
            move_to_trash(sess_name, f"Слетел при отправке: {err_msg}")

async def keep_alive():
    while True:
        await asyncio.sleep(25)
        for c in list(clients):
            sess_name = client_sessions.get(c, "unknown")
            try:
                if not c.is_connected():
                    await c.connect()
                if not await c.is_user_authorized():
                    raise Exception("Сессия сброшена")
                await c.get_me()
            except Exception as e:
                err_msg = str(e)
                if any(w in err_msg.lower() for w in ["frozen", "deactivated", "banned", "auth_key", "revoked"]):
                    if c in clients:
                        clients.remove(c)
                    move_to_trash(sess_name, f"Отлетел в фоне: {err_msg}")
                    try: await c.disconnect()
                    except Exception: pass

def solve_math_equation(text: str) -> str | None:
    if not text:
        return None

    clean_text = text.replace("**", "").replace("__", "").replace("`", "")
    clean_text = re.sub(r"[хХX]", "x", clean_text)

    for line in clean_text.splitlines():
        line = line.strip()
        if not line or "=" not in line or "x" not in line:
            continue

        if re.search(r"x\s*=\s*[\*\?]+", line):
            continue

        parts = line.split("=")
        if len(parts) != 2:
            continue

        def parse_side(side: str) -> str:
            side = re.sub(r"[:÷⁄]", "/", side)
            side = re.sub(r"[−–—]", "-", side)
            side = re.sub(r"[•·⋅∙‧×✕✖]", "*", side)
            side = re.sub(r"x\s*[^0-9+\-/()=]\s*(\d)", r"x*\1", side)
            side = re.sub(r"(\d)\s*[^0-9+\-/()=]\s*x", r"\1*x", side)
            side = re.sub(r"(\d)x", r"\1*x", side)
            side = re.sub(r"x(\d)", r"x*\1", side)
            side = re.sub(r"[^0-9x+\-*/().]", "", side)
            return side.strip("+-*/.")

        left = parse_side(parts[0])
        right = parse_side(parts[1])

        if not left or not right or "x" not in (left + right):
            continue

        for digit in range(10):
            t_left = re.sub(r"\bx\b|x", str(digit), left)
            t_right = re.sub(r"\bx\b|x", str(digit), right)
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
    enable_jitter = settings.get("enable_smart_jitter", False)

    accounts = load_accounts()
    if not accounts:
        print("[!] Список аккаунтов пуст.", flush=True)
        return

    print("[*] Подключение активных аккаунтов (без отправки /start)...", flush=True)

    for acc in accounts:
        session_name = str(acc["session"]).strip()
        session_path = os.path.join(DATA_DIR, session_name)
        if not os.path.exists(f"{session_path}.session"):
            session_path = os.path.join(BASE_DIR, session_name)

        bind_ip = acc.get("bind_ip")
        device_info = get_device_fingerprint(session_name)

        kwargs = {
            "session": session_path,
            "api_id": acc.get("api_id", 2040),
            "api_hash": acc.get("api_hash", "b18441a1ff607e10a989891a5462e627"),
            "device_model": device_info["device_model"],
            "system_version": device_info["system_version"],
            "app_version": device_info["app_version"],
            "lang_code": "ru",
            "system_lang_code": "ru-RU",
            "auto_reconnect": True,
            "connection_retries": 1,
            "retry_delay": 1,
            "timeout": 8
        }

        if bind_ip and str(bind_ip).strip():
            target_ip = str(bind_ip).strip()
            if ":" in target_ip:
                kwargs["use_ipv6"] = True
            kwargs["local_addr"] = (target_ip, 0)

        c = TelegramClient(**kwargs)
        is_alive = False
        death_reason = ""

        try:
            await c.connect()
            if not await c.is_user_authorized():
                death_reason = "Сессия сброшена / не авторизована"
            else:
                me = await c.get_me()
                if not me or getattr(me, "deleted", False):
                    death_reason = "Аккаунт удален в Telegram"
                else:
                    is_alive = True
                    clients.append(c)
                    client_sessions[c] = session_name
                    print(f"[+] Сессия {session_name} онлайн: {me.first_name} ({acc.get('phone', '-')})", flush=True)
        except Exception as e:
            death_reason = str(e)

        if not is_alive:
            try: await c.disconnect()
            except Exception: pass
            move_to_trash(session_name, death_reason)

    print(f"\n[✓] Итого активных аккаунтов: {len(clients)}", flush=True)
    print(f"[✓] Канал перехвата: @{source_channel}", flush=True)
    print(f"[✓] Целевой бот: @{target_bot}", flush=True)
    print(f"[✓] Smart Jitter: {'ВКЛЮЧЕН' if enable_jitter else 'ВЫКЛЮЧЕН'}", flush=True)

    if not clients:
        print("[!] Нет доступных аккаунтов для снайпинга.", flush=True)
        return

    print("[*] Кэшируем бота...", flush=True)
    await asyncio.gather(*(init_client_target(c, target_bot) for c in clients), return_exceptions=True)
    print("[✓] Ферма готова к работе", flush=True)

    listener = clients[0]

    @listener.on(events.NewMessage(chats=source_channel))
    async def handler(event):
        msg = event.message
        voucher_code = None
        raw_text = msg.text or ""

        match = re.search(r"va[a-zA-Z0-9*]{2,10}-[a-zA-Z0-9*]{2,10}-[a-zA-Z0-9*]{2,10}", raw_text)
        if match:
            voucher_code = match.group(0).strip()

        if not voucher_code and msg.entities:
            for ent in msg.entities:
                if isinstance(ent, (MessageEntityCode, MessageEntityPre)):
                    encoded = raw_text.encode("utf-16-le")
                    sub = encoded[ent.offset * 2 : (ent.offset + ent.length) * 2]
                    extracted = sub.decode("utf-16-le").strip("`'\" \n\r\t“”«»")
                    if "-" in extracted:
                        voucher_code = extracted
                        break

        if voucher_code:
            if voucher_code in processed_vouchers:
                return

            if "*" in voucher_code:
                digit = solve_math_equation(raw_text)
                if digit is not None:
                    voucher_code = voucher_code.replace("*", digit)
                    print(f"[⚡] Решено: x = {digit}! Код: {voucher_code}", flush=True)
                else:
                    print(f"[ERR] Не удалось решить уравнение для {voucher_code}. Пропуск.", flush=True)
                    return

            processed_vouchers.add(voucher_code)
            if len(processed_vouchers) > 200:
                processed_vouchers.clear()

            print(f"\n[!] ПОЙМАН ВАУЧЕР: {voucher_code}. Залп со всех {len(clients)} аккаунтов...", flush=True)
            
            tasks = []
            for idx, client in enumerate(list(clients)):
                delay = 0
                if enable_jitter and idx > 0:
                    delay = random.uniform(20.0, 80.0) * idx
                tasks.append(send_to_bot(client, target_bot, voucher_code, delay))

            await asyncio.gather(*tasks)

    asyncio.create_task(keep_alive())

    while True:
        try:
            await listener.run_until_disconnected()
        except Exception as e:
            print(f"[WARN] Реконнект слушателя: {e}", flush=True)
            await asyncio.sleep(2)
            try:
                if not listener.is_connected():
                    await listener.connect()
            except Exception:
                pass

if __name__ == "__main__":
    asyncio.run(main())
