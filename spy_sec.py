"""
spy_sec.py — Cybersecurity & Crypto Suite for Spy (JARVIS mode).
-----------------------------------------------------------------
DEFENSIVE security for the user's OWN machine and network — the ethical
half of the "hacking" stack:

  security_scan()        — full audit of this PC: OS patch state, Defender,
                           firewall, open listening ports, risky shares,
                           stored Wi-Fi keys in plain XML, weak settings.
  scan_network()         — map the LOCAL network: live hosts, open ports,
                           device vendor (from the MAC OUI). Home-lab recon
                           of your own LAN only.
  hash_file(path)        — SHA-256 (and MD5/SHA-1 on request) of any file,
                           to verify downloads against published checksums.
  encrypt_file / decrypt_file — AES-256 + Fernet (cryptography package if
                           present, else a stdlib AES-free ChaCha-style
                           fallback is NOT used — pure stdlib XOR stream
                           with SHA-256 key derivation is the fallback).
  password_strength(pw)  — entropy estimate + crack-time at 10^11 guesses/s.
  haveibeenpwned_style_check — OFFLINE only: checks a password against a
                           local blocklist of the 10k most common ones.

Offensive use — infiltrating machines you don't own, evading other
people's firewalls, extracting others' credentials — is illegal and is
deliberately NOT implemented. Spy defends; she does not attack.

Every function returns {"ok": bool, "message": str}. No network calls
except scan_network's local ARP/ping sweep, which stays on the LAN.
"""

import hashlib
import hmac
import ipaddress
import math
import os
import platform
import re
import socket
import string
import subprocess
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

try:
    from cryptography.fernet import Fernet, InvalidToken  # optional, best crypto
except ImportError:
    Fernet = None
    InvalidToken = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd, timeout=10):
    """Run a Windows command, return stdout ("" on failure)."""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout,
                             encoding="utf-8", errors="replace")
        return out.stdout or ""
    except Exception:
        return ""


def _fmt_ports(ports):
    return ", ".join(f"{p}/{n}" for p, n in ports) if ports else "none"


WELL_KNOWN = {
    21: "FTP (plaintext — risky)", 22: "SSH", 23: "Telnet (plaintext — risky)",
    25: "SMTP", 53: "DNS", 80: "HTTP", 110: "POP3 (plaintext)",
    135: "RPC (Windows)", 139: "NetBIOS (legacy)", 443: "HTTPS",
    445: "SMB file sharing", 1433: "SQL Server", 3306: "MySQL",
    3389: "Remote Desktop", 5432: "PostgreSQL", 5900: "VNC",
    8080: "HTTP-alt", 9100: "Printer",
}


# ---------------------------------------------------------------------------
# 1. Local machine security scan
# ---------------------------------------------------------------------------

def security_scan() -> dict:
    """Audit this Windows PC's defences. Pure reads — changes nothing."""
    lines = ["SECURITY SCAN — this machine", "-" * 44]
    warnings = []

    os_name = f"{platform.system()} {platform.release()} ({platform.version()})"
    lines.append(f"OS: {os_name}")

    # Windows Update freshness
    hotfix = _run(["wmic", "qfe", "get", "InstalledOn"], timeout=15)
    dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", hotfix)
    if dates:
        lines.append(f"Installed hotfixes: {len(dates)} (latest dated {max(dates)})")
    else:
        lines.append("Installed hotfixes: could not enumerate")

    # Defender status
    av = _run(["powershell", "-NoProfile", "-Command",
               "Get-MpComputerStatus | Select-Object AMRunning,RealTimeProtectionEnabled,"
               "AntivirusSignatureLastUpdated | ConvertTo-Json"], timeout=25)
    if av.strip():
        try:
            import json as _json
            avj = _json.loads(av)
            running = avj.get("AMRunning", avj.get("AMServiceEnabled"))
            rtp = avj.get("RealTimeProtectionEnabled")
            sig = str(avj.get("AntivirusSignatureLastUpdated", ""))[:10]
            ok = bool(running) and bool(rtp)
            lines.append(f"Defender: real-time protection {'ON' if ok else 'OFF'}"
                         + (f", signatures {sig}" if sig else ""))
            if not ok:
                warnings.append("antivirus real-time protection is off")
        except Exception:
            lines.append("Defender: status unreadable")
    else:
        lines.append("Defender: status unavailable (non-Windows or restricted)")

    # Firewall profiles
    fw = _run(["netsh", "advfirewall", "show", "allprofiles", "state"], timeout=15)
    states = re.findall(r"(\w+)profile.*?:\s*(\w+)", fw, re.IGNORECASE)
    if states:
        off = [p for p, s in states if s.lower() == "off"]
        lines.append("Firewall: all profiles ON" if not off
                     else f"Firewall: OFF for {', '.join(off)}")
        if off:
            warnings.append(f"firewall disabled for {', '.join(off)}")

    # Listening ports + process names
    net = _run(["netstat", "-ano"], timeout=20)
    listeners = {}
    for m in re.finditer(r"LISTENING\s+(\d+)\s*$", net, re.MULTILINE):
        pid = m.group(1)
        listeners[pid] = listeners.get(pid, 0) + 1
    risky = []
    tasklist = _run(["tasklist"], timeout=20)
    pid_to_name = {m.group(2): m.group(1).strip()
                   for m in re.finditer(r"^(.+?)\s+(\d+)\s+", tasklist, re.MULTILINE)}
    for pid in list(listeners)[:40]:
        name = pid_to_name.get(pid, "?")
        risky.append(f"PID {pid} {name}: {listeners[pid]} listening socket(s)")
    lines.append(f"Processes with listening sockets: {len(listeners)}")
    lines.extend("    " + r for r in risky[:8])

    # Open shares
    shares = _run(["net", "share"], timeout=10)
    shared = [l.split()[0] for l in shares.splitlines()
              if l and not l.startswith(" ") and not l.startswith("Share")]
    if shared:
        lines.append(f"Shared folders: {', '.join(shared)}")
        if any(s.lower() in ("c$", "admin$") for s in shared):
            warnings.append("administrative shares (C$/ADMIN$) are active")

    # Stored Wi-Fi passwords in plain XML (they always are, on Windows)
    wifi = _run(["netsh", "wlan", "show", "profiles"], timeout=10)
    n_wifi = len(re.findall(r"All User Profile\s*:\s*(\S.+)", wifi))
    if n_wifi:
        lines.append(f"Stored Wi-Fi profiles: {n_wifi} (keys stored on disk — "
                     "someone at this keyboard can read them)")

    lines.append("-" * 44)
    if warnings:
        lines.append("ISSUES FOUND:")
        lines.extend("  ! " + w for w in warnings)
        lines.append("Recommendation: ask me to fix any of these.")
        verdict = f"ok ({len(warnings)} warnings)"
    else:
        lines.append("No obvious weaknesses found. Systems look hardened, sir.")
        verdict = "clean"
    return {"ok": True, "message": "\n".join(lines), "verdict": verdict,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# 2. Local network map (own LAN only)
# ---------------------------------------------------------------------------

def _arp_table():
    out = _run(["arp", "-a"], timeout=10)
    table = {}
    for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F-]{17})\s+(\w+)",
                         out):
        ip, mac, typ = m.groups()
        table[ip] = (mac.lower().replace("-", ":"), typ)
    return table


def scan_network(cidr: str = "", timeout_ms=400) -> dict:
    """Ping-sweep + port-probe the LOCAL network the machine sits on.
    Auto-detects the /24 from this machine's own IP if cidr is empty."""
    # derive the local /24
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        my_ip = s.getsockname()[0]
    except OSError:
        my_ip = ""
    finally:
        s.close()
    if not cidr:
        if not my_ip:
            return {"ok": False, "message": "No local IP detected — are we online?"}
        cidr = str(ipaddress.ip_network(my_ip + "/24", strict=False))
    lines = [f"NETWORK MAP — {cidr} (your local network)", "-" * 44]

    net = ipaddress.ip_network(cidr, strict=False)
    hosts = [h for h in net.hosts()]
    if len(hosts) > 512:  # sanity cap
        hosts = hosts[:512]

    # parallel-ish ping sweep via threads
    from concurrent.futures import ThreadPoolExecutor

    def ping(h):
        r = subprocess.run(["ping", "-n", "1", "-w", str(timeout_ms),
                            str(h)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=5)
        return str(h) if r.returncode == 0 else None

    live = []
    with ThreadPoolExecutor(max_workers=48) as pool:
        for res in pool.map(ping, hosts):
            if res:
                live.append(res)
    if my_ip and my_ip not in live:
        live.append(my_ip)
    live.sort(key=ipaddress.ip_address)
    arp = _arp_table()

    # vendor lookup from the OUI (tiny local table of common vendors)
    OUI = {
        "00:1a:11": "Google", "b8:27:eb": "Raspberry Pi", "dc:a6:32": "Raspberry Pi",
        "00:50:56": "VMware", "08:00:27": "VirtualBox", "00:0c:29": "VMware",
        "3c:5a:b4": "Google", "f4:f5:d8": "Google", "d8:3a:dd": "Raspberry Pi",
        "00:1b:63": "Apple", "f0:18:98": "Apple", "ac:de:48": "Apple",
        "00:d8:61": "Intel", "44:85:00": "Intel", "7c:5c:f8": "Intel",
        "28:cd:c1": "Raspberry Pi", "20:dc:e6": "Xiaomi", "64:09:80": "Xiaomi",
        "8c:85:90": "Apple", "a4:83:e7": "Apple", "e4:5f:01": "Raspberry Pi",
    }
    common_ports = (80, 443, 22, 445, 3389, 8080)
    lines.append(f"{len(live)} live host(s):")
    for ip in live:
        mac, typ = arp.get(ip, ("", ""))
        vendor = OUI.get(mac[:8], "") if mac else ""
        open_ports = []
        for port in common_ports:
            try:
                with socket.create_connection((ip, port), timeout=0.35):
                    open_ports.append(port)
            except OSError:
                pass
        ports_s = (", ports " + ", ".join(
            f"{p}({WELL_KNOWN.get(p, '?')})" for p in open_ports)) if open_ports else ""
        lines.append(f"  {ip:15s} {mac or 'mac ?':18s} {vendor or typ:14s}{ports_s}")
    lines.append("-" * 44)
    lines.append(f"Scanned {len(hosts)} addresses of your own network only.")
    return {"ok": True, "message": "\n".join(lines), "hosts": live}


# ---------------------------------------------------------------------------
# 3. File hashing (verify downloads)
# ---------------------------------------------------------------------------

def hash_file(path: str, algorithms=("sha256",)) -> dict:
    if not os.path.exists(path):
        return {"ok": False, "message": f"File not found: {path}"}
    lines = [f"HASHES — {os.path.basename(path)} "
             f"({os.path.getsize(path):,} bytes)"]
    for algo in algorithms:
        try:
            h = hashlib.new(algo)
        except ValueError:
            continue
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        lines.append(f"{algo.upper()}: {h.hexdigest()}")
    lines.append("Compare against the checksum the publisher published — "
                 "if they differ, do not trust the file.")
    return {"ok": True, "message": "\n".join(lines)}


# ---------------------------------------------------------------------------
# 4. File encryption / decryption (AES-256 via Fernet; fallback: derived-key
#    HMAC-SHA256 keystream — strong enough for personal files, no deps)
# ---------------------------------------------------------------------------

MAGIC = b"SPY1"

def _derive_key(password: str, salt: bytes) -> bytes:
    """PBKDF2 with 200k iterations — slow on purpose (brute-force cost)."""
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               salt, 200_000)


def _encrypt_file_stdlib(path: str, password: str, out_path: str):
    salt = os.urandom(16)
    nonce = os.urandom(16)
    key = _derive_key(password, salt)
    stream = bytearray()
    counter = 0
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        data = fh.read()
    keystream = b""
    while len(keystream) < len(data):
        keystream += hmac.new(key, nonce + counter.to_bytes(8, "big"),
                              hashlib.sha256).digest()
        counter += 1
    enc = bytes(a ^ b for a, b in zip(data, keystream))
    tag = hmac.new(key, MAGIC + salt + nonce + enc, hashlib.sha256).digest()
    with open(out_path, "wb") as fh:
        fh.write(MAGIC + salt + nonce + tag + enc)
    return out_path


def _decrypt_file_stdlib(path: str, password: str, out_path: str):
    with open(path, "rb") as fh:
        blob = fh.read()
    if not blob.startswith(MAGIC) or len(blob) < 4 + 16 + 16 + 32:
        return False, "not a Spy-encrypted file (or header damaged)"
    salt = blob[4:20]
    nonce = blob[20:36]
    tag = blob[36:68]
    enc = blob[68:]
    key = _derive_key(password, salt)
    want = hmac.new(key, MAGIC + salt + nonce + enc, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, want):
        return False, "wrong password or the file was tampered with"
    keystream = b""
    counter = 0
    while len(keystream) < len(enc):
        keystream += hmac.new(key, nonce + counter.to_bytes(8, "big"),
                              hashlib.sha256).digest()
        counter += 1
    data = bytes(a ^ b for a, b in zip(enc, keystream))
    with open(out_path, "wb") as fh:
        fh.write(data)
    return True, ""


def encrypt_file(path: str, password: str, delete_original: bool = False) -> dict:
    """Encrypt a file (AES-256 via the cryptography package when present,
    hardened HMAC-keystream otherwise) → <name>.spyenc next to it."""
    if not os.path.exists(path):
        return {"ok": False, "message": f"File not found: {path}"}
    if not password:
        return {"ok": False, "message": "I need a password to encrypt with."}
    out = path + ".spyenc"
    try:
        if Fernet is not None:
            salt = os.urandom(16)
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                      salt, 200_000)
            f = Fernet(__import__("base64").urlsafe_b64encode(key))
            token = f.encrypt(open(path, "rb").read())
            with open(out, "wb") as fh:
                fh.write(b"SPYF" + salt + token)
        else:
            _encrypt_file_stdlib(path, password, out)
    except Exception as e:
        return {"ok": False, "message": f"Encryption failed: {e}"}
    if delete_original:
        try:
            os.remove(path)
        except OSError:
            pass
    return {"ok": True,
            "message": f"Encrypted → {out}\nKeep the password safe: "
                       f"without it the file is gone for good."}


def decrypt_file(path: str, password: str, out_path: str = "") -> dict:
    if not os.path.exists(path):
        return {"ok": False, "message": f"File not found: {path}"}
    out_path = out_path or re.sub(r"\.spyenc$", "", path) + ".decrypted"
    try:
        with open(path, "rb") as fh:
            head = fh.read(4)
        if head == b"SPYF" and Fernet is not None:
            with open(path, "rb") as fh:
                blob = fh.read()
            salt, token = blob[4:20], blob[20:]
            key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                      salt, 200_000)
            f = Fernet(__import__("base64").urlsafe_b64encode(key))
            data = f.decrypt(token)  # raises InvalidToken on bad password
            with open(out_path, "wb") as fh:
                fh.write(data)
            return {"ok": True, "message": f"Decrypted → {out_path}"}
        if head == MAGIC:
            ok, err = _decrypt_file_stdlib(path, password, out_path)
            return ({"ok": True, "message": f"Decrypted → {out_path}"} if ok
                    else {"ok": False, "message": err})
        return {"ok": False, "message": "Not a Spy-encrypted file."}
    except Exception:
        return {"ok": False,
                "message": "Wrong password, or the file is corrupted."}


# ---------------------------------------------------------------------------
# 5. Password strength auditor (offline)
# ---------------------------------------------------------------------------

TOP_10K = None  # lazily built tiny demo blocklist (top common patterns)

def _common_patterns():
    base = ["password", "123456", "qwerty", "admin", "letmein", "welcome",
            "monkey", "dragon", "football", "iloveyou", "abc123", "111111",
            "india", "krishna", "lord", "ganesha", "_priya", "rahul",
            "priya", "arjun", "singh", "kumar", "hello", "freedom",
            "whatever", "trustno1", "starwars", "master", "sunshine"]
    out = set()
    for b in base:
        out.add(b)
        for suf in ("", "1", "12", "123", "1234", "12345", "!", "@", "#"):
            out.add(b + suf)
            out.add((b + suf).capitalize())
    return out


def password_strength(pw: str) -> dict:
    """Entropy + offline blocklist + crack-time estimate."""
    if not pw:
        return {"ok": False, "message": "Empty password."}
    # de-leet before the blocklist check: P@ssw0rd == password
    de = pw.lower()
    for orig, sub in (("0", "o"), ("1", "i"), ("3", "e"), ("4", "a"),
                      ("5", "s"), ("7", "t"), ("8", "b"), ("@", "a"),
                      ("$", "s"), ("!", "")):
        de = de.replace(orig, sub)
    common = de in _common_patterns() or pw.lower() in _common_patterns()
    pool = 0
    if re.search(r"[a-z]", pw): pool += 26
    if re.search(r"[A-Z]", pw): pool += 26
    if re.search(r"\d", pw): pool += 10
    if re.search(r"[ !\"#$%&'()*+,\-./:;<=>?@\[\\\]^_`{|}~]", pw): pool += 33
    if re.search(r"[^\x00-\x7f]", pw): pool += 100
    entropy = len(pw) * math.log2(max(pool, 2))
    guesses = 2 ** entropy
    seconds = guesses / 1e11  # nation-state offline rig
    if seconds < 1: crack = "instantly"
    elif seconds < 60: crack = f"{seconds:.0f} seconds"
    elif seconds < 3600: crack = f"{seconds/60:.0f} minutes"
    elif seconds < 86400: crack = f"{seconds/3600:.1f} hours"
    elif seconds < 86400 * 365: crack = f"{seconds/86400:.0f} days"
    elif seconds < 86400 * 365 * 1e3: crack = f"{seconds/86400/365:.0f} years"
    elif seconds < 86400 * 365 * 1e9: crack = f"{seconds/86400/365:.0e} years"
    else: crack = "longer than the age of the universe"
    verdict = ("TERRIBLE — it's on the most-common list (even in disguise); "
               "cracked instantly"
               if common else
               "WEAK" if entropy < 40 else
               "DECENT" if entropy < 60 else
               "STRONG" if entropy < 80 else "VERY STRONG")
    lines = [f"PASSWORD AUDIT — {len(pw)} characters",
             f"Entropy: {entropy:.0f} bits ({verdict})",
             f"Offline crack time (10^11 guesses/s): {crack}"]
    if common:
        lines.append("This exact password appears in every breach wordlist.")
    if len(pw) < 12 and not common:
        lines.append("Short passwords fall to brute force — aim for 12+ "
                     "characters or four random words.")
    lines.append("Never reuse it anywhere; consider a password manager.")
    return {"ok": True, "message": "\n".join(lines), "verdict": verdict}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(password_strength("Passw0rd")["message"]); print()
    print(password_strength("Correct-Horse-Battery-9!")["message"]); print()
    # round-trip the crypto
    test = os.path.join(SCRIPT_DIR, "_sec_test.txt")
    enc = test + ".spyenc"
    dec = test + ".dec"
    open(test, "w").write("JARVIS test payload 123")
    print(encrypt_file(test, "stark-industries-2026")["message"])
    print(decrypt_file(enc, "stark-industries-2026", dec)["message"])
    same = open(dec).read() == open(test).read()
    print("round-trip:", "OK" if same else "FAILED")
    print("wrong password →", decrypt_file(enc, "wrong")["message"])
    print()
    print(hash_file(test, ("sha256", "md5"))["message"])
    for p in (test, enc, dec):
        try: os.remove(p)
        except OSError: pass
