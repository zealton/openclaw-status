#!/usr/bin/env python3
import datetime as dt
import json
import pathlib
import re
import socket
import subprocess
from typing import List, Optional


ROOT = pathlib.Path("/Users/leelark/openclaw-status")
OUT = ROOT / "status.json"
GATEWAY_LOG = pathlib.Path("/Users/leelark/.openclaw/logs/gateway.log")
WATCHDOG_LOG = pathlib.Path("/Users/leelark/.openclaw/logs/gateway-watchdog.log")
CAFFEINATE_STDERR = pathlib.Path("/Users/leelark/.openclaw/logs/caffeinate.stderr.log")

LABELS = {
    "openclaw": "gui/501/ai.openclaw.gateway",
    "watchdog": "gui/501/ai.openclaw.gateway-watchdog",
    "caffeinate": "gui/501/ai.openclaw.caffeinate",
    "notion_sync": "gui/501/ai.openclaw.notion-daily-sync",
}


def launchctl_info(label: str) -> dict:
    result = subprocess.run(
        ["launchctl", "print", label],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0:
        return {"loaded": False, "state": "not loaded", "pid": None}
    text = result.stdout
    state = re.search(r"state = ([^\n]+)", text)
    pid = re.search(r"\bpid = (\d+)", text)
    return {
        "loaded": True,
        "state": state.group(1).strip() if state else "unknown",
        "pid": int(pid.group(1)) if pid else None,
    }


def tail(path: pathlib.Path, limit: int = 8) -> List[str]:
    if not path.exists():
        return []
    lines = path.read_text(errors="ignore").splitlines()
    return lines[-limit:]


def last_matching(lines: List[str], needle: str) -> Optional[str]:
    for line in reversed(lines):
        if needle in line:
            return line
    return None


def main() -> int:
    services = {name: launchctl_info(label) for name, label in LABELS.items()}
    gateway_lines = tail(GATEWAY_LOG, 80)

    model_line = last_matching(gateway_lines, "agent model:")
    model = None
    if model_line:
        model = model_line.split("agent model:", 1)[-1].strip()

    telegram_line = last_matching(gateway_lines, "starting provider (@")
    browser_line = last_matching(gateway_lines, "Browser control listening on")
    watchdog_lines = tail(WATCHDOG_LOG, 6)

    payload = {
        "generated_at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "generated_at_local": dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "stale_after_seconds": 180,
        "host": socket.gethostname(),
        "openclaw": {
            "running": services["openclaw"]["state"] == "running",
            "pid": services["openclaw"]["pid"],
            "model": model,
        },
        "telegram": {
            "status": "running" if telegram_line else "unknown",
            "detail": telegram_line or "No recent telegram startup log",
        },
        "browser": {
            "status": "running" if browser_line else "unknown",
            "detail": browser_line or "No recent browser relay log",
        },
        "caffeinate": {
            "status": "running" if services["caffeinate"]["state"] == "running" else "stopped",
            "detail": "Headless keep-awake service",
        },
        "watchdog": {
            "status": "running" if services["watchdog"]["loaded"] else "stopped",
            "detail": watchdog_lines[-1] if watchdog_lines else "No watchdog events yet",
        },
        "notion_sync": {
            "status": "scheduled" if services["notion_sync"]["loaded"] else "missing",
            "detail": "Midnight daily summary job",
        },
        "services": services,
        "recent_log": gateway_lines[-8:],
        "notes": [],
    }

    if services["openclaw"]["state"] != "running":
        payload["notes"].append("OpenClaw gateway is not running.")
    if services["watchdog"]["loaded"]:
        payload["notes"].append("Gateway watchdog checks launchd every 60 seconds.")
    if CAFFEINATE_STDERR.exists() and CAFFEINATE_STDERR.read_text(errors="ignore").strip():
        payload["notes"].append("Caffeinate stderr is non-empty; inspect logs if sleep issues return.")

    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
