import os
# Блокировка пулов потоков в C/C++ библиотеках (OpenMP, BLAS, MKL) строго до импорта numpy, cv2, onnxruntime
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"

import ast
import asyncio
import hashlib
import io
import json
import operator
import random
import re
import shutil
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    from PIL import Image, ImageOps, ImageEnhance, ImageFilter, ImageChops
except ImportError:
    Image = None
    ImageOps = None
    ImageEnhance = None
    ImageFilter = None
    ImageChops = None

try:
    import numpy as np
except ImportError:
    np = None

try:
    import cv2
except ImportError:
    cv2 = None

# =========================================================================
# ГЛОБАЛЬНАЯ ИНИЦИАЛИЗАЦИЯ И ПРОГРЕВ МОДЕЛИ RAPIDOCR (ONNX RUNTIME)
# Строгая оптимизация под 1 vCPU / 1 GB RAM: 1 поток, без троттлинга и угла текста
# =========================================================================
RAPID_OCR = None
try:
    from rapidocr_onnxruntime import RapidOCR
    print("[OCR-INIT] [*] Загрузка весов RapidOCR ONNX (1 vCPU Ultra-Mode)...", flush=True)
    _t_init = time.perf_counter()
    RAPID_OCR = RapidOCR(
        intra_op_num_threads=1,
        inter_op_num_threads=1,
        det_limit_side_len=480,
        det_limit_type="max",
        use_cls=False,
        use_angle_cls=False,
    )
    # Фиксация параметров детекции без апскейла
    if hasattr(RAPID_OCR, "text_det"):
        RAPID_OCR.text_det.limit_side_len = 480
        RAPID_OCR.text_det.limit_type = "max"

    # Однократный прогрев (warmup) модели при старте bot.py на тестовой матрице
    if np is not None:
        _dummy_mat = np.zeros((100, 100, 3), dtype=np.uint8)
        RAPID_OCR(_dummy_mat, use_cls=False)
    _t_warmup = (time.perf_counter() - _t_init) * 1000.0
    print(f"[OCR-INIT] [OK] RapidOCR ONNX успешно загружен и прогрет за {_t_warmup:.1f}мс (1 поток, det=480 max)!", flush=True)
except Exception as _e:
    print(f"[OCR-INIT] [ERR] Ошибка инициализации RapidOCR: {_e}", flush=True)
    import traceback
    traceback.print_exc()
    RAPID_OCR = None

from telethon import TelegramClient, events
from telethon.errors import (
    AuthKeyDuplicatedError,
    AuthKeyUnregisteredError,
    ChatWriteForbiddenError,
    FloodWaitError,
    PhoneNumberBannedError,
    SessionRevokedError,
    UserDeactivatedBanError,
    UserDeactivatedError,
    UserRestrictedError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import GetMessagesViewsRequest, SendReactionRequest
from telethon.tl.types import MessageEntityCode, MessageEntityPre, ReactionEmoji

BASE_DIR = "/root/farm_panel" if os.name != "nt" else os.path.dirname(os.path.abspath(__file__))
DATA_DIR = "/root/farm_data" if os.name != "nt" else os.path.join(BASE_DIR, "farm_data")
os.makedirs(BASE_DIR, exist_ok=True)
os.makedirs(DATA_DIR, exist_ok=True)

TRASH_DIR = os.path.join(DATA_DIR, "trash")
os.makedirs(TRASH_DIR, exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, "trash"), exist_ok=True)

VOUCHERS_HISTORY_FILE = os.path.join(DATA_DIR, "vouchers_history.json")
MOBILE_EVENTS_FILE = os.path.join(DATA_DIR, "mobile_events.json")
WARMUP_TRIGGER_FILE = os.path.join(DATA_DIR, "warmup_trigger.flag")

FATAL_ERROR_TYPES = (
    UserDeactivatedError,
    UserDeactivatedBanError,
    AuthKeyUnregisteredError,
    AuthKeyDuplicatedError,
    SessionRevokedError,
    PhoneNumberBannedError,
)

FATAL_ERROR_KEYWORDS = (
    "frozen",
    "deactivated",
    "banned",
    "auth_key_unregistered",
    "auth_key_duplicated",
    "session_revoked",
    "user_deactivated",
)

clients = []
client_sessions = {}
session_to_client = {}
client_meta = {}
target_peers = {}
processed_vouchers = set()
processed_msg_keys = set()
listener_clients = set()
current_source_channel = "vaycher_virtnumber"
current_target_bot = "Virtnumber_buy_bot"
warmup_in_progress = False
autowithdraw_locks = set()

# Расширенный пул реалистичных индивидуальных отпечатков устройств (24 профиля)
DEVICE_PROFILES = [
    {"device_model": "Google Pixel 9 Pro XL", "system_version": "Android 15", "app_version": "11.2.1"},
    {"device_model": "Google Pixel 9 Pro", "system_version": "Android 15", "app_version": "11.2.0"},
    {"device_model": "Google Pixel 8 Pro", "system_version": "Android 14", "app_version": "11.1.3"},
    {"device_model": "Google Pixel 8a", "system_version": "Android 14", "app_version": "11.0.5"},
    {"device_model": "Samsung Galaxy S24 Ultra", "system_version": "Android 14", "app_version": "11.2.1"},
    {"device_model": "Samsung Galaxy S24+", "system_version": "Android 14", "app_version": "11.1.8"},
    {"device_model": "Samsung Galaxy S23 Ultra", "system_version": "Android 14", "app_version": "10.14.5"},
    {"device_model": "Samsung Galaxy Z Fold6", "system_version": "Android 14", "app_version": "11.1.4"},
    {"device_model": "Samsung Galaxy A55 5G", "system_version": "Android 14", "app_version": "11.0.2"},
    {"device_model": "Samsung Galaxy A54", "system_version": "Android 13", "app_version": "10.12.0"},
    {"device_model": "iPhone 16 Pro Max", "system_version": "iOS 18.1", "app_version": "11.2"},
    {"device_model": "iPhone 16 Pro", "system_version": "iOS 18.0", "app_version": "11.1.2"},
    {"device_model": "iPhone 15 Pro Max", "system_version": "iOS 17.6", "app_version": "11.0.4"},
    {"device_model": "iPhone 15", "system_version": "iOS 17.5", "app_version": "10.14.1"},
    {"device_model": "Xiaomi 14 Ultra", "system_version": "Android 14", "app_version": "11.2.0"},
    {"device_model": "Xiaomi 14 Pro", "system_version": "Android 14", "app_version": "11.1.5"},
    {"device_model": "Xiaomi 13T Pro", "system_version": "Android 14", "app_version": "10.14.2"},
    {"device_model": "POCO F6 Pro", "system_version": "Android 14", "app_version": "11.0.8"},
    {"device_model": "OnePlus 12", "system_version": "Android 14", "app_version": "11.1.6"},
    {"device_model": "OnePlus 11 5G", "system_version": "Android 14", "app_version": "10.13.8"},
    {"device_model": "Nothing Phone (2)", "system_version": "Android 14", "app_version": "11.1.0"},
    {"device_model": "ASUS ROG Phone 8", "system_version": "Android 14", "app_version": "11.0.9"},
    {"device_model": "Desktop x64", "system_version": "Windows 11", "app_version": "5.6.3 x64"},
    {"device_model": "MacBook Pro M3", "system_version": "macOS 14.6", "app_version": "11.2"},
]


def _atomic_write_json(path: str, data):
    tmp_path = f"{path}.tmp.{os.getpid()}.{random.randint(1000, 9999)}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, path)
    except Exception as e:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        raise e


def push_mobile_event(event_type: str, title: str, body: str, badge: str = ""):
    events_list = []
    if os.path.exists(MOBILE_EVENTS_FILE):
        try:
            with open(MOBILE_EVENTS_FILE, "r", encoding="utf-8") as f:
                events_list = json.load(f)
                if not isinstance(events_list, list):
                    events_list = []
        except Exception:
            events_list = []

    ev = {
        "id": int(time.time() * 1000) + random.randint(1, 999),
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "type": event_type,
        "title": title,
        "body": body,
        "badge": badge,
    }
    events_list.insert(0, ev)
    events_list = events_list[:60]
    try:
        _atomic_write_json(MOBILE_EVENTS_FILE, events_list)
    except Exception:
        pass
    return ev


def get_device_fingerprint(session_name: str, acc: dict | None = None) -> dict:
    if acc and acc.get("device_model") and acc.get("system_version") and acc.get("app_version"):
        return {
            "device_model": acc["device_model"],
            "system_version": acc["system_version"],
            "app_version": acc["app_version"],
        }
    idx = int(hashlib.md5(str(session_name).strip().encode("utf-8")).hexdigest(), 16) % len(DEVICE_PROFILES)
    return dict(DEVICE_PROFILES[idx])


def is_fatal_account_error(exc: Exception) -> bool:
    if isinstance(exc, FATAL_ERROR_TYPES):
        return True
    err_msg = str(exc).lower()
    return any(k in err_msg for k in FATAL_ERROR_KEYWORDS)


def resolve_session_path(session_name: str) -> str:
    s_clean = re.sub(r"[^a-zA-Z0-9_\-\u0400-\u04FF]", "", str(session_name).strip())
    data_path = os.path.join(DATA_DIR, s_clean)
    base_path = os.path.join(BASE_DIR, s_clean)

    if os.path.exists(f"{data_path}.session"):
        return data_path
    if os.path.exists(f"{base_path}.session"):
        try:
            shutil.copy2(f"{base_path}.session", f"{data_path}.session")
            if os.path.exists(f"{base_path}.session-journal"):
                shutil.copy2(f"{base_path}.session-journal", f"{data_path}.session-journal")
            return data_path
        except Exception:
            return base_path
    return data_path


def load_settings() -> dict:
    default_settings = {
        "source_channel": "vaycher_virtnumber",
        "target_bot": "Virtnumber_buy_bot",
        "enable_smart_jitter": False,
        "jitter_min_ms": 20,
        "jitter_max_ms": 80,
        "min_withdraw_amount": 30.0,
        "auto_withdraw_enabled": True,
        "site_withdraw_login": "",
        "total_withdrawn_rub": 0.0,
        "total_withdrawn_count": 0,
        "enable_stealth_warmup": True,
        "warmup_channels": "telegram, durov",
        "last_warmup_at": "",
    }
    for p in [os.path.join(DATA_DIR, "config.json"), os.path.join(BASE_DIR, "config.json")]:
        if os.path.exists(p) and os.path.getsize(p) > 2:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        for k, v in default_settings.items():
                            data.setdefault(k, v)
                        return data
            except Exception:
                pass
    return default_settings


def save_settings_bot(settings: dict):
    for d in [DATA_DIR, BASE_DIR]:
        try:
            _atomic_write_json(os.path.join(d, "config.json"), settings)
        except Exception:
            pass


def load_accounts() -> list:
    for p in [os.path.join(DATA_DIR, "accounts.json"), os.path.join(BASE_DIR, "accounts.json")]:
        if os.path.exists(p) and os.path.getsize(p) >= 2:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    accs = json.load(f)
                    if isinstance(accs, list):
                        return accs
            except Exception:
                pass
    return []


def save_accounts(accounts: list):
    for d in [DATA_DIR, BASE_DIR]:
        try:
            _atomic_write_json(os.path.join(d, "accounts.json"), accounts)
        except Exception as e:
            print(f"[!] Ошибка сохранения accounts.json в {d}: {e}", flush=True)


def load_trash() -> list:
    for p in [os.path.join(DATA_DIR, "trash.json"), os.path.join(BASE_DIR, "trash.json")]:
        if os.path.exists(p) and os.path.getsize(p) >= 2:
            try:
                with open(p, "r", encoding="utf-8") as f:
                    trash = json.load(f)
                    if isinstance(trash, list):
                        return trash
            except Exception:
                pass
    return []


def save_trash(trash: list):
    for d in [DATA_DIR, BASE_DIR]:
        try:
            _atomic_write_json(os.path.join(d, "trash.json"), trash)
        except Exception as e:
            print(f"[!] Ошибка сохранения trash.json в {d}: {e}", flush=True)


# --- Парсер баланса и успешных активаций из ответов целевого бота ---
def parse_balance_from_text(txt: str) -> str | None:
    if not txt:
        return None
    clean = txt.replace("**", "").replace("__", "").replace("`", "")
    m = re.search(r"баланс[^\d\n]{0,25}([\d]+(?:[.,]\d+)?)", clean, re.IGNORECASE)
    if m:
        return f"{float(m.group(1).replace(',', '.')):.2f}"
    m2 = re.search(r"на\s+счету[^\d\n]{0,20}([\d]+(?:[.,]\d+)?)", clean, re.IGNORECASE)
    if m2:
        return f"{float(m2.group(1).replace(',', '.')):.2f}"
    m3 = re.search(r"([\d]+(?:[.,]\d+)?)\s*(?:₽|руб\.?|rub|р\b)", clean, re.IGNORECASE)
    if m3:
        return f"{float(m3.group(1).replace(',', '.')):.2f}"
    return None


def parse_bot_activation_response(txt: str) -> dict:
    """
    Анализирует ответ целевого бота после отправки промокода/ваучера.
    Возвращает dict:
      - status: 'activated' | 'rejected' | 'unknown'
      - amount: float (сумма начисления, если удалось распознать)
      - balance: str | None (новый баланс, если бот прислал его в том же сообщении)
      - summary: str (краткая выжимка ответа)
    """
    if not txt or not txt.strip():
        return {"status": "unknown", "amount": 0.0, "balance": None, "summary": ""}

    clean = re.sub(r"[*_`~]", "", txt).strip()
    low = clean.lower()

    reject_keywords = (
        "уже активирован",
        "уже был активирован",
        "уже использовали",
        "вы уже активировали",
        "не найден",
        "не существует",
        "исчерпан",
        "закончились активации",
        "активации закончились",
        "неверный",
        "некорректный",
        "неактивен",
        "просрочен",
        "ошибка",
        "лимит",
        "не удалось",
        "подождите",
        "доступен только",
        "не выполнили",
        "подпишитесь",
    )
    for rk in reject_keywords:
        if rk in low:
            first_line = clean.splitlines()[0][:80]
            return {"status": "rejected", "amount": 0.0, "balance": None, "summary": first_line}

    success_keywords = (
        "успешно активирован",
        "ваучер активирован",
        "промокод активирован",
        "чек активирован",
        "вы активировали",
        "успешная активация",
        "зачислено",
        "начислено",
        "получено",
        "вы получили",
        "баланс пополнен",
        "пополнение на",
        "активирован на сумму",
        "бонус зачислен",
    )
    is_success = any(sk in low for sk in success_keywords)
    if not is_success and ("успешно" in low and any(w in low for w in ("ваучер", "промокод", "бонус", "₽", "руб", "р."))):
        is_success = True

    if is_success:
        amount = 0.0
        # Ищем сумму начисления (например: "на 25 ₽", "+15.5 руб", "получили 50₽", "сумму 30 руб")
        amt_patterns = [
            r"(?:на\s+сумму|пополнен\s+на|получили|начислено|зачислено|активирован\s+на|\+)\s*([\d]+(?:[.,]\d+)?)\s*(?:₽|руб|rub|р\b)?",
            r"([\d]+(?:[.,]\d+)?)\s*(?:₽|руб\.?|rub)",
        ]
        for pat in amt_patterns:
            m = re.search(pat, clean, re.IGNORECASE)
            if m:
                try:
                    amount = float(m.group(1).replace(",", "."))
                    break
                except ValueError:
                    pass

        new_bal = None
        bal_match = re.search(r"(?:ваш\s+баланс|текущий\s+баланс|баланс\s*:)\s*([\d]+(?:[.,]\d+)?)", clean, re.IGNORECASE)
        if bal_match:
            try:
                new_bal = f"{float(bal_match.group(1).replace(',', '.')):.2f}"
            except ValueError:
                pass

        first_line = clean.splitlines()[0][:80]
        return {"status": "activated", "amount": amount, "balance": new_bal, "summary": first_line}

    return {"status": "unknown", "amount": 0.0, "balance": None, "summary": clean.splitlines()[0][:80] if clean else ""}


def record_voucher_history(
    code: str,
    solved_x: str | None,
    sent_count: int,
    elapsed_ms: float,
    activated_count: int = 0,
    earned_rub: float = 0.0,
    winners: list[str] | None = None,
    status: str = "pending",
    bot_reply_sample: str = "",
    source: str = "text",
    ocr_time_ms: float = 0.0,
):
    history = []
    if os.path.exists(VOUCHERS_HISTORY_FILE):
        try:
            with open(VOUCHERS_HISTORY_FILE, "r", encoding="utf-8") as f:
                history = json.load(f)
                if not isinstance(history, list):
                    history = []
        except Exception:
            history = []

    history.insert(0, {
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
        "solved_x": solved_x,
        "sent_count": sent_count,
        "elapsed_ms": round(elapsed_ms, 1),
        "ocr_time_ms": round(ocr_time_ms, 1) if ocr_time_ms > 0 else None,
        "activated_count": activated_count,
        "earned_rub": round(earned_rub, 2),
        "winners": winners or [],
        "status": status,
        "activation_status": status,
        "bot_reply_sample": bot_reply_sample,
        "bot_reply_preview": bot_reply_sample,
        "source": source,
    })
    history = history[:100]
    try:
        _atomic_write_json(VOUCHERS_HISTORY_FILE, history)
    except Exception:
        pass


def update_voucher_history_result(
    code: str,
    activated_count: int,
    earned_rub: float,
    winners: list[str],
    status: str,
    bot_reply_sample: str,
):
    if not os.path.exists(VOUCHERS_HISTORY_FILE):
        return
    try:
        with open(VOUCHERS_HISTORY_FILE, "r", encoding="utf-8") as f:
            history = json.load(f)
        if not isinstance(history, list):
            return
        for item in history:
            if item.get("code") == code:
                item["activated_count"] = activated_count
                item["earned_rub"] = round(earned_rub, 2)
                item["winners"] = winners
                item["status"] = status
                item["activation_status"] = status
                if bot_reply_sample:
                    item["bot_reply_sample"] = bot_reply_sample
                    item["bot_reply_preview"] = bot_reply_sample
                break
        _atomic_write_json(VOUCHERS_HISTORY_FILE, history)
    except Exception:
        pass


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
            for ext in [".session", ".session-journal", ".session-wal", ".session-shm"]:
                src = os.path.join(base, f"{s_target}{ext}")
                dst = os.path.join(t_dir, f"{s_target}{ext}")
                if os.path.exists(src):
                    try:
                        shutil.move(src, dst)
                    except Exception:
                        pass

        print(f"[КОРЗИНА] Сессия {s_target} перемещена: {reason}", flush=True)
    except Exception as e:
        print(f"[!] Ошибка перемещения в корзину {session_name}: {e}", flush=True)


# --- Безопасный вычислитель арифметических выражений (без eval) ---
_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _safe_eval_expr(expr: str) -> float:
    node = ast.parse(expr, mode="eval").body

    def _eval(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Num):
            return float(n.n)
        if isinstance(n, ast.BinOp) and type(n.op) in _SAFE_OPS:
            left_val = _eval(n.left)
            right_val = _eval(n.right)
            if abs(left_val) > 1e12 or abs(right_val) > 1e12:
                raise ValueError("Overflow")
            return _SAFE_OPS[type(n.op)](left_val, right_val)
        if isinstance(n, ast.UnaryOp) and type(n.op) in _SAFE_OPS:
            return _SAFE_OPS[type(n.op)](_eval(n.operand))
        raise ValueError("Unsupported AST node")

    return _eval(node)


def solve_math_equation(text: str) -> str | None:
    if not text:
        return None

    clean_text = text.replace("**", "").replace("__", "").replace("`", "")
    clean_text = re.sub(r"[хХXуУY\?]", "x", clean_text)

    for raw_line in clean_text.splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue

        if re.search(r"^[x*]\s*=\s*[x*\?]+$", line):
            continue

        if "x" not in line and "*" in line:
            line = re.sub(r"(?:^|(?<=[+\-/:÷=•·×(]))\s*\*\s*(?=$|[+\-/:÷=•·×)])", "x", line)

        if "x" not in line:
            continue

        parts = line.split("=")
        if len(parts) != 2:
            continue

        def parse_side(side: str) -> str:
            side = re.sub(r"[:÷⁄]", "/", side)
            side = re.sub(r"[−–—]", "-", side)
            side = re.sub(r"[•·⋅∙‧×✕✖]", "*", side)
            side = re.sub(r"x\s*[^0-9+\-*/()=]\s*(\d)", r"x*\1", side)
            side = re.sub(r"(\d)\s*[^0-9+\-*/()=]\s*x", r"\1*x", side)
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
                vl = _safe_eval_expr(t_left)
                vr = _safe_eval_expr(t_right)
                if abs(vl - vr) < 0.0001:
                    return str(digit)
            except ZeroDivisionError:
                continue
            except Exception:
                continue

    return None


CYRILLIC_HOMOGLYPHS = str.maketrans({
    "а": "a", "А": "A",
    "с": "c", "С": "C",
    "е": "e", "Е": "E",
    "о": "o", "О": "O",
    "р": "p", "Р": "P",
    "х": "x", "Х": "X",
    "у": "y", "У": "Y",
    "к": "k", "К": "K",
    "т": "t", "Т": "T",
    "м": "m", "М": "M",
    "б": "b", "Б": "B",
})

VOUCHER_REGEX = re.compile(
    r"\b(va[a-zA-Z0-9*?]{2,12}(?:-[a-zA-Z0-9*?]{2,12})+)\b",
    re.IGNORECASE
)

def heal_hex_segment(seg: str) -> str:
    if "*" in seg or "?" in seg:
        return seg.replace("?", "*")
    cand = seg.replace("o", "0").replace("O", "0").replace("l", "1").replace("I", "1")
    cand = cand.replace("G", "6").replace("g", "6").replace("S", "5").replace("s", "5")
    cand = cand.replace("B", "b").replace("D", "b")
    return cand.lower()

def clean_and_heal_ocr_voucher(text: str) -> str | None:
    if not text:
        return None

    # Шаг 1: Нормализация дефисов, тире, подчеркиваний и пробелов вокруг них
    t = re.sub(r"[\u2010-\u2015\u2212_~]", "-", text)
    t = re.sub(r"\s*-\s*", "-", t)

    # Шаг 2: Нормализация кириллических опечаток (например 'ва' вместо 'va')
    t = re.sub(r"\b[вВ][аАaA]", "va", t)
    t = t.translate(CYRILLIC_HOMOGLYPHS)

    # Шаг 3: Поиск по целевому regex (все ваучеры начинаются на va): r'\b(va[a-zA-Z0-9*?]{2,12}(?:-[a-zA-Z0-9*?]{2,12})+)\b'
    m = VOUCHER_REGEX.search(t)
    if m:
        cand = m.group(1)
        parts = cand.split("-")
        if len(parts) >= 2:
            p0 = "va" + heal_hex_segment(parts[0][2:])
            healed = [p0]
            for p in parts[1:]:
                healed.append(heal_hex_segment(p))
            # Если к последнему сегменту прилипли цифры таймстампа/хвоста (например 1b6b22:44 или b6b0)
            if len(healed[-1]) > 5 and len(healed[-2]) <= 5 and not ("*" in healed[-1]):
                healed[-1] = healed[-1][:len(healed[-2])]
            return "-".join(healed).replace("?", "*")

    # Шаг 4: Слитый средний или последний сегмент: 'va6f8b-59C01B6B0'
    m_fused = re.search(r"\bva([a-zA-Z0-9*?]{4,6})-([a-zA-Z0-9*?]{4})[1I\-]?([a-zA-Z0-9*?]{4})\b", t, re.IGNORECASE)
    if m_fused:
        p0 = heal_hex_segment(m_fused.group(1))
        p1 = heal_hex_segment(m_fused.group(2))
        p2 = heal_hex_segment(m_fused.group(3))
        return f"va{p0}-{p1}-{p2}".replace("?", "*")

    # Шаг 5: Разделители точки или пробелы вместо дефисов
    m_sep = re.search(r"\bva([a-zA-Z0-9*?]{2,12})[.\s]+([a-zA-Z0-9*?]{2,12})[.\s]+([a-zA-Z0-9*?]{2,12})\b", t, re.IGNORECASE)
    if m_sep:
        p0 = heal_hex_segment(m_sep.group(1))
        p1 = heal_hex_segment(m_sep.group(2))
        p2 = heal_hex_segment(m_sep.group(3))
        if len(p2) > 4 and len(p1) == 4 and not ("*" in p2):
            p2 = p2[:4]
        return f"va{p0}-{p1}-{p2}".replace("?", "*")

    # Шаг 6: Полностью слитый код без дефисов: 'va6f3b59c0b6b0'
    m_all_fused = re.search(r"\bva([0-9a-fA-F]{4,6})([0-9a-fA-F]{4})([0-9a-fA-F]{4})\b", t)
    if m_all_fused:
        p0 = heal_hex_segment(m_all_fused.group(1))
        p1 = heal_hex_segment(m_all_fused.group(2))
        p2 = heal_hex_segment(m_all_fused.group(3))
        return f"va{p0}-{p1}-{p2}"

    return None


def _sync_extract_voucher_from_image(img_input) -> tuple[str | None, str, dict]:
    """
    Сверхбыстрый чистый инференс RapidOCR ONNX без блокировки event loop.
    Pytesseract полностью удален. Ресайз до 960px + ранний выход при первом совпадении.
    Возвращает (voucher_code, recognized_text, benchmark_dict).
    """
    t_start = time.perf_counter()
    bench = {"t_resize": 0.0, "t_infer": 0.0, "t_regex": 0.0}

    # 1. Быстрое декодирование напрямую из памяти + ресайз OpenCV (<= 960px)
    t_prep_start = time.perf_counter()
    img_cv = None
    try:
        if isinstance(img_input, (bytes, bytearray)):
            if cv2 is not None and np is not None:
                nparr = np.frombuffer(img_input, np.uint8)
                img_cv = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            elif Image is not None:
                pil_im = Image.open(io.BytesIO(img_input)).convert("RGB")
                img_cv = np.array(pil_im)[:, :, ::-1] if np is not None else None
        elif isinstance(img_input, np.ndarray):
            img_cv = img_input
        elif Image is not None and isinstance(img_input, Image.Image):
            pil_im = img_input.convert("RGB")
            img_cv = np.array(pil_im)[:, :, ::-1] if np is not None else None
        elif isinstance(img_input, str) and os.path.isfile(img_input):
            if cv2 is not None:
                img_cv = cv2.imread(img_input)
            elif Image is not None:
                img_cv = np.array(Image.open(img_input).convert("RGB"))[:, :, ::-1] if np is not None else None
    except Exception as e:
        return None, f"img_decode_error: {e}", bench

    if img_cv is None:
        return None, "empty_or_corrupt_image", bench

    # Ресайз до максимальной стороны 480px (шрифт ваучера крупный и контрастный, на 480px читаемость 100%,
    # а время детекции на 1 vCPU падает с 900мс до ~350-450мс!)
    if cv2 is not None:
        h, w = img_cv.shape[:2]
        max_dim = max(h, w)
        if max_dim > 480:
            scale = 480.0 / float(max_dim)
            new_w = max(1, int(w * scale))
            new_h = max(1, int(h * scale))
            img_cv = cv2.resize(img_cv, (new_w, new_h), interpolation=cv2.INTER_AREA)

    bench["t_resize"] = round((time.perf_counter() - t_prep_start) * 1000.0, 1)

    # 2. Проверка глобального движка RapidOCR
    if RAPID_OCR is None or np is None:
        print("[OCR-WARN] [!] RapidOCR ONNX не загружен в память! Убедитесь что пакет установлен в venv.", flush=True)
        return None, "rapidocr_not_initialized", bench

    # 3. Чистый инференс детекции и распознавания с ранним выходом
    t_det_accum = 0.0
    t_rec_accum = 0.0
    t_regex_accum = 0.0
    all_lines = []

    try:
        t0 = time.perf_counter()
        dt_boxes, _ = RAPID_OCR.text_det(img_cv)
        t_det_accum = (time.perf_counter() - t0)

        if dt_boxes is not None and len(dt_boxes) > 0:
            # Расчет метрик и скоринга боксов:
            # Ваучер всегда имеет высокое соотношение сторон (>= 3.0) и большую ширину (bw)
            def _score_box(b):
                bw = float(np.linalg.norm(b[0] - b[1]))
                bh = float(max(np.linalg.norm(b[1] - b[2]), 1.0))
                ratio = bw / bh
                # Приоритет: ваучер (16 знаков) всегда длинный (ratio >= 2.5, bw >= 45, bh >= 10)
                is_voucher = 1 if (ratio >= 2.5 and bw >= 45 and bh >= 10) else 0
                return (is_voucher, bw, ratio)

            cand_boxes = []
            other_boxes = []
            for b in dt_boxes:
                bw = float(np.linalg.norm(b[0] - b[1]))
                bh = float(max(np.linalg.norm(b[1] - b[2]), 1.0))
                if (bw / bh) >= 1.5 and bw >= 25:
                    cand_boxes.append(b)
                else:
                    other_boxes.append(b)

            # Сортируем cand_boxes ПО УБЫВАНИЮ соотношения сторон и ширины!
            # Самый длинный горизонтальный бокс (сам ваучер) идет ПЕРВЫМ (#0) в text_rec,
            # благодаря чему text_rec отрабатывает всего 1 раз вместо 3-4 раз!
            cand_boxes.sort(key=_score_box, reverse=True)
            other_boxes.sort(key=_score_box, reverse=True)

            eval_boxes = cand_boxes + other_boxes
            crops = RAPID_OCR.get_crop_img_list(img_cv, eval_boxes)

            # Жадный прогон: распознаем боксы по очереди и выходим при первой находке
            for crop in crops:
                t_rec0 = time.perf_counter()
                rec_res, _ = RAPID_OCR.text_rec([crop])
                t_rec_accum += (time.perf_counter() - t_rec0)

                if rec_res and rec_res[0]:
                    line_txt = rec_res[0][0].strip()
                    if line_txt:
                        all_lines.append(line_txt)

                        t_reg0 = time.perf_counter()
                        found_code = clean_and_heal_ocr_voucher(line_txt)
                        t_regex_accum += (time.perf_counter() - t_reg0)

                        if found_code:
                            bench["t_det"] = round(t_det_accum * 1000.0, 1)
                            bench["t_rec"] = round(t_rec_accum * 1000.0, 1)
                            bench["t_infer"] = round((t_det_accum + t_rec_accum) * 1000.0, 1)
                            bench["t_regex"] = round(t_regex_accum * 1000.0, 1)
                            return found_code, line_txt, bench

            # Если ваучер разбит на несколько строк
            combined = " ".join(all_lines)
            t_reg0 = time.perf_counter()
            found_code = clean_and_heal_ocr_voucher(combined)
            t_regex_accum += (time.perf_counter() - t_reg0)

            bench["t_det"] = round(t_det_accum * 1000.0, 1)
            bench["t_rec"] = round(t_rec_accum * 1000.0, 1)
            bench["t_infer"] = round((t_det_accum + t_rec_accum) * 1000.0, 1)
            bench["t_regex"] = round(t_regex_accum * 1000.0, 1)
            if found_code:
                return found_code, combined, bench
            return None, combined, bench

    except Exception as e:
        print(f"[OCR-INFER-ERR] Ошибка инференса: {e}", flush=True)

    bench["t_det"] = round(t_det_accum * 1000.0, 1)
    bench["t_rec"] = round(t_rec_accum * 1000.0, 1)
    bench["t_infer"] = round((t_det_accum + t_rec_accum) * 1000.0, 1)
    bench["t_regex"] = round(t_regex_accum * 1000.0, 1)
    return None, "\n".join(all_lines), bench


async def async_extract_voucher_from_image(img_input) -> tuple[str | None, str, dict]:
    """
    Асинхронный запуск OCR строго в отдельном рабочем потоке.
    НИКОГДА не блокирует Telethon asyncio event loop!
    Возвращает (voucher_code, raw_text, bench_dict).
    """
    return await asyncio.to_thread(_sync_extract_voucher_from_image, img_input)


def extract_voucher_from_image(img_input) -> tuple[str | None, str]:
    """
    Синхронная совместимость для web.py (тестер картинок в веб-панели).
    Возвращает (voucher_code, raw_text).
    """
    code, raw, _ = _sync_extract_voucher_from_image(img_input)
    return code, raw


def extract_voucher_from_message(raw_text: str, entities=None) -> str | None:
    voucher_code = None
    if raw_text:
        match = re.search(
            r"va[a-zA-Z0-9*?]{2,12}-[a-zA-Z0-9*?]{2,12}-[a-zA-Z0-9*?]{2,12}",
            raw_text,
            re.IGNORECASE,
        )
        if match:
            voucher_code = match.group(0).strip().replace("?", "*")
        else:
            voucher_code = clean_and_heal_ocr_voucher(raw_text)

    if not voucher_code and entities and raw_text:
        encoded = raw_text.encode("utf-16-le")
        for ent in entities:
            if isinstance(ent, (MessageEntityCode, MessageEntityPre)):
                sub = encoded[ent.offset * 2 : (ent.offset + ent.length) * 2]
                extracted = sub.decode("utf-16-le", errors="ignore").strip("`'\" \n\r\t“”«»")
                if "-" in extracted and len(extracted) >= 6:
                    voucher_code = clean_and_heal_ocr_voucher(extracted) or extracted.replace("?", "*")
                    break

    return voucher_code


async def init_client_target(client: TelegramClient, target_bot: str, source_channel: str = ""):
    sess_name = client_sessions.get(client, "unknown")
    try:
        peer = await client.get_input_entity(target_bot)
        target_peers[client] = peer
    except Exception as e:
        print(f"[!] [{sess_name}] Не удалось закэшировать бота @{target_bot}: {e}", flush=True)

    if source_channel and client in listener_clients:
        try:
            ch_entity = await client.get_input_entity(source_channel)
            try:
                await client(JoinChannelRequest(ch_entity))
            except Exception:
                pass

            # Прогрев MTProto Media Sender для DC канала (авторизация на медиа-DC)
            # Устраняет холодный старт 1.5с при первом скачивании фото ваучера
            try:
                recent_msgs = await client.get_messages(ch_entity, limit=4)
                for rm in recent_msgs:
                    if rm.photo or rm.document:
                        await client.download_media(rm, file=bytes, thumb="s")
                        print(f"[WARMUP-DC] [{sess_name}] Медиа-соединение к DC канала прогрето и авторизовано!", flush=True)
                        break
            except Exception:
                pass
        except Exception as e:
            print(f"[WARN] [{sess_name}] Канал @{source_channel} пока недоступен: {e}", flush=True)


async def send_to_bot(client: TelegramClient, target_bot: str, code: str, delay_ms: float = 0):
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)

    sess_name = client_sessions.get(client, "unknown")
    meta_label = client_meta.get(client, sess_name)
    t0 = time.perf_counter()
    try:
        peer = target_peers.get(client, target_bot)
        sent_msg = await client.send_message(peer, code)
        rpc_ms = int((time.perf_counter() - t0) * 1000)
        print(
            f"[OK] {meta_label} -> {code} (джиттер: {int(delay_ms)}мс, RPC: {rpc_ms}мс)",
            flush=True,
        )
        return (client, getattr(sent_msg, "id", 0), True)
    except FloodWaitError as e:
        print(f"[WARN] FloodWait на {sess_name}: подождите {e.seconds} сек.", flush=True)
        return (client, 0, False)
    except (UserRestrictedError, ChatWriteForbiddenError) as e:
        print(f"[ERR] Ограничение отправки на {sess_name}: {e}", flush=True)
        return (client, 0, False)
    except Exception as e:
        err_msg = str(e)
        print(f"[ERR] Ошибка отправки на {sess_name}: {err_msg}", flush=True)
        if is_fatal_account_error(e):
            _unregister_client(client)
            move_to_trash(sess_name, f"Слетел при отправке: {err_msg}")
        return (client, 0, False)


# --- Авто-вывод с конкретной суммы на активном клиенте ---
async def auto_withdraw_single_client(
    client: TelegramClient,
    sess_name: str,
    target_bot: str,
    site_login: str,
    min_withdraw: float,
) -> bool:
    if not site_login or sess_name in autowithdraw_locks:
        return False
    autowithdraw_locks.add(sess_name)
    try:
        peer = target_peers.get(client) or await client.get_input_entity(target_bot)
        cmd = await client.send_message(peer, "/balance")

        balance_msg = None
        current_bal = None
        for _ in range(16):
            await asyncio.sleep(0.15)
            msgs = await client.get_messages(peer, limit=4)
            for m in msgs:
                if m.id > cmd.id and not m.out:
                    b_str = parse_balance_from_text(m.text or "")
                    if b_str is not None:
                        try:
                            current_bal = float(b_str)
                        except ValueError:
                            pass
                    if m.buttons:
                        for row in m.buttons:
                            for btn in row:
                                if "перенести" in (btn.text or "").lower():
                                    balance_msg = m
                                    break
                if balance_msg:
                    break
            if balance_msg:
                break

        if current_bal is not None and current_bal < min_withdraw:
            return False
        if not balance_msg:
            return False

        clicked_transfer = False
        for row_idx, row in enumerate(balance_msg.buttons):
            for col_idx, btn in enumerate(row):
                if "перенести" in (btn.text or "").lower():
                    await balance_msg.click(row_idx, col_idx)
                    clicked_transfer = True
                    break
            if clicked_transfer:
                break

        if not clicked_transfer:
            return False

        prompt_ready = False
        for _ in range(16):
            await asyncio.sleep(0.15)
            msgs = await client.get_messages(peer, limit=4)
            for m in msgs:
                if m.out or m.id < balance_msg.id:
                    continue
                txt_low = (m.text or "").lower()
                if any(w in txt_low for w in ["минимальн", "недостаточно", "ошибка", "недоступ"]):
                    return False
                if m.id > balance_msg.id or m.edit_date:
                    if any(w in txt_low for w in ["введите id", "введите логин", "укажите id", "id пользователя"]):
                        prompt_ready = True
                        break
            if prompt_ready:
                break

        if not prompt_ready:
            return False

        sent_login = await client.send_message(peer, site_login)
        confirm_msg = None
        for _ in range(16):
            await asyncio.sleep(0.15)
            msgs = await client.get_messages(peer, limit=4)
            for m in msgs:
                if m.id > sent_login.id and not m.out and m.buttons:
                    for row in m.buttons:
                        for btn in row:
                            if "подтвердить" in (btn.text or "").lower():
                                confirm_msg = m
                                break
                if confirm_msg:
                    break
            if confirm_msg:
                break

        if not confirm_msg:
            return False

        for row_idx, row in enumerate(confirm_msg.buttons):
            for col_idx, btn in enumerate(row):
                if "подтвердить" in (btn.text or "").lower():
                    await confirm_msg.click(row_idx, col_idx)
                    break

        withdrawn_val = current_bal if current_bal is not None else min_withdraw
        print(
            f"[💸 АВТО-ВЫВОД] [{sess_name}] Успешно выведено {withdrawn_val:.2f} ₽ на сайт (ID: {site_login})!",
            flush=True,
        )

        # Обновляем баланс аккаунта в accounts.json и счетчик выводов в config.json
        accs = load_accounts()
        for a in accs:
            if str(a.get("session", "")).strip() == sess_name:
                a["balance"] = "0.00"
                a["last_withdrawn_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
                break
        save_accounts(accs)

        settings = load_settings()
        settings["total_withdrawn_rub"] = round(float(settings.get("total_withdrawn_rub", 0.0)) + withdrawn_val, 2)
        settings["total_withdrawn_count"] = int(settings.get("total_withdrawn_count", 0)) + 1
        save_settings_bot(settings)

        push_mobile_event(
            "auto_withdraw",
            f"💸 Авто-вывод: {withdrawn_val:.2f} ₽",
            f"Аккаунт {sess_name} автоматически вывел {withdrawn_val:.2f} ₽ на ID {site_login}",
            badge=f"{withdrawn_val:.0f}₽",
        )
        return True
    except Exception as e:
        print(f"[WARN] [АВТО-ВЫВОД] Ошибка на {sess_name}: {e}", flush=True)
        return False
    finally:
        autowithdraw_locks.discard(sess_name)


# --- Проверка ответов целевого бота после залпа ваучера ---
async def inspect_post_shot_responses(
    voucher_code: str,
    shot_records: list[tuple[TelegramClient, int, bool]],
    target_bot: str,
):
    await asyncio.sleep(1.1)
    settings = load_settings()
    auto_withdraw = bool(settings.get("auto_withdraw_enabled", True))
    min_withdraw = float(settings.get("min_withdraw_amount", 30.0))
    site_login = str(settings.get("site_withdraw_login", "") or "").strip()

    accs = load_accounts()
    accs_map = {str(a.get("session", "")).strip(): a for a in accs}
    accounts_modified = False

    winners = []
    total_earned = 0.0
    activated_count = 0
    sample_reply = ""

    for client, sent_msg_id, was_ok in shot_records:
        if not was_ok or client not in clients:
            continue
        sess_name = client_sessions.get(client, "unknown")
        acc_obj = accs_map.get(sess_name)
        if not acc_obj:
            continue

        try:
            peer = target_peers.get(client) or await client.get_input_entity(target_bot)
            msgs = await client.get_messages(peer, limit=4)
            bot_replies = [m for m in msgs if m and not m.out and (sent_msg_id == 0 or m.id > sent_msg_id)]
            if not bot_replies:
                continue

            latest_reply = bot_replies[0]
            reply_text = latest_reply.text or ""
            parsed = parse_bot_activation_response(reply_text)
            if not sample_reply and parsed.get("summary"):
                sample_reply = parsed["summary"]

            if parsed["status"] == "activated":
                activated_count += 1
                old_bal = 0.0
                try:
                    old_bal = float(str(acc_obj.get("balance", "0")).replace(",", "."))
                except ValueError:
                    old_bal = 0.0

                won_amt = float(parsed.get("amount") or 0.0)
                new_bal_str = parsed.get("balance")

                # Если в ответе об активации нет точного нового баланса — запрашиваем /balance
                if new_bal_str is None:
                    cmd_b = await client.send_message(peer, "/balance")
                    for _ in range(8):
                        await asyncio.sleep(0.35)
                        b_msgs = await client.get_messages(peer, limit=3)
                        for bm in b_msgs:
                            if bm and not bm.out and bm.id > cmd_b.id:
                                nb = parse_balance_from_text(bm.text or "")
                                if nb is not None:
                                    new_bal_str = nb
                                    break
                        if new_bal_str is not None:
                            break

                new_bal = old_bal + won_amt
                if new_bal_str is not None:
                    try:
                        new_bal = float(new_bal_str)
                        if won_amt <= 0 and new_bal > old_bal:
                            won_amt = round(new_bal - old_bal, 2)
                    except ValueError:
                        pass

                total_earned += won_amt
                acc_obj["balance"] = f"{new_bal:.2f}"
                acc_obj["activated_count"] = int(acc_obj.get("activated_count", 0)) + 1
                acc_obj["earned_total"] = round(float(acc_obj.get("earned_total", 0.0)) + won_amt, 2)
                acc_obj["last_checked"] = datetime.now().strftime("%H:%M:%S")
                accounts_modified = True

                win_label = f"{sess_name} (+{won_amt:.2f} ₽)" if won_amt > 0 else f"{sess_name} (Баланс: {new_bal:.2f} ₽)"
                winners.append(win_label)
                print(
                    f"[🎯 АКТИВИРОВАН] [{sess_name}] Код {voucher_code} успешно активирован! Начислено: +{won_amt:.2f} ₽ | Баланс: {new_bal:.2f} ₽",
                    flush=True,
                )

                # Если включен авто-вывод и баланс достиг порога — сразу выводим!
                if auto_withdraw and site_login and new_bal >= min_withdraw:
                    asyncio.create_task(
                        auto_withdraw_single_client(client, sess_name, target_bot, site_login, min_withdraw)
                    )

        except Exception as e:
            print(f"[WARN] Ошибка проверки ответа бота на {sess_name}: {e}", flush=True)

    if accounts_modified:
        save_accounts(list(accs_map.values()))

    final_status = "activated" if activated_count > 0 else "rejected"
    update_voucher_history_result(
        code=voucher_code,
        activated_count=activated_count,
        earned_rub=total_earned,
        winners=winners,
        status=final_status,
        bot_reply_sample=sample_reply,
    )

    if activated_count > 0:
        push_mobile_event(
            "voucher_hit",
            f"🎯 Пойман ваучер +{total_earned:.2f} ₽!",
            f"Код {voucher_code} активирован ({activated_count} акк.): {', '.join(winners)}",
            badge=f"+{total_earned:.0f}₽",
        )
    else:
        print(
            f"[*] [ОТВЕТ БОТА] По коду {voucher_code}: 0 успешных активаций ({sample_reply or 'уже активирован / мимо'})",
            flush=True,
        )


def _unregister_client(c: TelegramClient):
    sess_name = client_sessions.pop(c, None)
    if sess_name:
        session_to_client.pop(sess_name, None)
    if c in clients:
        clients.remove(c)
    listener_clients.discard(c)
    client_meta.pop(c, None)
    target_peers.pop(c, None)


# --- БЛОК 2.1: Фоновый «Стелс-прогрев» (Имитация живого пользователя) ---
def parse_warmup_channels(raw_channels: str) -> list[str]:
    if not raw_channels:
        return ["telegram", "durov"]
    items = [c.strip().lstrip("@").replace("https://t.me/", "") for c in re.split(r"[,;\s]+", raw_channels) if c.strip()]
    return items if items else ["telegram", "durov"]


async def warmup_single_client(client: TelegramClient, channels: list[str]) -> bool:
    sess_name = client_sessions.get(client, "unknown")
    if not channels:
        channels = ["telegram", "durov"]
    target_ch = random.choice(channels)
    try:
        entity = await client.get_input_entity(target_ch)
        msgs = await client.get_messages(entity, limit=5)
        valid_msgs = [m for m in msgs if m and getattr(m, "id", None)]
        if not valid_msgs:
            return False

        await client.send_read_acknowledge(entity, max_id=valid_msgs[0].id)
        try:
            await client(
                GetMessagesViewsRequest(
                    peer=entity,
                    id=[m.id for m in valid_msgs[:3]],
                    increment=True,
                )
            )
        except Exception:
            pass

        reacted_info = ""
        if random.random() < 0.55:
            chosen_msg = random.choice(valid_msgs[:3])
            emoji = random.choice(["👍", "🔥", "❤️"])
            try:
                await client(
                    SendReactionRequest(
                        peer=entity,
                        msg_id=chosen_msg.id,
                        reaction=[ReactionEmoji(emoticon=emoji)],
                    )
                )
                reacted_info = f" + реакция {emoji}"
            except Exception:
                pass

        print(
            f"[OK] [СТЕЛС-ПРОГРЕВ] {sess_name}: прочитана лента @{target_ch} ({len(valid_msgs)} постов){reacted_info}",
            flush=True,
        )
        return True
    except Exception as e:
        print(f"[WARN] [СТЕЛС-ПРОГРЕВ] {sess_name} (@{target_ch}): {e}", flush=True)
        return False


async def run_stealth_warmup_pass(reason: str = "плановый"):
    global warmup_in_progress
    if warmup_in_progress:
        return
    warmup_in_progress = True
    try:
        settings = load_settings()
        channels = parse_warmup_channels(settings.get("warmup_channels", "telegram, durov"))
        active_list = list(clients)
        if not active_list:
            print("[WARN] [СТЕЛС-ПРОГРЕВ] Нет активных аккаунтов для прогрева.", flush=True)
            return

        print(
            f"[*] [СТЕЛС-ПРОГРЕВ] Старт ({reason}) для {len(active_list)} аккаунтов | Каналы: {', '.join('@' + c for c in channels)}",
            flush=True,
        )
        ok_total = 0
        for idx, c in enumerate(active_list):
            if idx > 0:
                await asyncio.sleep(random.uniform(1.2, 3.0))
            if await warmup_single_client(c, channels):
                ok_total += 1

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        settings = load_settings()
        settings["last_warmup_at"] = now_str
        save_settings_bot(settings)
        print(f"[✓] [СТЕЛС-ПРОГРЕВ] Завершен успешно ({ok_total}/{len(active_list)} акк.)", flush=True)
    finally:
        warmup_in_progress = False


# =========================================================================
# РАДАР ГОТОВНОСТИ К ВАУЧЕРУ (ДЕТЕКЦИЯ АНОНСОВ И ОБРАТНОГО ОТСЧЕТА 3.. 2.. 1)
# =========================================================================
last_teaser_info = {"text": "", "time": 0.0}

def detect_voucher_teaser(raw_text: str) -> dict | None:
    if not raw_text:
        return None
    text_clean = raw_text.strip()
    if not text_clean:
        return None

    # 1. Обратный отсчет (3, 2, 1, 5, 4, 10, "3..", "2!", "1 🔥")
    m_count = re.match(r"^(?:[🔥⚡⏳⏱️💥⚠️🔔🚨\s]*)([1-9]|10)(?:[\.\!\?\s🔥⚡⏳⏱️💥⚠️🔔🚨]*)$", text_clean)
    if m_count:
        num = m_count.group(1)
        return {
            "type": "countdown",
            "num": num,
            "title": f"⏱️ ОБРАТНЫЙ ОТСЧЕТ: {num}!",
            "body": f"В канале раздачи начался отсчет ({text_clean})! Ваучер вылетит с секунды на секунду!",
            "badge": "Отсчет"
        }

    lower = text_clean.lower()

    # 2. Анонс скорой публикации ваучера («Ваучер будет опубликован в течение 5-ти минут!!!»)
    is_drop_soon = any(k in lower for k in [
        "ваучер будет",
        "ваучер опублик",
        "будет опубликован",
        "в течение",
        "через 5",
        "через пару",
        "через 1",
        "через 2",
        "через 3",
        "через 4",
        "через 10",
        "скоро ваучер",
        "ваучер скоро",
        "готовьтесь",
        "готовимся",
        "минут ваучер",
        "ваучер через",
        "секунд ваучер",
        "раздача через",
        "начинаем раздач",
        "минуту ваучер",
        "раздача ваучер",
    ])
    if is_drop_soon:
        snippet = text_clean.replace("\n", " ")[:140]
        return {
            "type": "drop_soon",
            "title": "⚡ СКОРО ВАУЧЕР В КАНАЛЕ!",
            "body": snippet,
            "badge": "Скоро дроп"
        }

    # 3. Вопрос/голосование («Делаем сегодня ваучер?», «набираем 70 реакций»)
    is_teaser_poll = any(k in lower for k in [
        "делаем сегодня ваучер",
        "делаем ваучер",
        "набираем",
        "реакций на этом посте",
        "реакции на этот пост",
        "хотите ваучер",
        "раздать ваучер",
    ])
    if is_teaser_poll:
        snippet = text_clean.replace("\n", " ")[:140]
        return {
            "type": "teaser_poll",
            "title": "⚡ АНОНС В КАНАЛЕ ВАУЧЕРОВ",
            "body": snippet,
            "badge": "Анонс"
        }

    return None


async def fast_download_media_bytes(msg) -> tuple[bytes | None, float]:
    """
    Сверхбыстрое скачивание превью изображения (30-60 КБ вместо 3-5 МБ).
    Снижает время скачивания (download) с 2238мс до 80-180мс!
    Возвращает (photo_bytes, size_kb).
    """
    # 1. Если это Photo (стандартное сжатое фото в Telegram)
    if getattr(msg, "photo", None):
        sizes = getattr(msg.photo, "sizes", [])
        # Отбираем только реальные размеры изображений (исключая Stripped/Path/Video)
        img_sizes = [
            s for s in sizes 
            if getattr(s, "type", "") in ("x", "m", "y", "s") or hasattr(s, "w")
        ]
        
        # Приоритет: 1) объект размера 'x' (~800px), 2) предпоследний размер (перед 4K оригиналом), 3) 'm' (320px)
        target_thumb = None
        for s in img_sizes:
            if getattr(s, "type", "") == "x":
                target_thumb = s
                break
        if not target_thumb and len(img_sizes) > 1:
            target_thumb = img_sizes[-2]
        elif not target_thumb and img_sizes:
            target_thumb = img_sizes[-1]

        if target_thumb is not None:
            try:
                data = await msg.download_media(file=bytes, thumb=target_thumb)
                if data and len(data) > 300:
                    return data, round(len(data) / 1024.0, 1)
            except Exception:
                pass

        # Дополнительные попытки по стандартным ключам
        for thumb_key in ("x", -2, "m", -1):
            try:
                data = await msg.download_media(file=bytes, thumb=thumb_key)
                if data and len(data) > 300:
                    return data, round(len(data) / 1024.0, 1)
            except Exception:
                pass

    # 2. Если это Document (файл-картинка) со встроенной миниатюрой
    doc = getattr(msg, "document", None)
    if doc and getattr(doc, "thumbs", None):
        for thumb_key in (-1, "x", "m"):
            try:
                data = await msg.download_media(file=bytes, thumb=thumb_key)
                if data and len(data) > 300:
                    return data, round(len(data) / 1024.0, 1)
            except Exception:
                pass

    # 3. Резервный вариант: прямое скачивание исходника в память
    try:
        data = await msg.download_media(file=bytes)
        if data:
            return data, round(len(data) / 1024.0, 1)
    except Exception:
        pass
    return None, 0.0


async def handle_new_message(event):
    msg = event.message
    if not msg:
        return

    # Защита от дублей и параллельного OCR при нескольких слушателях (listener_clients)
    # Если 2-3 слушателя получают один и тот же пост одновременно, обрабатывает только ПЕРВЫЙ!
    msg_key = (getattr(event, "chat_id", None) or getattr(msg, "chat_id", None), msg.id)
    if msg_key in processed_msg_keys:
        return
    processed_msg_keys.add(msg_key)
    if len(processed_msg_keys) > 1000:
        processed_msg_keys.clear()
        processed_msg_keys.add(msg_key)

    raw_text = msg.text or msg.message or ""
    voucher_code = extract_voucher_from_message(raw_text, msg.entities)
    ocr_used = False
    ocr_time_ms = 0.0

    # Если в тексте ваучер не найден, проверяем прикрепленное фото или изображение
    has_photo = bool(msg.photo or (msg.file and msg.file.mime_type and msg.file.mime_type.startswith("image/")))
    if not voucher_code and has_photo:
        t_all_start = time.perf_counter()
        t_download = 0.0
        size_kb = 0.0
        try:
            # 1. Сверхбыстрое скачивание превью в память (80-150мс вместо 2.3с)
            t0 = time.perf_counter()
            photo_bytes, size_kb = await fast_download_media_bytes(msg)
            t_download = (time.perf_counter() - t0) * 1000.0

            if photo_bytes:
                # 2. Асинхронный запуск RapidOCR в отдельном потоке (без блокировки event loop)
                ocr_code, ocr_raw_text, bench = await async_extract_voucher_from_image(photo_bytes)
                t_total = (time.perf_counter() - t_all_start) * 1000.0
                t_resize = bench.get("t_resize", 0.0)
                t_infer = bench.get("t_infer", 0.0)
                t_det = bench.get("t_det", 0.0)
                t_rec = bench.get("t_rec", 0.0)
                t_regex = bench.get("t_regex", 0.0)

                # Детализированный бенчмарк в логах
                print(
                    f"[OCR-BENCH] Всего: {t_total:.1f}мс | "
                    f"download: {t_download:.1f}мс ({size_kb:.1f}КБ) | "
                    f"cv_prep: {t_resize:.1f}мс | "
                    f"infer: {t_infer:.1f}мс (det: {t_det:.1f}мс, rec: {t_rec:.1f}мс) | "
                    f"regex: {t_regex:.1f}мс",
                    flush=True
                )

                if ocr_code:
                    voucher_code = ocr_code
                    ocr_used = True
                    ocr_time_ms = t_total
                    raw_text = f"{raw_text}\n{ocr_raw_text}".strip()
                    print(f"[OCR] 🎯 Ваучер успешно распознан из фото за {t_total:.1f}мс: {voucher_code}", flush=True)
                else:
                    print(f"[OCR] Фото проверено за {t_total:.1f}мс, ваучер не найден", flush=True)
        except Exception as e:
            print(f"[OCR-ERR] Ошибка обработки фото: {e}", flush=True)
            import traceback
            traceback.print_exc()

    if not voucher_code:
        settings = load_settings()
        if settings.get("radar_teaser_enabled", True):
            teaser = detect_voucher_teaser(raw_text)
            if teaser:
                now = time.time()
                clean_snippet = teaser["body"][:60]
                if clean_snippet != last_teaser_info.get("text") or (now - last_teaser_info.get("time", 0)) > 10:
                    last_teaser_info["text"] = clean_snippet
                    last_teaser_info["time"] = now

                    print(f"[РАДАР] {teaser['title']}: {teaser['body']}", flush=True)

                    push_mobile_event(
                        event_type=teaser["type"],
                        title=teaser["title"],
                        body=teaser["body"],
                        badge=teaser["badge"]
                    )

                    # Если начался обратный отсчет (3, 2, 1) — мгновенно прогреваем сокеты сессий
                    if teaser["type"] == "countdown":
                        for c in list(clients)[:5]:
                            try:
                                asyncio.create_task(c.get_me())
                            except Exception:
                                pass
        return

    solved_digit = None
    if "*" in voucher_code:
        solved_digit = solve_math_equation(raw_text)
        if solved_digit is not None:
            voucher_code = voucher_code.replace("*", solved_digit)
        else:
            print(f"[ERR] Не удалось решить уравнение для {voucher_code}. Пропуск.", flush=True)
            return

    if voucher_code in processed_vouchers:
        return
    processed_vouchers.add(voucher_code)
    if len(processed_vouchers) > 500:
        processed_vouchers.clear()
        processed_vouchers.add(voucher_code)

    settings = load_settings()
    target_bot = settings.get("target_bot", "Virtnumber_buy_bot").lstrip("@").strip()
    enable_jitter = settings.get("enable_smart_jitter", False)
    jitter_min = float(settings.get("jitter_min_ms", 20))
    jitter_max = float(settings.get("jitter_max_ms", 80))

    if solved_digit is not None:
        print(f"[SOLVED] Капча решена: x = {solved_digit} -> Код: {voucher_code}", flush=True)

    active_snapshot = list(clients)
    source_tag = f" (ИЗ ФОТО OCR за {ocr_time_ms:.1f}мс)" if ocr_used else ""
    print(f"[!] ПОЙМАН ВАУЧЕР{source_tag}: {voucher_code} | Залп из {len(active_snapshot)} аккаунтов...", flush=True)

    t_start = time.perf_counter()
    tasks = []
    for idx, client in enumerate(active_snapshot):
        delay = 0.0
        if enable_jitter and idx > 0:
            delay = random.uniform(jitter_min, max(jitter_min, jitter_max)) * idx
        tasks.append(send_to_bot(client, target_bot, voucher_code, delay))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    shot_records = [r for r in results if isinstance(r, tuple)]
    ok_count = sum(1 for _, _, ok in shot_records if ok)
    elapsed_ms = (time.perf_counter() - t_start) * 1000
    record_voucher_history(
        code=voucher_code,
        solved_x=solved_digit,
        sent_count=ok_count,
        elapsed_ms=elapsed_ms,
        source="photo_ocr" if ocr_used else "text",
        ocr_time_ms=ocr_time_ms if ocr_used else 0.0,
    )

    # Асинхронно проверяем ответы целевого бота (кто забрал ваучер, сколько рублей начислено и нужен ли авто-вывод)
    if ok_count > 0:
        asyncio.create_task(inspect_post_shot_responses(voucher_code, shot_records, target_bot))


async def connect_single_account(acc: dict, source_channel: str, target_bot: str) -> TelegramClient | None:
    session_name = str(acc.get("session", "")).strip()
    if not session_name or session_name in session_to_client:
        return session_to_client.get(session_name)

    session_path = resolve_session_path(session_name)
    if not os.path.exists(f"{session_path}.session"):
        return None

    bind_ip = acc.get("bind_ip")
    device_info = get_device_fingerprint(session_name, acc)

    kwargs = {
        "session": session_path,
        "api_id": int(acc.get("api_id", 2040)),
        "api_hash": str(acc.get("api_hash", "b18441a1ff607e10a989891a5462e627")),
        "device_model": device_info["device_model"],
        "system_version": device_info["system_version"],
        "app_version": device_info["app_version"],
        "lang_code": "ru",
        "system_lang_code": "ru-RU",
        "auto_reconnect": True,
        "connection_retries": 2,
        "retry_delay": 1,
        "timeout": 10,
    }

    if bind_ip and str(bind_ip).strip():
        target_ip = str(bind_ip).strip()
        if ":" in target_ip:
            kwargs["use_ipv6"] = True
        kwargs["local_addr"] = (target_ip, 0)

    c = TelegramClient(**kwargs)
    is_alive = False
    fatal_death_reason = None

    try:
        await c.connect()
        if not await c.is_user_authorized():
            fatal_death_reason = "Сессия сброшена / не авторизована"
        else:
            me = await c.get_me()
            if not me or getattr(me, "deleted", False):
                fatal_death_reason = "Аккаунт удален в Telegram"
            else:
                is_alive = True
                clients.append(c)
                client_sessions[c] = session_name
                session_to_client[session_name] = c
                uname = f"@{me.username}" if getattr(me, "username", None) else (acc.get("phone") or "-")
                client_meta[c] = f"{me.first_name or session_name} ({uname})"
                print(
                    f"[+] Сессия {session_name} онлайн: {me.first_name} [{device_info['device_model']}] ({acc.get('phone', '-')})",
                    flush=True,
                )
    except Exception as e:
        if is_fatal_account_error(e):
            fatal_death_reason = f"Бан / Сброс сессии: {e}"
        else:
            print(
                f"[WARN] Сессия {session_name} временно недоступна (сеть/таймаут): {e}",
                flush=True,
            )

    if not is_alive:
        try:
            await c.disconnect()
        except Exception:
            pass
        if fatal_death_reason:
            move_to_trash(session_name, fatal_death_reason)
        return None

    if len(listener_clients) < 3:
        listener_clients.add(c)
        c.add_event_handler(handle_new_message, events.NewMessage(chats=source_channel))

    await init_client_target(c, target_bot, source_channel)
    return c


async def sync_accounts_and_settings():
    global current_source_channel, current_target_bot
    settings = load_settings()
    source_channel = settings.get("source_channel", "vaycher_virtnumber").lstrip("@").strip()
    target_bot = settings.get("target_bot", "Virtnumber_buy_bot").lstrip("@").strip()

    channel_changed = source_channel != current_source_channel
    bot_changed = target_bot != current_target_bot
    current_source_channel = source_channel
    current_target_bot = target_bot

    accounts = load_accounts()
    desired_sessions = {str(a.get("session", "")).strip(): a for a in accounts if str(a.get("session", "")).strip()}

    for s_name, c in list(session_to_client.items()):
        if s_name not in desired_sessions:
            print(f"[*] Отключение удаленной сессии: {s_name}", flush=True)
            _unregister_client(c)
            try:
                await c.disconnect()
            except Exception:
                pass

    for s_name, acc in desired_sessions.items():
        if s_name not in session_to_client:
            await connect_single_account(acc, source_channel, target_bot)

    if channel_changed:
        for c in list(listener_clients):
            c.remove_event_handler(handle_new_message)
        listener_clients.clear()

    for c in clients:
        if len(listener_clients) >= 3:
            break
        if c not in listener_clients:
            listener_clients.add(c)
            c.add_event_handler(handle_new_message, events.NewMessage(chats=source_channel))
            await init_client_target(c, target_bot, source_channel)
        elif bot_changed:
            await init_client_target(c, target_bot, source_channel)

    # Проверяем, есть ли аккаунты с балансом >= порога авто-вывода
    if settings.get("auto_withdraw_enabled", True) and settings.get("site_withdraw_login", "").strip():
        min_w = float(settings.get("min_withdraw_amount", 30.0))
        s_login = settings.get("site_withdraw_login", "").strip()
        for s_name, acc in desired_sessions.items():
            c = session_to_client.get(s_name)
            if not c:
                continue
            try:
                bal = float(str(acc.get("balance", "0")).replace(",", "."))
            except ValueError:
                bal = 0.0
            if bal >= min_w and s_name not in autowithdraw_locks:
                asyncio.create_task(auto_withdraw_single_client(c, s_name, target_bot, s_login, min_w))


async def keep_alive_and_hot_reload():
    tick = 0
    next_warmup_ts = time.time() + random.uniform(4 * 3600, 6 * 3600)

    while True:
        await asyncio.sleep(5)
        tick += 1
        try:
            await sync_accounts_and_settings()
        except Exception as e:
            print(f"[WARN] Ошибка синхронизации конфигураций: {e}", flush=True)

        if os.path.exists(WARMUP_TRIGGER_FILE):
            try:
                os.remove(WARMUP_TRIGGER_FILE)
            except Exception:
                pass
            asyncio.create_task(run_stealth_warmup_pass(reason="ручной запуск из панели"))
            next_warmup_ts = time.time() + random.uniform(4 * 3600, 6 * 3600)

        if tick % 12 == 0 and time.time() >= next_warmup_ts:
            settings = load_settings()
            if settings.get("enable_stealth_warmup", True) and clients:
                asyncio.create_task(run_stealth_warmup_pass(reason="авто-прогрев 4-6ч"))
            next_warmup_ts = time.time() + random.uniform(4 * 3600, 6 * 3600)

        # Периодический Media DC Keep-Alive (раз в 2 минуты = 24 тика)
        # Предотвращает закрытие TCP-сокета к Media DC по таймауту неактивности Telegram
        if tick % 24 == 0 and listener_clients:
            settings = load_settings()
            src_ch = settings.get("source_channel", "vaycher_virtnumber").lstrip("@").strip()
            for lc in list(listener_clients)[:1]:
                try:
                    if src_ch:
                        ch = await lc.get_input_entity(src_ch)
                        recent = await lc.get_messages(ch, limit=1)
                        if recent and (recent[0].photo or recent[0].document):
                            await lc.download_media(recent[0], file=bytes, thumb="s")
                except Exception:
                    pass

        if tick % 5 == 0:
            for c in list(clients):
                sess_name = client_sessions.get(c, "unknown")
                try:
                    if not c.is_connected():
                        await c.connect()
                    if not await c.is_user_authorized():
                        raise AuthKeyUnregisteredError(request=None)
                    me = await c.get_me()
                    if not me or getattr(me, "deleted", False):
                        raise UserDeactivatedError(request=None)
                except Exception as e:
                    err_msg = str(e)
                    if is_fatal_account_error(e):
                        _unregister_client(c)
                        move_to_trash(sess_name, f"Отлетел в фоне: {err_msg}")
                        try:
                            await c.disconnect()
                        except Exception:
                            pass
                    else:
                        print(f"[WARN] Временная сетевая ошибка на {sess_name}: {err_msg}", flush=True)


async def main():
    global current_source_channel, current_target_bot
    settings = load_settings()
    current_source_channel = settings.get("source_channel", "vaycher_virtnumber").lstrip("@").strip()
    current_target_bot = settings.get("target_bot", "Virtnumber_buy_bot").lstrip("@").strip()
    enable_jitter = settings.get("enable_smart_jitter", False)
    enable_warmup = settings.get("enable_stealth_warmup", True)
    auto_w = settings.get("auto_withdraw_enabled", True)
    min_w = settings.get("min_withdraw_amount", 30.0)

    print(f"[*] Запуск FarmBot Sniper PRO v5.0 by wcatox | Канал: @{current_source_channel} -> Бот: @{current_target_bot}", flush=True)
    print(
        f"[*] Smart Jitter: {'ВКЛ' if enable_jitter else 'ВЫКЛ'} | Авто-вывод (от {min_w}₽): {'ВКЛ' if auto_w else 'ВЫКЛ'} | Стелс-прогрев: {'ВКЛ' if enable_warmup else 'ВЫКЛ'}",
        flush=True,
    )

    accounts = load_accounts()
    accounts_updated = False
    for acc in accounts:
        s_name = str(acc.get("session", "")).strip()
        if s_name and not acc.get("device_model"):
            dev = get_device_fingerprint(s_name, acc)
            acc["device_model"] = dev["device_model"]
            acc["system_version"] = dev["system_version"]
            acc["app_version"] = dev["app_version"]
            accounts_updated = True
    if accounts_updated:
        save_accounts(accounts)

    if not accounts:
        print("[*] Список аккаунтов пока пуст. Служба активна и ожидает подключения аккаунтов в веб-панели...", flush=True)
    else:
        print(f"[*] Подключение {len(accounts)} аккаунтов...", flush=True)
        await sync_accounts_and_settings()
        print(f"[✓] Активных аккаунтов на старте: {len(clients)} | Слушателей канала: {len(listener_clients)}", flush=True)

    await keep_alive_and_hot_reload()


if __name__ == "__main__":
    asyncio.run(main())
