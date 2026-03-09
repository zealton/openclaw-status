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
STATE_ROOT = pathlib.Path("/Users/leelark/.openclaw/agents")

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


def summarize_user_text(raw: str) -> str:
    text = raw
    if "```" in text:
        parts = text.split("```")
        text = parts[-1]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    filtered = [
        line
        for line in lines
        if not line.startswith("Conversation info")
        and not line.startswith("Sender")
        and not line.startswith("{")
        and not line.startswith("}")
        and not line.startswith('"')
    ]
    if not filtered:
        filtered = lines
    joined = " ".join(filtered).strip()
    return joined[:160] if joined else "(empty)"


def infer_task_state(events: List[dict]) -> str:
    if not events:
        return "unknown"
    last = events[-1]
    message = last.get("message", {})
    role = message.get("role")
    if role == "toolResult":
        return "working"
    if role == "assistant":
        content = message.get("content", [])
        if any(item.get("type") == "toolCall" for item in content if isinstance(item, dict)):
            return "working"
        if message.get("stopReason") == "error":
            return "error"
        return "idle"
    if role == "user":
        return "queued"
    return "unknown"


def extract_skill_names_from_text(text: str) -> List[str]:
    names = []
    for match in re.findall(r"/skills/([^/\n]+)/SKILL\.md", text):
        names.append(match)
    return names


def extract_recent_skills(events: List[dict]) -> List[str]:
    found: List[str] = []
    seen = set()
    for event in events[-60:]:
        message = event.get("message", {})
        content = message.get("content", [])
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "toolCall":
                args = item.get("arguments", {})
                if isinstance(args, dict):
                    for value in args.values():
                        if isinstance(value, str):
                            for name in extract_skill_names_from_text(value):
                                if name not in seen:
                                    seen.add(name)
                                    found.append(name)
            elif item.get("type") == "text":
                for name in extract_skill_names_from_text(item.get("text", "")):
                    if name not in seen:
                        seen.add(name)
                        found.append(name)
    return found[:8]


def extract_active_tasks() -> List[dict]:
    tasks: List[dict] = []
    cutoff_ms = int((dt.datetime.utcnow() - dt.timedelta(hours=6)).timestamp() * 1000)
    for sessions_path in STATE_ROOT.glob("*/sessions/sessions.json"):
        try:
            sessions = json.loads(sessions_path.read_text())
        except Exception:
            continue
        for session_key, meta in sessions.items():
            updated_at = int(meta.get("updatedAt") or 0)
            if updated_at < cutoff_ms:
                continue
            session_file = meta.get("sessionFile")
            if not session_file or not pathlib.Path(session_file).exists():
                continue
            lines = pathlib.Path(session_file).read_text(errors="ignore").splitlines()[-200:]
            events = []
            for line in lines:
                try:
                    events.append(json.loads(line))
                except Exception:
                    continue
            last_user = None
            for event in reversed(events):
                message = event.get("message", {})
                if message.get("role") != "user":
                    continue
                content = message.get("content", [])
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        last_user = summarize_user_text(item.get("text", ""))
                        break
                if last_user:
                    break
            tasks.append(
                {
                    "agent": session_key.split(":")[1] if ":" in session_key else "unknown",
                    "session_key": session_key,
                    "updated_at_ms": updated_at,
                    "updated_at_local": dt.datetime.fromtimestamp(updated_at / 1000).strftime("%Y-%m-%d %H:%M:%S"),
                    "state": infer_task_state(events),
                    "task": last_user or "No recent user task found",
                    "skills": extract_recent_skills(events),
                    "available_skills": [
                        entry.get("name")
                        for entry in meta.get("skillsSnapshot", {}).get("entries", [])
                        if isinstance(entry, dict) and entry.get("name")
                    ][:12],
                }
            )
    tasks.sort(key=lambda item: item["updated_at_ms"], reverse=True)
    return tasks[:8]


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
        "active_tasks": extract_active_tasks(),
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
