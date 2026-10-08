import asyncio
import json
import os
import re
import hashlib
from telethon import TelegramClient, events
from telethon.tl.types import MessageEntityCode, MessageEntityPre
from telethon.errors import (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    SessionRevokedError,
    SessionExpiredError
)

CONFIG_FILE = "/root/farm_panel/accounts.json"
SETTINGS_FILE = "/root/farm_panel/config.json"

clients = []
client_sessions = {}
target_peers = {}

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

def save_accounts(accounts):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(accounts, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"[!] Ошибка сохранения accounts.json: {e}", flush=True)

def purge_dead_account(session_name: str, reason: str = ""):
    """Автоматически вырезает аккаунт из accounts.json и стирает файлы сессии с диска"""
    try:
        accs = load_accounts()
        new_accs = [a for a in accs if a.get("session") != session_name]
        save_accounts(new_accs)

        for ext in [".session", ".session-journal"]:
            fpath = f"/root/farm_panel/{session_name}{ext}"
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except Exception:
                    pass
        print(f"[🗑️ АВТО-УДАЛЕНИЕ] Сессия {session_name} ({reason}) удалена из базы и стерта с диска!", flush=True)
    except Exception as e:
        print(f"[!] Не удалось удалить {session_name}: {e}", flush=True)

def get_device_fingerprint(session_name: str):
    devices = [
        {"device_model": "Samsung Galaxy S23", "system_version": "Android 14", "app_version": "10.14.0"},
        {"device_model": "Xiaomi Redmi Note 13", "system_version": "Android 13", "app_version": "10.13.1"},
        {"device_model": "Desktop x64", "system_version": "Windows 11", "app_version": "5.5.1 x64"},
        {"device_model": "Desktop x64", "system_version": "Windows 10", "app_version": "5.4.0 x64"},
        {"device_model": "Google Pixel 8", "system_version": "Android 14", "app_version": "10.14.2"}
    ]
    idx = int(hashlib.md5(session_name.encode()).hexdigest(), 16) % len(devices)
    return devices[idx]

async def init_client_target(client: TelegramClient, target_bot: str):
    try:
        peer = await client.get_input_entity(target_bot)
        target_peers[client] = peer
    except Exception as e:
        print(f"[!] Не удалось закэшировать бота: {e}", flush=True)

async def send_to_bot(client: TelegramClient, target_bot: str, code: str):
    sess_name = client_sessions.get(client, "unknown")
    try:
        peer = target_peers.get(client, target_bot)
        await client.send_message(peer, code)
        me = await client.get_me()
        print(f"[OK] {me.first_name} (@{me.username}) отправил код: {code}", flush=True)
    except (UserDeactivatedError, UserDeactivatedBanError, AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError) as e:
        if client in clients:
            clients.remove(client)
        purge_dead_account(sess_name, f"Бан/слет при отправке: {e}")
    except Exception as e:
        err_str = str(e).lower()
        if "restricted" in err_str or "deactivated" in err_str:
            if client in clients:
                clients.remove(client)
            purge_dead_account(sess_name, f"Заморожен: {e}")
        else:
            print(f"[ERR] Ошибка отправки ({sess_name}): {e}", flush=True)

async def check_client_alive(client: TelegramClient):
    sess_name = client_sessions.get(client, "unknown")
    try:
        if not client.is_connected():
            await client.connect()
        if not await client.is_user_authorized():
            return False, "Не авторизован"
        me = await client.get_me()
        if not me or getattr(me, "restricted", False) or getattr(me, "deleted", False):
            return False, "Заморожен / удален"
        return True, me
    except (UserDeactivatedError, UserDeactivatedBanError, AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError) as e:
        return False, f"Бан: {e}"
    except Exception as e:
        err_str = str(e).lower()
        if "restricted" in err_str or "deactivated" in err_str:
            return False, f"Заморозка: {e}"
        return True, None

async def keep_alive():
    """Фоновый мониторинг сокетов и авто-выкидывание отлетевших аккаунтов"""
    while True:
        await asyncio.sleep(20)
        for c in list(clients):
            is_ok, info = await check_client_alive(c)
            if not is_ok:
                sess = client_sessions.get(c, "unknown")
                if c in clients:
                    clients.remove(c)
                purge_dead_account(sess, f"Отлетел в фоне: {info}")
                try:
                    await c.disconnect()
                except Exception:
                    pass

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

    accounts = load_accounts()
    if not accounts:
        print("[!] Список аккаунтов пуст.", flush=True)
        return

    print("[*] Сканирование аккаунтов на заморозку и слет сессий...", flush=True)

    for acc in accounts:
        session_name = acc["session"]
        session_path = f"/root/farm_panel/{session_name}"
        bind_ip = acc.get("bind_ip")
        device_info = get_device_fingerprint(session_name)

        kwargs = {
            "session": session_path,
            "api_id": acc["api_id"],
            "api_hash": acc["api_hash"],
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
                death_reason = "Сессия сброшена"
            else:
                me = await c.get_me()
                if not me:
                    death_reason = "Пустой профиль"
                elif getattr(me, "restricted", False):
                    death_reason = "Заморожен Telegram (restricted)"
                elif getattr(me, "deleted", False):
                    death_reason = "Аккаунт удален"
                else:
                    # Тестовый запрос к API диалогов для проверки на скрытый блок
                    await c.get_dialogs(limit=1)
                    is_alive = True
                    clients.append(c)
                    client_sessions[c] = session_name
                    print(f"[+] Сессия {session_name} онлайн: {me.first_name} ({acc.get('phone', '-')})", flush=True)
        except (UserDeactivatedError, UserDeactivatedBanError, AuthKeyUnregisteredError, SessionRevokedError, SessionExpiredError) as e:
            death_reason = f"Бан/деактивация: {e}"
        except Exception as e:
            err_str = str(e).lower()
            if "restricted" in err_str or "deactivated" in err_str or "banned" in err_str:
                death_reason = f"Заморозка/бан: {e}"
            else:
                print(f"[!] Временная ошибка сети у {session_name}: {e}", flush=True)
                is_alive = True
                clients.append(c)
                client_sessions[c] = session_name

        if not is_alive:
            try:
                await c.disconnect()
            except Exception:
                pass
            # Моментальное авто-удаление нерабочей сессии
            purge_dead_account(session_name, death_reason)

    print(f"\n[✓] Итого живых аккаунтов: {len(clients)}", flush=True)
    print(f"[✓] Канал перехвата: @{source_channel}", flush=True)
    print(f"[✓] Целевой бот: @{target_bot}", flush=True)

    if not clients:
        print("[!] Нет доступных аккаунтов для снайпинга.", flush=True)
        return

    print("[*] Кэшируем бота для быстрого залпа...", flush=True)
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
            if "*" in voucher_code:
                digit = solve_math_equation(raw_text)
                if digit is not None:
                    voucher_code = voucher_code.replace("*", digit)
                    print(f"[⚡] Решено: x = {digit}! Ваучер: {voucher_code}", flush=True)
                else:
                    print(f"[ERR] Не удалось решить пример для {voucher_code}. Пропуск.", flush=True)
                    return

            print(f"\n[!] ПОЙМАН ВАУЧЕР: {voucher_code}. Залп со всех {len(clients)} аккаунтов...", flush=True)
            await asyncio.gather(*(send_to_bot(client, target_bot, voucher_code) for client in list(clients)))

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
