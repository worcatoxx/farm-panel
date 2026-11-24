#!/usr/bin/env python3
import argparse, asyncio, base64, hashlib, json, os, re, secrets, socket, subprocess, tempfile, time, urllib.parse, urllib.request
from datetime import datetime

SUB_URL_DEFAULT = ""
DEFAULT_BEACON_URL = ""
DEFAULT_BEACON_TOKEN = ""
DATA_DIR = "/root/farm_data" if os.name != "nt" else os.path.join(os.path.dirname(os.path.abspath(__file__)), "farm_data")
PROXIES_STATE_FILE = os.path.join(DATA_DIR, "xray_proxies.json")
RESERVED_CACHE_FILE = os.path.join(DATA_DIR, "xray_reserved_cache.json")
BEACON_FILE = os.path.join(DATA_DIR, "beacon.json")
XRAY_CONF_DIR = "/etc/xray-farm"
XRAY_CONF_FILE = os.path.join(XRAY_CONF_DIR, "config.json")
XRAY_BIN = "/usr/local/bin/xray"

SKIP_KW = ("автовыбор", "трафик", "осталось", "истекает", "поддерж", "инфо", "подписк", "traffic", "expire", "reset", "support", "tg:", "t.me")
EU_HINTS = ("🇩🇪", "🇳🇱", "🇱🇻", "🇵🇱", "🇫🇮", "🇸🇪", "🇪🇪", "🇱🇹", "🇨🇿", "🇦🇹", "🇫🇷", "🇬🇧", "герм", "нидер", "латв", "рига", "польш", "финл", "швец", "литв", "эст", "праг", "франц", "frankfurt", "amsterdam", "riga", "warsaw", "helsinki")


def get_machine_id_hash() -> str:
    mid = open("/etc/machine-id").read().strip() if os.path.exists("/etc/machine-id") else socket.gethostname()
    return hashlib.sha256(mid.encode()).hexdigest()[:16]


def fetch_subscription(url: str, hwid: str = "") -> str:
    url = str(url or "").strip()
    if not url:
        raise RuntimeError("Ссылка подписки пуста")
    hid = hwid.strip() if hwid else get_machine_id_hash()
    attempts = [
        {"User-Agent": "Happ/2.4.1/android", "x-hwid": hid, "x-device-os": "Android", "x-ver-os": "14", "x-device-model": "Pixel 9"},
        {"User-Agent": "v2rayN/6.45"},
        {"User-Agent": "curl/8.5.0"},
    ]
    last_raw = ""
    for hdrs in attempts:
        try:
            raw = urllib.request.urlopen(urllib.request.Request(url, headers=hdrs), timeout=25).read().decode("utf-8", errors="ignore").strip()
            if not raw: continue
            last_raw = raw
            if parse_subscription(raw): return raw
        except Exception: pass
    if last_raw: return last_raw
    raise RuntimeError("Не удалось скачать подписку")


def fetch_reserved_ips(exclude_url: str) -> set[str]:
    if not exclude_url:
        return set()
    try:
        raw = urllib.request.urlopen(urllib.request.Request(exclude_url, headers={"User-Agent": "FarmBot-Sync/1.0"}), timeout=8).read().decode("utf-8", errors="ignore")
        ips = {str(x).strip() for x in json.loads(raw).get("reserved", []) if str(x).strip()}
        if ips:
            os.makedirs(DATA_DIR, exist_ok=True)
            open(RESERVED_CACHE_FILE, "w", encoding="utf-8").write(json.dumps(sorted(ips)))
        return ips
    except Exception:
        if os.path.exists(RESERVED_CACHE_FILE):
            try:
                return set(json.load(open(RESERVED_CACHE_FILE, "r", encoding="utf-8")))
            except Exception:
                pass
    return set()


def _load_bound_proxy_ports() -> set[int]:
    ports: set[int] = set()
    candidates = [
        os.path.join(DATA_DIR, "accounts.json"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "accounts.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                accs = json.load(f)
            if not isinstance(accs, list):
                continue
            for a in accs:
                bind_ip = str(a.get("bind_ip", "") or "").strip()
                if not bind_ip.startswith("socks5://"):
                    continue
                parsed = urllib.parse.urlsplit(bind_ip)
                if parsed.port:
                    ports.add(int(parsed.port))
            if ports:
                break
        except Exception:
            pass
    return ports


def send_master_heartbeat(meta: dict, slots_map: dict):
    b_url = meta.get("beacon_url", "").strip()
    b_tok = meta.get("beacon_token", "").strip() or meta.get("share_token", "").strip()
    if not b_url or not b_tok:
        return
    try:
        accs_cnt = 0
        accs_file = os.path.join(DATA_DIR, "accounts.json")
        if os.path.exists(accs_file):
            accs_cnt = len(json.load(open(accs_file, "r", encoding="utf-8")))
        vpn_domain = urllib.parse.urlsplit(meta.get("sub_url", "")).netloc.split("@")[-1] or "не задана"
        payload = json.dumps({
            "token": b_tok,
            "client_id": get_machine_id_hash(),
            "hostname": socket.gethostname(),
            "role": "master" if meta.get("tier", 0) == 0 else "friend",
            "tier": meta.get("tier", 0),
            "friends": meta.get("friends", 1),
            "accounts_count": accs_cnt,
            "proxies_count": len(slots_map),
            "version": "PRO v5.0",
            "vpn_domain": vpn_domain,
            "exit_ips": [v.get("exit_ip", "") for v in slots_map.values() if v.get("exit_ip")],
        }).encode("utf-8")
        req = urllib.request.Request(b_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=5).read()
    except Exception:
        pass


def _extract_hp(ob: dict):
    p, s = ob.get("protocol", ""), ob.get("settings", {})
    if p in ("vless", "vmess") and s.get("vnext"):
        return str(s["vnext"][0].get("address", "")), int(s["vnext"][0].get("port", 0))
    if p in ("trojan", "shadowsocks") and s.get("servers"):
        return str(s["servers"][0].get("address", "")), int(s["servers"][0].get("port", 0))
    return None


def _dedup(nodes: list) -> list:
    seen, clean = set(), []
    for n in nodes:
        addr, low = n["address"], n["remark"].lower()
        if addr in ("127.0.0.1", "0.0.0.0", "localhost") or any(k in low for k in SKIP_KW) or addr in seen:
            continue
        seen.add(addr)
        n["is_eu"] = any(h in low for h in EU_HINTS)
        n["remark_short"] = re.sub(r"[▶️⚡🔥]", "", n["remark"]).strip()[:28]
        fm = re.match(r"^([^\w\s]{1,4}\s*[^\s•|]+)", n["remark_short"])
        n["country_prefix"] = fm.group(1) if fm else ""
        clean.append(n)
    return clean


def _parse_uri(line: str):
    line = line.strip()
    if line.startswith("vless://"):
        u = urllib.parse.urlsplit(line)
        host, port, uid = u.hostname, u.port or 443, urllib.parse.unquote(u.username or "")
        if not host or not uid:
            return None
        rem = urllib.parse.unquote(u.fragment or "").strip() or f"{host}:{port}"
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        net, sec = qs.get("type", "tcp") or "tcp", qs.get("security", "none") or "none"
        st = {"network": net, "security": sec}
        if sec == "reality":
            st["realitySettings"] = {"show": False, "fingerprint": qs.get("fp", "chrome") or "chrome", "serverName": qs.get("sni", ""), "publicKey": qs.get("pbk", ""), "shortId": qs.get("sid", ""), "spiderX": qs.get("spx", "")}
        elif sec == "tls":
            t = {"serverName": qs.get("sni") or host, "fingerprint": qs.get("fp", "chrome") or "chrome", "allowInsecure": qs.get("allowInsecure", "0") in ("1", "true", "True")}
            if qs.get("alpn"):
                t["alpn"] = [x.strip() for x in qs["alpn"].split(",") if x.strip()]
            st["tlsSettings"] = t
        if net == "ws":
            st["wsSettings"] = {"path": qs.get("path", "/") or "/", "headers": {"Host": qs["host"]} if qs.get("host") else {}}
        elif net == "grpc":
            st["grpcSettings"] = {"serviceName": qs.get("serviceName", "") or "", "multiMode": qs.get("mode") == "multi"}
        elif net in ("xhttp", "splithttp"):
            st["xhttpSettings"] = {"path": qs.get("path", "/") or "/", "host": qs.get("host", "") or ""}
        usr = {"id": uid, "encryption": qs.get("encryption", "none") or "none"}
        if qs.get("flow"):
            usr["flow"] = qs["flow"]
        return {"remark": rem, "address": host, "port": int(port), "outbound": {"protocol": "vless", "settings": {"vnext": [{"address": host, "port": int(port), "users": [usr]}]}, "streamSettings": st}}
    if line.startswith("trojan://"):
        u = urllib.parse.urlsplit(line)
        host, port, pwd = u.hostname, u.port or 443, urllib.parse.unquote(u.username or "")
        if not host or not pwd:
            return None
        rem = urllib.parse.unquote(u.fragment or "").strip() or f"{host}:{port}"
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        st = {"network": qs.get("type", "tcp") or "tcp", "security": qs.get("security", "tls") or "tls"}
        if st["security"] == "tls":
            st["tlsSettings"] = {"serverName": qs.get("sni") or host, "fingerprint": qs.get("fp", "chrome") or "chrome", "allowInsecure": qs.get("allowInsecure", "0") in ("1", "true", "True")}
        return {"remark": rem, "address": host, "port": int(port), "outbound": {"protocol": "trojan", "settings": {"servers": [{"address": host, "port": int(port), "password": pwd}]}, "streamSettings": st}}
    return None


def parse_subscription(raw: str) -> list:
    nodes, text = [], raw.strip()
    if text.startswith("[") or text.startswith("{"):
        try:
            obj = json.loads(text)
            for item in (obj if isinstance(obj, list) else [obj]):
                if not isinstance(item, dict):
                    continue
                rem = str(item.get("remarks") or item.get("remark") or "").strip()
                for ob in item.get("outbounds", []):
                    if ob.get("protocol") in ("vless", "vmess", "trojan", "shadowsocks"):
                        hp = _extract_hp(ob)
                        if hp and hp[0] and hp[1] > 0:
                            nodes.append({"remark": rem or f"{hp[0]}:{hp[1]}", "address": hp[0], "port": hp[1], "outbound": json.loads(json.dumps(ob))})
                        break
            if nodes:
                return _dedup(nodes)
        except Exception:
            pass
    if "://" not in text[:300]:
        try:
            s = re.sub(r"\s+", "", text).replace("-", "+").replace("_", "/")
            s += "=" * ((-len(s)) % 4)
            dec = base64.b64decode(s).decode("utf-8", errors="ignore")
            if "://" in dec:
                text = dec
        except Exception:
            pass
    for ln in text.splitlines():
        n = _parse_uri(ln)
        if n:
            nodes.append(n)
    return _dedup(nodes)


async def vps_direct_ok() -> bool:
    for h, p in (("1.1.1.1", 443), ("149.154.167.50", 443)):
        w = None
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(h, p), timeout=2.5)
            return True
        except Exception:
            pass
        finally:
            if w:
                try:
                    w.close(); await w.wait_closed()
                except Exception:
                    pass
    return False


async def tcp_probe(node: dict, sem: asyncio.Semaphore):
    async with sem:
        t0, w = time.perf_counter(), None
        try:
            _, w = await asyncio.wait_for(asyncio.open_connection(node["address"], node["port"]), timeout=1.5)
            return node, round((time.perf_counter() - t0) * 1000.0, 1)
        except Exception:
            return node, None
        finally:
            if w:
                try:
                    w.close(); await w.wait_closed()
                except Exception:
                    pass


async def _s5_conn(port: int, thost: str, tport: int, timeout: float = 4.0):
    r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), timeout=timeout)
    w.write(b"\x05\x01\x00"); await w.drain()
    if await asyncio.wait_for(r.readexactly(2), timeout=timeout) != b"\x05\x00":
        raise RuntimeError("auth")
    hb = thost.encode()
    w.write(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + int(tport).to_bytes(2, "big")); await w.drain()
    hdr = await asyncio.wait_for(r.readexactly(4), timeout=timeout)
    if hdr[1] != 0:
        raise RuntimeError("conn")
    if hdr[3] == 1:
        await asyncio.wait_for(r.readexactly(6), timeout=timeout)
    elif hdr[3] == 3:
        ln = (await asyncio.wait_for(r.readexactly(1), timeout=timeout))[0]
        await asyncio.wait_for(r.readexactly(ln + 2), timeout=timeout)
    elif hdr[3] == 4:
        await asyncio.wait_for(r.readexactly(18), timeout=timeout)
    return r, w


async def fast_tg_probe(port: int, timeout: float = 4.0):
    t0, w = time.perf_counter(), None
    try:
        r, w = await _s5_conn(port, "core.telegram.org", 80, timeout=timeout)
        w.write(b"HEAD / HTTP/1.1\r\nHost: core.telegram.org\r\nConnection: close\r\n\r\n"); await w.drain()
        if b"HTTP/" not in await asyncio.wait_for(r.read(128), timeout=timeout):
            return None
        return round((time.perf_counter() - t0) * 1000.0, 1)
    except Exception:
        return None
    finally:
        if w:
            try:
                w.close(); await w.wait_closed()
            except Exception:
                pass


async def _http_ipify_probe(port: int, timeout: float = 4.0) -> str | None:
    w = None
    try:
        r, w = await _s5_conn(port, "api.ipify.org", 80, timeout=timeout)
        w.write(b"GET / HTTP/1.1\r\nHost: api.ipify.org\r\nUser-Agent: curl/8.0\r\nConnection: close\r\n\r\n")
        await w.drain()
        body = (await asyncio.wait_for(r.read(1024), timeout=timeout + 0.5)).decode("utf-8", errors="ignore").split("\r\n\r\n", 1)[-1].strip()
        if re.match(r"^\d+\.\d+\.\d+\.\d+$", body):
            return body
        return None
    except Exception:
        return None
    finally:
        if w:
            try:
                w.close()
                await w.wait_closed()
            except Exception:
                pass


async def e2e_ip_probe(port: int, node: dict, sem: asyncio.Semaphore):
    async with sem:
        exit_ip = await _http_ipify_probe(port, timeout=4.0)
        if not exit_ip:
            return node, None, None
        tg_ms = await fast_tg_probe(port, timeout=4.0)
        if tg_ms is None:
            return node, None, None
        return node, tg_ms, exit_ip


def build_xray_cfg(port_nodes: list) -> dict:
    ins, outs, rules = [], [], []
    for i, (p, n) in enumerate(port_nodes):
        ins.append({"tag": f"socks-{i}", "port": int(p), "listen": "127.0.0.1", "protocol": "socks", "settings": {"auth": "noauth", "udp": True}})
        ob = json.loads(json.dumps(n["outbound"])); ob["tag"] = f"node-{i}"; outs.append(ob)
        rules.append({"type": "field", "inboundTag": [f"socks-{i}"], "outboundTag": f"node-{i}"})
    outs.append({"protocol": "freedom", "tag": "direct"})
    return {"log": {"loglevel": "warning"}, "inbounds": ins, "outbounds": outs, "routing": {"domainStrategy": "AsIs", "rules": rules}}


async def verify_cands_batched(cands: list, batch_size: int = 45) -> list:
    if not cands:
        return []
    alive_all = []
    for start in range(0, len(cands), batch_size):
        batch = cands[start : start + batch_size]
        t_ports = [(19001 + i, n) for i, n in enumerate(batch)]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tf:
            json.dump(build_xray_cfg(t_ports), tf); tfp = tf.name
        proc = None
        try:
            proc = subprocess.Popen([XRAY_BIN, "run", "-c", tfp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            await asyncio.sleep(1.3)
            sem = asyncio.Semaphore(8)
            for n, ms, ip in await asyncio.gather(*[e2e_ip_probe(p, n, sem) for p, n in t_ports]):
                if ms is not None and ip:
                    nc = dict(n); nc["ping_ms"] = ms; nc["exit_ip"] = ip; alive_all.append(nc)
        finally:
            if proc:
                proc.terminate()
                try: proc.wait(timeout=3)
                except Exception: proc.kill()
            if os.path.exists(tfp): os.remove(tfp)
        if start + batch_size < len(cands):
            await asyncio.sleep(0.25)

    alive_all.sort(key=lambda x: x["ping_ms"])
    seen_exit, seen_addr, unique_alive = set(), set(), []
    for n in alive_all:
        if n["exit_ip"] in seen_exit or n["address"] in seen_addr:
            continue
        seen_exit.add(n["exit_ip"]); seen_addr.add(n["address"])
        unique_alive.append(n)
    return unique_alive


def filter_for_tier(unique_alive: list, tier: int, friends: int, reserved_ips: set[str]) -> list:
    if tier <= 0:
        return unique_alive
    pool = [n for n in unique_alive if n["exit_ip"] not in reserved_ips and n["address"] not in reserved_ips]
    if friends <= 1:
        return pool
    bucket_idx = (tier - 1) % friends
    return [n for n in pool if (int(hashlib.md5(n["exit_ip"].encode()).hexdigest(), 16) % friends) == bucket_idx]


def _atomic_write_text(path: str, content: str):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp, path)


def _acquire_sync_lock():
    os.makedirs(DATA_DIR, exist_ok=True)
    lock_path = os.path.join(DATA_DIR, ".xray_sync.lock")
    fh = open(lock_path, "a+", encoding="utf-8")
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except ImportError:
        pass
    except OSError:
        fh.close()
        return None
    return fh


def _release_sync_lock(fh):
    if not fh:
        return
    try:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        fh.close()
    except Exception:
        pass


def save_and_apply(meta: dict, slots_map: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(XRAY_CONF_DIR, exist_ok=True)
    ordered = sorted(slots_map.items(), key=lambda x: x[0])
    new_str = json.dumps(build_xray_cfg(ordered), indent=2, ensure_ascii=False)
    old_str = open(XRAY_CONF_FILE, "r", encoding="utf-8").read() if os.path.exists(XRAY_CONF_FILE) else ""
    if new_str != old_str:
        _atomic_write_text(XRAY_CONF_FILE, new_str)
        if os.name != "nt":
            subprocess.run(["systemctl", "restart", "xray-farm"], check=False)
        print("[*] Конфиг Xray обновлен -> xray-farm перезапущен!", flush=True)
    elif os.name != "nt" and subprocess.run(["systemctl", "is-active", "xray-farm"], capture_output=True, text=True).stdout.strip() != "active":
        subprocess.run(["systemctl", "start", "xray-farm"], check=False)

    st = {
        "sub_url": meta["sub_url"],
        "hwid": meta.get("hwid", ""),
        "share_token": meta["share_token"],
        "beacon_url": meta.get("beacon_url", ""),
        "beacon_token": meta.get("beacon_token", ""),
        "tier": meta["tier"],
        "friends": meta["friends"],
        "exclude_url": meta["exclude_url"],
        "target_size": int(meta.get("target_size") or len(ordered) or 25),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pool_size": len(ordered),
        "slots": {
            str(p): {
                "remark": n["remark"], "remark_short": n["remark_short"], "country_prefix": n.get("country_prefix", ""),
                "address": n["address"], "port": n["port"], "exit_ip": n.get("exit_ip", n["address"]),
                "ping_ms": n.get("ping_ms"), "outbound": n["outbound"],
            } for p, n in ordered
        },
    }
    _atomic_write_text(PROXIES_STATE_FILE, json.dumps(st, indent=2, ensure_ascii=False))
    send_master_heartbeat(meta, slots_map)


def _load_meta_and_prev(args):
    prev_data = {}
    if os.path.exists(PROXIES_STATE_FILE):
        try:
            with open(PROXIES_STATE_FILE, "r", encoding="utf-8") as f:
                prev_data = json.load(f)
        except Exception:
            pass
    beacon_data = {}
    if os.path.exists(BEACON_FILE):
        try:
            with open(BEACON_FILE, "r", encoding="utf-8") as f:
                beacon_data = json.load(f)
        except Exception:
            pass

    share_tok = prev_data.get("share_token") or secrets.token_hex(12)
    b_url = args.beacon_url if args.beacon_url is not None else (prev_data.get("beacon_url") or beacon_data.get("beacon_url") or DEFAULT_BEACON_URL)
    prev_btok = prev_data.get("beacon_token", "")
    if args.beacon_token is not None:
        b_tok = args.beacon_token
    elif beacon_data.get("token"):
        b_tok = beacon_data["token"]
    elif prev_btok and (prev_data.get("beacon_url") or not DEFAULT_BEACON_TOKEN or prev_btok != share_tok):
        b_tok = prev_btok
    else:
        b_tok = DEFAULT_BEACON_TOKEN or share_tok

    raw_size = args.size if args.size is not None else (prev_data.get("target_size") or prev_data.get("pool_size") or 25)
    try:
        target_size = max(1, min(100, int(raw_size)))
    except Exception:
        target_size = 25

    meta = {
        "sub_url": (args.sub or prev_data.get("sub_url") or SUB_URL_DEFAULT).strip(),
        "hwid": (args.hwid or prev_data.get("hwid") or "").strip(),
        "share_token": share_tok,
        "beacon_url": b_url,
        "beacon_token": b_tok,
        "tier": args.tier if args.tier is not None else int(prev_data.get("tier", 0) or 0),
        "friends": args.friends if args.friends is not None else int(prev_data.get("friends", 1) or 1),
        "exclude_url": args.exclude_url if args.exclude_url is not None else prev_data.get("exclude_url", ""),
        "target_size": target_size,
    }
    return meta, prev_data.get("slots", {})


async def full_sync(meta: dict, prev_slots: dict, pool_size: int, base_port: int, rotate_all: bool, scan_all: bool):
    os.makedirs(DATA_DIR, exist_ok=True); os.makedirs(XRAY_CONF_DIR, exist_ok=True)
    all_nodes = parse_subscription(fetch_subscription(meta["sub_url"], meta["hwid"]))
    print(f"[*] В подписке найдено конфигов (уник. вход): {len(all_nodes)}. Проверка TCP...", flush=True)
    sem = asyncio.Semaphore(50)
    tcp_alive = sorted(
        [(n, ms) for n, ms in await asyncio.gather(*[tcp_probe(n, sem) for n in all_nodes]) if ms is not None],
        key=lambda x: x[1],
    )

    cands = [n for n, _ in tcp_alive]
    c_addrs = {n["address"] for n in cands}
    for v in prev_slots.values():
        if v.get("address") and v["address"] not in c_addrs:
            for n, _ in tcp_alive:
                if n["address"] == v["address"]:
                    cands.append(n); c_addrs.add(n["address"])

    print(f"[*] Сквозной Xray-тест всех {len(cands)} TCP-живых узлов (ipify -> прогретый Telegram HEAD)...", flush=True)
    unique_alive = await verify_cands_batched(cands, batch_size=45)
    print(f"[OK] Живых серверов с УНИКАЛЬНЫМ выходным IP: {len(unique_alive)}", flush=True)

    reserved = fetch_reserved_ips(meta["exclude_url"]) if (meta["tier"] > 0 or meta.get("exclude_url")) else set()
    tier_pool = filter_for_tier(unique_alive, meta["tier"], meta["friends"], reserved)
    if meta["tier"] > 0:
        print(f"[*] Исключено {len(reserved)} IP владельца -> в корзине (Друг #{meta['tier']} из {meta['friends']}): {len(tier_pool)} уник. IP", flush=True)
    if not tier_pool:
        raise RuntimeError("В корзине не осталось свободных уникальных IP!")

    by_exit = {n["exit_ip"]: n for n in tier_pool}
    by_addr = {n["address"]: n for n in tier_pool}
    slots, used_exit, used_addr = {}, set(), set()
    ports = [base_port + i for i in range(min(pool_size, len(tier_pool)))]
    bound_ports = _load_bound_proxy_ports()

    if not rotate_all:
        for p in ports:
            if p not in bound_ports:
                continue
            old = prev_slots.get(str(p)) or {}
            cand = by_exit.get(old.get("exit_ip", "")) or by_addr.get(old.get("address", ""))
            if (
                cand
                and cand.get("ping_ms") is not None
                and float(cand["ping_ms"]) <= 250.0
                and cand["exit_ip"] not in used_exit
                and cand["address"] not in used_addr
            ):
                slots[p] = cand; used_exit.add(cand["exit_ip"]); used_addr.add(cand["address"])

    free = [n for n in tier_pool if n["exit_ip"] not in used_exit and n["address"] not in used_addr]
    for p in ports:
        if p not in slots and free:
            c = free.pop(0); slots[p] = c; used_exit.add(c["exit_ip"]); used_addr.add(c["address"])

    save_and_apply(meta, slots)
    for p in sorted(slots)[:10]:
        print(f"  127.0.0.1:{p} -> {slots[p]['remark_short']} | IP: {slots[p]['exit_ip']} | {slots[p]['ping_ms']} ms", flush=True)
    return slots, all_nodes


async def watchdog_daemon(args, base_port: int, interval: int, recheck: int):
    meta, prev_slots = _load_meta_and_prev(args)
    pool_size = meta["target_size"]
    print(f"[*] Watchdog (tier={meta['tier']}, friends={meta['friends']}, size={pool_size}): проверка {interval}с, повтор {recheck}с", flush=True)
    slots = {int(ps): info for ps, info in prev_slots.items() if info.get("outbound")}
    cached, last_fetch, last_hb = [], 0.0, 0.0
    last_mtime = os.path.getmtime(PROXIES_STATE_FILE) if os.path.exists(PROXIES_STATE_FILE) else 0.0
    last_sub_url = meta["sub_url"]
    synced_sig = (meta["sub_url"], pool_size) if (slots and len(slots) >= pool_size) else None
    waiting_logged = False
    applied_initial = False

    while True:
        try:
            cur_mtime = os.path.getmtime(PROXIES_STATE_FILE) if os.path.exists(PROXIES_STATE_FILE) else 0.0
            if cur_mtime != last_mtime:
                meta, prev_slots = _load_meta_and_prev(args)
                slots = {int(ps): info for ps, info in prev_slots.items() if info.get("outbound")}
                pool_size = meta["target_size"]
                if meta["sub_url"] != last_sub_url:
                    cached, last_fetch = [], 0.0
                    last_sub_url = meta["sub_url"]
                    waiting_logged = False
                synced_sig = (meta["sub_url"], pool_size) if (slots and len(slots) >= pool_size) else None
                last_mtime = cur_mtime
            else:
                meta, prev_slots = _load_meta_and_prev(args)
                pool_size = meta["target_size"]

            if not meta["sub_url"]:
                if not waiting_logged:
                    print("[*] Подписка VPN не задана — ожидание ссылки из веб-панели...", flush=True)
                    waiting_logged = True
                if (time.time() - last_hb) >= 90:
                    await asyncio.to_thread(send_master_heartbeat, meta, slots)
                    last_hb = time.time()
                await asyncio.sleep(5)
                continue

            waiting_logged = False
            needs_sync = (not slots) or (len(slots) > pool_size) or (synced_sig != (meta["sub_url"], pool_size) and len(slots) < pool_size)
            if needs_sync:
                lock_fh = _acquire_sync_lock()
                if lock_fh is None:
                    await asyncio.sleep(3)
                    continue
                try:
                    slots, cached = await full_sync(meta, prev_slots, pool_size, base_port, False, scan_all=True)
                    last_fetch = time.time()
                    last_hb = time.time()
                    synced_sig = (meta["sub_url"], pool_size)
                    last_mtime = os.path.getmtime(PROXIES_STATE_FILE) if os.path.exists(PROXIES_STATE_FILE) else 0.0
                    applied_initial = True
                finally:
                    _release_sync_lock(lock_fh)
                await asyncio.sleep(2)
                continue

            if not applied_initial and slots:
                save_and_apply(meta, slots)
                last_mtime = os.path.getmtime(PROXIES_STATE_FILE) if os.path.exists(PROXIES_STATE_FILE) else 0.0
                last_hb = time.time()
                applied_initial = True
                await asyncio.sleep(2)

            if (time.time() - last_hb) >= 90:
                await asyncio.to_thread(send_master_heartbeat, meta, slots)
                last_hb = time.time()

            if (time.time() - last_fetch) > 21600 or not cached:
                try:
                    p = parse_subscription(fetch_subscription(meta["sub_url"], meta["hwid"]))
                    if p: cached = p; last_fetch = time.time(); print(f"[SUB] Кэш обновлен ({len(cached)} узлов)", flush=True)
                except Exception as e: print(f"[WARN] SUB: {e}", flush=True)

            sem = asyncio.Semaphore(6)
            async def _chk(p):
                async with sem: return p, await fast_tg_probe(p, 4.0)
            suspects = {}
            for p, ms in await asyncio.gather(*[_chk(p) for p in sorted(slots)]):
                if ms is not None:
                    slots[p]["ping_ms"] = ms
                if ms is None or ms > 280.0:
                    suspects[p] = ms

            if suspects:
                suspect_list = sorted(suspects)
                print(f"[WATCHDOG] [!] Таймаут/высокий пинг (>280ms) портов {suspect_list} -> повтор через {recheck}с...", flush=True)
                await asyncio.sleep(recheck)
                if not await vps_direct_ok():
                    print("[WATCHDOG] [WARN] Нет сети на самом VPS, отмена!", flush=True)
                    await asyncio.sleep(interval); continue
                bad_ports = {}
                for p, ms in await asyncio.gather(*[_chk(p) for p in suspect_list]):
                    if ms is not None:
                        slots[p]["ping_ms"] = ms
                    if ms is not None and ms <= 280.0:
                        print(f"[WATCHDOG] [OK] Порт {p} в норме ({ms} ms)", flush=True)
                    else:
                        bad_ports[p] = ms
                if bad_ports:
                    if (time.time() - last_fetch) > 900:
                        try:
                            p = parse_subscription(fetch_subscription(meta["sub_url"], meta["hwid"]))
                            if p: cached = p; last_fetch = time.time()
                        except Exception: pass
                    bad_set = set(bad_ports)
                    used_addr = {v["address"] for k, v in slots.items() if k not in bad_set}
                    used_exit = {v.get("exit_ip", "") for k, v in slots.items() if k not in bad_set}
                    reserved = fetch_reserved_ips(meta["exclude_url"]) if (meta["tier"] > 0 or meta.get("exclude_url")) else set()
                    s_tcp = asyncio.Semaphore(40)
                    cands_tcp = sorted(
                        [(n, ms) for n, ms in await asyncio.gather(*[tcp_probe(n, s_tcp) for n in cached if n["address"] not in used_addr and n["address"] not in reserved]) if ms is not None],
                        key=lambda x: x[1],
                    )
                    verified = await verify_cands_batched([n for n, _ in cands_tcp], batch_size=45)
                    spares = [n for n in filter_for_tier(verified, meta["tier"], meta["friends"], reserved) if n["exit_ip"] not in used_exit and n["address"] not in used_addr]
                    changed = False
                    for dp in sorted(bad_ports):
                        if not spares:
                            break
                        prev_ms = bad_ports[dp]
                        ch = spares[0]
                        if prev_ms is not None and ch["ping_ms"] >= prev_ms:
                            continue
                        spares.pop(0)
                        old_ms_lbl = f"{prev_ms} ms" if prev_ms is not None else "таймаут"
                        print(f"[FAILOVER] Порт {dp} ({old_ms_lbl}): {slots[dp].get('remark_short')} -> {ch['remark_short']} [{ch['exit_ip']}, {ch['ping_ms']} ms]", flush=True)
                        slots[dp] = ch; used_exit.add(ch["exit_ip"]); used_addr.add(ch["address"]); changed = True
                    if changed:
                        save_and_apply(meta, slots)
                        last_mtime = os.path.getmtime(PROXIES_STATE_FILE) if os.path.exists(PROXIES_STATE_FILE) else 0.0
                        await asyncio.sleep(3)
        except Exception as e:
            print(f"[WATCHDOG-ERR] {e}", flush=True)
        await asyncio.sleep(interval)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", default=""); ap.add_argument("--hwid", default="")
    ap.add_argument("--size", type=int, default=None); ap.add_argument("--base-port", type=int, default=10801)
    ap.add_argument("--tier", type=int, default=None); ap.add_argument("--friends", type=int, default=None)
    ap.add_argument("--exclude-url", default=None); ap.add_argument("--beacon-url", default=None); ap.add_argument("--beacon-token", default=None)
    ap.add_argument("--rotate-all", action="store_true"); ap.add_argument("--scan-all", action="store_true")
    ap.add_argument("--daemon", action="store_true"); ap.add_argument("--interval", type=int, default=45); ap.add_argument("--recheck", type=int, default=15)
    a = ap.parse_args()
    if a.daemon:
        asyncio.run(watchdog_daemon(a, a.base_port, a.interval, a.recheck))
    else:
        meta, prev = _load_meta_and_prev(a)
        lock_fh = _acquire_sync_lock()
        try:
            asyncio.run(full_sync(meta, prev, meta["target_size"], a.base_port, a.rotate_all, a.scan_all))
        finally:
            _release_sync_lock(lock_fh)
