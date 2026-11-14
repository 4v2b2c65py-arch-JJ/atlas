"""ATLAS beacon — LAN radio identification + peer discovery.

- Identifies the WiFi radio the hub is on (SSID, channel, frequency).
- Broadcasts a signed-ish JSON beacon over UDP so other ATLAS hubs on
  the network can find it and merge it as a peer automatically.
- Listens for peer beacons and registers their hub URLs.
"""

import json
import socket
import subprocess
import threading
import time
from pathlib import Path

BEACON_PORT = 8918
BEACON_INTERVAL = 10.0
ROOT = Path(__file__).parent
PEER_HUBS_PATH = ROOT / "data" / "peer_hubs.json"


def wifi_info() -> dict:
    """Identify the radio path: interface, link type, SSID/channel/freq
    when Wi-Fi is associated. Works on modern macOS (no airport binary)."""
    info = {"radio": "unknown", "interface": "en0"}
    try:
        out = subprocess.run(["route", "-n", "get", "default"],
                             capture_output=True, text=True,
                             timeout=5).stdout
        for line in out.splitlines():
            if "interface:" in line:
                info["interface"] = line.split(":", 1)[1].strip()
    except Exception:
        pass
    try:
        net = subprocess.run(
            ["networksetup", "-getairportnetwork", info["interface"]],
            capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        net = ""
    if "Current Wi-Fi Network:" in net:
        info["ssid"] = net.split(":", 1)[1].strip()
        info["associated"] = True
        info["radio"] = "wifi"
        # pull channel from system_profiler
        try:
            sp = subprocess.run(["system_profiler", "SPAirPortDataType"],
                                capture_output=True, text=True,
                                timeout=10).stdout
            idx = sp.find("Current Network Information")
            seg = sp[idx:idx + 2000] if idx >= 0 else sp
            for line in seg.splitlines():
                if "Channel:" in line:
                    ch = int(line.split("Channel:")[1].strip().split()[0])
                    info["channel"] = ch
                    info["frequency_mhz"] = (2400 + ch * 5) if ch < 15 \
                        else (5000 + ch * 5)
                    break
        except Exception:
            pass
    else:
        info["associated"] = False
        info["radio"] = "ethernet"
    return info


def _load_hubs() -> list:
    try:
        return json.loads(PEER_HUBS_PATH.read_text())
    except Exception:
        return []


def _save_hubs(hubs: list):
    PEER_HUBS_PATH.parent.mkdir(exist_ok=True)
    PEER_HUBS_PATH.write_text(json.dumps(hubs[-20:]))


def register_hub(url: str):
    hubs = _load_hubs()
    if url not in hubs:
        hubs.append(url)
        _save_hubs(hubs)
    return hubs


def _hub_url() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return f"http://{ip}:8917"


def beacon_payload(identity_name: str = "ATLAS") -> dict:
    w = wifi_info()
    return {
        "type": "atlas-beacon",
        "hub": _hub_url(),
        "identity": identity_name,
        "freq": w.get("frequency_mhz"),
        "channel": w.get("channel"),
        "ssid": w.get("ssid"),
        "ts": time.time(),
    }


def _listener(on_peer):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except Exception:
        pass
    sock.bind(("", BEACON_PORT))
    sock.settimeout(2.0)
    while True:
        try:
            data, addr = sock.recvfrom(4096)
            msg = json.loads(data.decode())
            if msg.get("type") == "atlas-beacon" and msg.get("hub"):
                if on_peer:
                    on_peer(msg, addr)
        except socket.timeout:
            continue
        except Exception:
            continue


def _broadcaster(stop: threading.Event, identity_name: str):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    payload = json.dumps(beacon_payload(identity_name)).encode()
    hubs = _load_hubs()
    targets = ["255.255.255.255"] + [
        h.split("//")[1].split(":")[0] for h in hubs if "://" in h]
    while not stop.is_set():
        for t in set(targets):
            try:
                sock.sendto(payload, (t, BEACON_PORT))
            except Exception:
                pass
        time.sleep(BEACON_INTERVAL)


def start(identity_name: str = "ATLAS", on_peer=None):
    """Start beacon listener + broadcaster threads (daemon)."""
    stop = threading.Event()
    def _peer(msg, addr):
        hub = msg["hub"]
        if hub != _hub_url():
            register_hub(hub)
            if on_peer:
                on_peer(msg)
    threading.Thread(target=_listener, args=(_peer,), daemon=True).start()
    threading.Thread(target=_broadcaster, args=(stop, identity_name),
                     daemon=True).start()
    return {"port": BEACON_PORT, "interval": BEACON_INTERVAL,
            "wifi": wifi_info(), "hub": _hub_url()}
