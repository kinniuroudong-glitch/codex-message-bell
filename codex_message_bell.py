#!/usr/bin/env python3
"""Play a macOS sound when a Codex task finishes."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DB = Path.home() / ".codex" / "sqlite" / "codex-dev.db"
DEFAULT_SOUND = Path.home() / "Library" / "Sounds" / "codex-done-reply.aiff"
STATE_FILE = Path.home() / ".codex-message-bell-state"
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "local.codex-message-bell.plist"
DEFAULT_VOICE_TEXT = "我做好了，请回复"


@dataclass(frozen=True)
class Snapshot:
    max_thread_updated_at: float
    unread_inbox_count: int
    max_inbox_created_at: int
    file_count: int = 0
    max_file_mtime_ns: int = 0
    total_file_size: int = 0
    max_task_completed_at: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Play a macOS sound when Codex writes a task_complete event."
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="Codex sqlite DB path.")
    parser.add_argument("--sound", type=Path, default=DEFAULT_SOUND, help="Sound file to play.")
    parser.add_argument("--interval", type=float, default=2.0, help="Polling interval in seconds.")
    parser.add_argument("--cooldown", type=float, default=5.0, help="Minimum seconds between sounds.")
    parser.add_argument(
        "--finish-delay",
        type=float,
        default=2.0,
        help="Seconds to wait after task_complete before playing the sound.",
    )
    parser.add_argument("--rings", type=int, default=1, help="How many times to ring after a Codex task completes.")
    parser.add_argument(
        "--file-window-minutes",
        type=float,
        default=1440.0,
        help="Only watch Codex files modified within this many minutes.",
    )
    parser.add_argument("--once", action="store_true", help="Check once and exit.")
    parser.add_argument("--install", action="store_true", help="Install and start a LaunchAgent.")
    parser.add_argument("--uninstall", action="store_true", help="Stop and remove the LaunchAgent.")
    parser.add_argument("--test-sound", action="store_true", help="Play the configured sound and exit.")
    parser.add_argument("--verbose", action="store_true", help="Print detected changes.")
    return parser.parse_args()


def watched_roots() -> list[Path]:
    return [
        Path.home() / ".codex" / "sessions",
        Path.home() / ".codex" / "session_index.jsonl",
        Path.home() / ".codex" / "process_manager",
        Path.home() / "Library" / "Application Support" / "com.openai.chat",
        Path.home() / "Library" / "Application Support" / "Codex" / "Default" / "Local Storage",
        Path.home() / "Library" / "Application Support" / "Codex" / "Default" / "Session Storage",
        Path.home() / "Library" / "Application Support" / "Codex" / "Default" / "Partitions" / "codex-browser-app",
    ]


def session_files(window_minutes: float) -> list[Path]:
    root = Path.home() / ".codex" / "sessions"
    if not root.exists():
        return []

    newest_allowed = time.time() - window_minutes * 60
    files: list[Path] = []
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime >= newest_allowed:
                files.append(path)
        except OSError:
            continue
    return files


def get_max_task_completed_at(window_minutes: float) -> int:
    max_completed_at = 0
    for path in session_files(window_minutes):
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"task_complete"' not in line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = item.get("payload") or {}
            if payload.get("type") != "task_complete":
                continue
            completed_at = payload.get("completed_at") or 0
            try:
                max_completed_at = max(max_completed_at, int(completed_at))
            except (TypeError, ValueError):
                continue
    return max_completed_at


def should_ignore(path: Path) -> bool:
    ignored_parts = {
        "__pycache__",
        "Cache",
        "GPUCache",
        "DawnGraphiteCache",
        "DawnWebGPUCache",
        "GraphiteDawnCache",
        "Code Cache",
        "Crashpad",
        "sentry",
        "shell_snapshots",
        "tmp",
    }
    return any(part in ignored_parts for part in path.parts)


def get_file_snapshot(window_minutes: float) -> tuple[int, int, int]:
    newest_allowed = time.time() - window_minutes * 60
    file_count = 0
    max_mtime_ns = 0
    total_size = 0

    for root in watched_roots():
        if not root.exists():
            continue
        paths: list[Path]
        if root.is_file():
            paths = [root]
        else:
            paths = []
            for dirpath, dirnames, filenames in os.walk(root):
                dir_path = Path(dirpath)
                dirnames[:] = [name for name in dirnames if not should_ignore(dir_path / name)]
                if should_ignore(dir_path):
                    continue
                paths.extend(dir_path / filename for filename in filenames)

        for path in paths:
            if should_ignore(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            if stat.st_mtime < newest_allowed:
                continue
            file_count += 1
            max_mtime_ns = max(max_mtime_ns, stat.st_mtime_ns)
            total_size += stat.st_size

    return file_count, max_mtime_ns, total_size


def get_snapshot(db_path: Path, window_minutes: float) -> Snapshot:
    file_count, max_file_mtime_ns, total_file_size = get_file_snapshot(window_minutes)
    max_task_completed_at = get_max_task_completed_at(window_minutes)

    if not db_path.exists():
        return Snapshot(0, 0, 0, file_count, max_file_mtime_ns, total_file_size, max_task_completed_at)

    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=1.0) as conn:
        max_thread_updated_at = conn.execute(
            "SELECT COALESCE(MAX(source_updated_at), 0) FROM local_thread_catalog "
            "WHERE missing_candidate = 0"
        ).fetchone()[0]
        unread_inbox_count, max_inbox_created_at = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(created_at), 0) FROM inbox_items WHERE read_at IS NULL"
        ).fetchone()

    return Snapshot(
        max_thread_updated_at=float(max_thread_updated_at or 0),
        unread_inbox_count=int(unread_inbox_count or 0),
        max_inbox_created_at=int(max_inbox_created_at or 0),
        file_count=file_count,
        max_file_mtime_ns=max_file_mtime_ns,
        total_file_size=total_file_size,
        max_task_completed_at=max_task_completed_at,
    )


def play_sound(sound_path: Path) -> None:
    ensure_default_sound(sound_path)
    if sound_path.exists():
        subprocess.run(["afplay", str(sound_path)], check=False)
        return

    # Fallback to a built-in macOS alert sound.
    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)


def ensure_default_sound(sound_path: Path) -> None:
    if sound_path != DEFAULT_SOUND or sound_path.exists() or not shutil.which("say"):
        return
    sound_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["say", "-v", "Tingting", "-r", "185", "-o", str(sound_path), "--", DEFAULT_VOICE_TEXT],
        check=False,
    )


def load_seen_snapshot() -> Snapshot | None:
    if not STATE_FILE.exists():
        return None
    try:
        parts = STATE_FILE.read_text().strip().split(",")
        if len(parts) == 3:
            thread_updated, unread_count, inbox_created = parts
            return Snapshot(float(thread_updated), int(unread_count), int(inbox_created))
        if len(parts) == 6:
            thread_updated, unread_count, inbox_created, file_count, file_mtime, file_size = parts
            return Snapshot(
                float(thread_updated),
                int(unread_count),
                int(inbox_created),
                int(file_count),
                int(file_mtime),
                int(file_size),
            )
        thread_updated, unread_count, inbox_created, file_count, file_mtime, file_size, task_completed = parts
        return Snapshot(
            float(thread_updated),
            int(unread_count),
            int(inbox_created),
            int(file_count),
            int(file_mtime),
            int(file_size),
            int(task_completed),
        )
    except (OSError, ValueError):
        return None


def save_seen_snapshot(snapshot: Snapshot) -> None:
    STATE_FILE.write_text(
        f"{snapshot.max_thread_updated_at},{snapshot.unread_inbox_count},{snapshot.max_inbox_created_at},"
        f"{snapshot.file_count},{snapshot.max_file_mtime_ns},{snapshot.total_file_size},"
        f"{snapshot.max_task_completed_at}\n"
    )


def has_new_activity(previous: Snapshot, current: Snapshot) -> bool:
    return current.max_task_completed_at > previous.max_task_completed_at


def ring(sound_path: Path, count: int) -> None:
    for index in range(max(1, count)):
        play_sound(sound_path)
        if index < count - 1:
            time.sleep(0.3)


def install_launch_agent(script_path: Path, args: argparse.Namespace) -> None:
    python_path = Path(sys.executable)
    log_dir = Path.home() / "Library" / "Logs" / "codex-message-bell"
    log_dir.mkdir(parents=True, exist_ok=True)
    ensure_default_sound(args.sound.expanduser())
    save_seen_snapshot(get_snapshot(args.db.expanduser(), args.file_window_minutes))
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>local.codex-message-bell</string>
  <key>ProgramArguments</key>
  <array>
    <string>{python_path}</string>
    <string>{script_path}</string>
    <string>--db</string>
    <string>{args.db.expanduser()}</string>
    <string>--sound</string>
    <string>{args.sound.expanduser()}</string>
    <string>--interval</string>
    <string>{args.interval}</string>
    <string>--cooldown</string>
    <string>{args.cooldown}</string>
    <string>--finish-delay</string>
    <string>{args.finish_delay}</string>
    <string>--rings</string>
    <string>{args.rings}</string>
    <string>--file-window-minutes</string>
    <string>{args.file_window_minutes}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{log_dir / "out.log"}</string>
  <key>StandardErrorPath</key>
  <string>{log_dir / "err.log"}</string>
</dict>
</plist>
"""
    LAUNCH_AGENT.write_text(plist)
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", str(LAUNCH_AGENT)], check=False)
    print(f"Installed and started: {LAUNCH_AGENT}")


def uninstall_launch_agent() -> None:
    subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        LAUNCH_AGENT.unlink()
    except FileNotFoundError:
        pass
    print(f"Removed: {LAUNCH_AGENT}")


def main() -> int:
    args = parse_args()
    script_path = Path(__file__).resolve()

    if args.uninstall:
        uninstall_launch_agent()
        return 0
    if args.install:
        install_launch_agent(script_path, args)
        return 0
    if args.test_sound:
        play_sound(args.sound.expanduser())
        return 0

    if not shutil.which("afplay"):
        print("afplay not found; this program needs macOS afplay to make sound.", file=sys.stderr)
        return 2

    db_path = args.db.expanduser()
    previous = load_seen_snapshot() or get_snapshot(db_path, args.file_window_minutes)
    save_seen_snapshot(previous)
    last_sound_at = 0.0

    while True:
        try:
            current = get_snapshot(db_path, args.file_window_minutes)
        except sqlite3.Error as exc:
            if args.verbose:
                print(f"Could not read DB yet: {exc}", flush=True)
            time.sleep(args.interval)
            continue

        if has_new_activity(previous, current):
            now = time.monotonic()
            if now - last_sound_at >= args.cooldown:
                if args.verbose:
                    print(f"Codex task completed: {previous} -> {current}", flush=True)
                time.sleep(args.finish_delay)
                ring(args.sound.expanduser(), args.rings)
                last_sound_at = now
            save_seen_snapshot(current)
            previous = current

        if args.once:
            break
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
