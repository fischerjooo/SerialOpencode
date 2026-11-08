#!/usr/bin/env python3
"""OpenCode 1.x Bluetooth remote bridge. Paired devices are trusted.

No network listener is opened. Optional existing OpenCode server credentials
are read from the environment; the default is password-free loopback IPC.
Version 3 adds acknowledged JSON commands while retaining terminal commands.
"""

from __future__ import annotations

from remote import RemoteCommands
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    import serial
except ImportError:
    print("Missing dependency: run  py -3 -m pip install pyserial", file=sys.stderr)
    sys.exit(2)


MAX_INPUT_BYTES = 32768
MAX_MESSAGE_CHARACTERS = 12000
SESSION_LIST_LIMIT = 100


class APIError(RuntimeError):
    pass


class OpenCodeAPI:
    """OpenCode API client with optional Basic auth, restricted to loopback.

    Explicit ProxyHandler({}) is important on proxy-managed Windows PCs:
    HTTP to 127.0.0.1 is a local IPC connection, not a corporate web request.
    """

    def __init__(self, port: int, password: str = "", username: str = "opencode") -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
        self.authorization = f"Basic {token}" if password else None

    def request(self, method: str, path: str, payload: Any = None) -> Any:
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("API path must be an absolute local path")
        url = self.base + path
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if self.authorization:
            headers["Authorization"] = self.authorization
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=8.0) as response:
                body = response.read(8 * 1024 * 1024 + 1)
                if len(body) > 8 * 1024 * 1024:
                    raise APIError("OpenCode response exceeds 8 MiB")
                return json.loads(body) if body else None
        except urllib.error.HTTPError as exc:
            raw = exc.read(350).decode("utf-8", errors="replace").strip()
            if exc.code == 401:
                raise APIError("OpenCode authentication failed: wrong server password") from exc
            raise APIError(f"OpenCode HTTP {exc.code}: {raw[:200]}") from exc
        except urllib.error.URLError as exc:
            raise APIError(f"Cannot reach OpenCode at {self.base}: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise APIError("OpenCode did not respond within 8 seconds") from exc
        except (ValueError, UnicodeError) as exc:
            raise APIError(f"OpenCode returned invalid JSON: {exc}") from exc


def message_text(entry: dict[str, Any]) -> str:
    """Only text message parts; do not forward reasoning or tool payloads."""
    parts = entry.get("parts") or []
    texts = [part.get("text", "") for part in parts
             if isinstance(part, dict) and part.get("type") == "text"]
    return "\n".join(piece for piece in texts if isinstance(piece, str) and piece)


def safe_line(s: str) -> str:
    # Strip terminal control chars except newline and tab; avoid escape sequences.
    s = "".join(ch for ch in s if ch in "\n\t" or (ord(ch) >= 32 and ord(ch) != 127))
    if len(s) > MAX_MESSAGE_CHARACTERS:
        return s[:MAX_MESSAGE_CHARACTERS] + "\n[Message truncated for Bluetooth display]"
    return s


def timestamp(session: dict[str, Any]) -> int:
    value = session.get("time", {})
    if isinstance(value, dict):
        value = value.get("updated", value.get("created", 0))
    return int(value or 0) if isinstance(value, (int, float)) else 0


class BluetoothBridge:
    def __init__(self, port: Any, api: OpenCodeAPI, poll_interval: float):
        self.port = port
        self.api = api
        self.poll_interval = poll_interval
        self.buffer = bytearray()
        self.sessions: list[dict[str, Any]] = []
        self.session_id: str | None = None
        self.seen: dict[str, str] = {}
        self.last_poll_time = 0.0
        self.remote = RemoteCommands(self)
        self.busy = False
        self.discarding = False
        self.ui_json = False  # Upgraded Android app opts in with /ui json.

    def write_all(self, data: bytes) -> None:
        while data:
            written = self.port.write(data)
            if written is None:  # Compatibility with adapters that consume the full buffer.
                return
            if written <= 0:
                raise OSError("Bluetooth write made no progress")
            data = data[written:]

    def feed(self, data: bytes) -> None:
        """Discard oversized frames through LF, including any dangerous command tail."""
        for byte in data:
            if byte == 10:
                if not self.discarding:
                    try:
                        self.command(self.buffer.decode("utf-8", errors="strict"))
                    except (APIError, ValueError, UnicodeError) as exc:
                        self.send_event("error", {"message": str(exc)}) if self.ui_json else self.send(str(exc))
                self.buffer.clear()
                self.discarding = False
            elif not self.discarding:
                self.buffer.append(byte)
                if len(self.buffer) > MAX_INPUT_BYTES:
                    self.buffer.clear()
                    self.discarding = True
                    self.send("Input too long; frame discarded through next LF.")

    def send_event(self, event: str, fields: dict[str, Any] | None = None) -> None:
        """One UTF-8 JSON frame per line, compatible with Android's readLine()."""
        frame = {"type": event}
        frame.update(fields or {})
        if event in ("message", "message_delta", "history_begin", "history_end"):
            frame["sessionId"] = self.session_id
        text = "@OCBT1 " + json.dumps(frame, ensure_ascii=False, separators=(",", ":")) + "\r\n"
        data = text.encode("utf-8")
        for start in range(0, len(data), 256):
            self.write_all(data[start:start + 256])

    def send(self, text: str) -> None:
        """Write UTF-8 text with CRLF line endings for Android terminal apps."""
        if self.ui_json:
            self.send_event("notice", {"text": safe_line(str(text))})
            return
        text = safe_line(str(text)).replace("\r\n", "\n").replace("\r", "\n")
        data = (text.replace("\n", "\r\n") + "\r\n").encode("utf-8", errors="replace")
        # Small blocks make it friendlier to low-throughput Bluetooth serial links.
        for start in range(0, len(data), 256):
            self.write_all(data[start:start + 256])

    def sessions_command(self) -> None:
        sessions = self.api.request("GET", "/session")
        if not isinstance(sessions, list):
            raise APIError(f"Unexpected /session response: {type(sessions).__name__}")
        latest = sorted(
            (s for s in sessions if isinstance(s, dict) and isinstance(s.get("id"), str)),
            key=timestamp,
            reverse=True,
        )
        # Newly created sessions may not be returned by /session immediately.
        # Keep the selected session visible until the server's listing catches up.
        active = next((s for s in self.sessions if s["id"] == self.session_id), None)
        if active and not any(s["id"] == self.session_id for s in latest):
            latest.insert(0, active)
        self.sessions = latest[:SESSION_LIST_LIMIT]
        self.send_sessions()

    def send_sessions(self) -> None:
        """Send the current numbered list, without querying the server again."""
        if not self.sessions:
            if self.ui_json:
                self.send_event("sessions", {"items": []})
            else:
                self.send("No sessions found. Start/resume a session on the PC first.")
            return
        if self.ui_json:
            self.send_event("sessions", {"items": [
                {"index": n, "title": str(sess.get("title") or "Untitled")[:75],
                 "id": sess["id"], "active": sess["id"] == self.session_id}
                for n, sess in enumerate(self.sessions, 1)
            ]})
            return
        self.send("Sessions (most recent first):")
        for n, session in enumerate(self.sessions, 1):
            active = " *" if session["id"] == self.session_id else ""
            title = str(session.get("title") or "Untitled").replace("\r", " ").replace("\n", " ")
            self.send(f"{n}. {title[:75]}{active}")
        self.send("Select with /use 1 (or another number).")

    def select_session(self, arg: str) -> None:
        if not self.sessions:
            self.sessions_command()
        try:
            index = int(arg)
        except ValueError:
            self.send("Usage: /use 1")
            return
        if index < 1 or index > len(self.sessions):
            self.send(f"Choose a session number from 1 to {len(self.sessions)}.")
            return
        session = self.sessions[index - 1]
        self.session_id = session["id"]
        self.seen.clear()
        self.remote.pending = None
        if self.ui_json:
            self.send_event("selected", {"id": self.session_id, "title": session.get("title") or "Untitled"})
        else:
            self.send(f"Selected: {session.get('title', 'Untitled')}")
        self.history_command(6)
        self.last_poll_time = time.monotonic()

    def auto_select_newest(self, sessions: list[dict[str, Any]]) -> None:
        valid = [s for s in sessions if isinstance(s, dict) and isinstance(s.get("id"), str)]
        if not valid:
            print("No existing session found; send /new on the phone to start one.")
            return
        self.sessions = sorted(valid, key=timestamp, reverse=True)[:SESSION_LIST_LIMIT]
        self.session_id = self.sessions[0]["id"]
        self.seen.clear()
        self.remote.pending = None
        self.last_poll_time = time.monotonic()
        title = self.sessions[0].get("title") or "Untitled"
        print(f"Auto-connected to newest session: {title} ({self.session_id})")

    def new_session(self, arg: str) -> None:
        payload: dict[str, Any] = {}
        title = arg.strip()
        if title:
            payload["title"] = title
        session = self.api.request("POST", "/session", payload)
        if not isinstance(session, dict) or not isinstance(session.get("id"), str):
            raise APIError("Unexpected /session create response")
        self.session_id = session["id"]
        # Include the session in the list *before* emitting events; we must not
        # depend on /session returning the newly created item right away.
        self.sessions = ([session] + [s for s in self.sessions if s["id"] != self.session_id])[:SESSION_LIST_LIMIT]
        self.seen.clear()
        self.remote.pending = None
        self.last_poll_time = time.monotonic()
        if self.ui_json:
            self.send_event("selected", {"id": self.session_id, "title": session.get("title") or "Untitled"})
            self.send_sessions()
            self.send_event("history_begin")
            self.send_event("history_end")
        else:
            self.send(f"Selected: {session.get('title') or 'Untitled'}")
            self.send_sessions()
            self.send("Send any text to start chatting.")

    def fetch_messages(self) -> list[dict[str, Any]]:
        if not self.session_id:
            raise APIError("No session selected. Type /sessions then /use 1.")
        path = "/session/" + urllib.parse.quote(self.session_id, safe="") + "/message?limit=50"
        messages = self.api.request("GET", path)
        if not isinstance(messages, list):
            raise APIError("Unexpected messages response")
        return [m for m in messages if isinstance(m, dict)]

    def history_command(self, count: int) -> None:
        messages = self.fetch_messages()
        # Seed all currently fetched messages to prevent duplicate replay by polling.
        self.seen = {
            str((m.get("info") or {}).get("id", f"unknown-{i}")): message_text(m)
            for i, m in enumerate(messages)
        }
        shown = 0
        if self.ui_json:
            self.send_event("history_begin")
            for i, message in enumerate(messages[-count:]):
                info = message.get("info") or {}
                role = str(info.get("role", "unknown")).lower()
                content = message_text(message)
                if role in ("user", "assistant") and content:
                    self.send_event("message", {
                        "id": str(info.get("id", f"history-{i}")),
                        "role": role, "text": safe_line(content)
                    })
            self.send_event("history_end")
            return
        self.send(f"--- Last {count} chat messages ---")
        for m in messages[-count:]:
            info = m.get("info") or {}
            role = str(info.get("role", "unknown")).upper()
            content = message_text(m)
            if content:
                self.send(f"[{role}]\n{content}")
                shown += 1
        if not shown:
            self.send("No text messages in this session yet.")
        self.send("--- End history ---")

    def poll(self) -> None:
        if not self.session_id:
            return
        messages = self.fetch_messages()
        live_ids = {str((m.get("info") or {}).get("id", f"unknown-{i}")) for i, m in enumerate(messages)}
        self.seen = {k: v for k, v in self.seen.items() if k in live_ids}
        for i, m in enumerate(messages):
            info = m.get("info") or {}
            role = info.get("role")
            if role not in ("user", "assistant"):
                continue
            ident = str(info.get("id", f"unknown-{i}"))
            current = message_text(m)
            before = self.seen.get(ident)
            if before is None:
                self.seen[ident] = current
                if current:
                    if self.ui_json:
                        self.send_event("message", {"id": ident, "role": role, "text": safe_line(current)})
                    else:
                        self.send(f"\n[{role.upper()}]\n{current}")
            elif current != before:
                self.seen[ident] = current
                if self.ui_json:
                    self.send_event("message_delta", {"id": ident, "role": role, "text": current[len(before):]}) if current.startswith(before) and len(current) <= MAX_MESSAGE_CHARACTERS else self.send_event("message", {"id": ident, "role": role, "text": safe_line(current)})
                elif current.startswith(before):
                    addition = current[len(before):]
                    if addition:
                        self.send(f"[{role.upper()} continues]\n{addition}")
                else:
                    self.send(f"[{role.upper()} updated]\n{current}")

    def status_command(self) -> None:
        if not self.session_id:
            self.send("No session selected. Type /sessions.")
            return
        try:
            states = self.api.request("GET", "/session/status")
            state = states.get(self.session_id, "idle/unknown") if isinstance(states, dict) else "unknown"
            if isinstance(state, dict):
                state = state.get("type", "unknown")
            self.busy = state == "busy"
            if self.ui_json:
                self.send_event("status", {"id": self.session_id, "status": state})
            else:
                self.send(f"Current session: {self.session_id}\nStatus: {state}")
        except APIError as exc:
            self.send(f"Status unavailable: {exc}")

    def command(self, raw: str) -> None:
        line = raw.strip()
        if line.startswith("@OCBT1 "):
            self.ui_json = True
            self.remote.handle(line[7:])
            return
        if not line:
            return

        if line == "/ui json":
            self.ui_json = True
            self.send_event("hello", {"version": 3, "transport": "Bluetooth RFCOMM", "auth": False})
        elif line == "/ui text":
            self.ui_json = False
            self.send("Text terminal protocol enabled.")
        elif line == "/help":
            self.send("Commands: /new [title], /sessions, /use N, /history [N], /status, /help, /ui [json|text]")
            self.send("Any other text sends a prompt to the selected OpenCode session.")
        elif line == "/new" or line.startswith("/new "):
            self.new_session(line[len("/new"):])
        elif line == "/sessions":
            self.sessions_command()
        elif line.startswith("/use "):
            self.select_session(line[5:].strip())
        elif line == "/history" or line.startswith("/history "):
            arg = line[len("/history"):].strip()
            if arg and (not arg.isdigit() or not 1 <= int(arg) <= 20):
                self.send("Usage: /history [1..20]")
                return
            if not self.session_id:
                self.send("Select a session first: /sessions then /use 1")
                return
            self.history_command(int(arg) if arg else 8)
        elif line == "/status":
            self.status_command()
        elif line.startswith("/"):
            self.send("Unknown bridge command. Type /help.")
        else:
            if not self.session_id:
                self.send("Select a session first: /sessions then /use 1")
            elif len(line) > 4000:
                self.send("Prompt too long for this bridge (max 4000 characters).")
            else:
                self.api.request("POST", "/session/" + urllib.parse.quote(self.session_id, safe="") + "/prompt_async",
                                 {"parts": [{"type": "text", "text": line}]})
                if self.ui_json:
                    self.send_event("prompt_accepted")
                else:
                    self.send("Prompt accepted by OpenCode. Watching for replies...")

    def run(self) -> None:
        print("Bluetooth bridge listening. Pair/connect your Android SPP terminal.")
        print("Phone setup: send line ending LF; text encoding UTF-8.")
        print("Bluetooth bridge ready; no one-time token is required.")
        print("Press Ctrl+C to quit. No HTTP port is opened by this script.")
        try:
            while True:
                n = min(max(self.port.in_waiting, 1), 1024)
                data = self.port.read(n)
                if data:
                    self.feed(data)
                now = time.monotonic()
                if self.session_id and now - self.last_poll_time >= (self.poll_interval if self.busy else max(3, self.poll_interval)):
                    self.last_poll_time = now
                    try:
                        self.poll()
                        self.remote.poll()
                    except (APIError, ValueError) as exc:
                        print(f"OpenCode polling error: {exc}")
                        self.send(f"OpenCode connection issue: {exc}")
                        # Avoid rapidly repeating the same error on the phone.
                        self.last_poll_time = time.monotonic() + 8
        except KeyboardInterrupt:
            print("\nStopped by user")


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenCode 1.x Bluetooth serial chat bridge")
    parser.add_argument("--com", default="COM3", help="Windows incoming Bluetooth COM port (default COM3)")
    parser.add_argument("--api-port", type=int, default=4096, help="OpenCode loopback HTTP port (default 4096)")
    parser.add_argument("--poll", type=float, default=0.5, help="Chat polling interval in seconds (default 0.5)")
    args = parser.parse_args()
    if not (1 <= args.api_port <= 65535 and 0.5 <= args.poll <= 60):
        parser.error("API port must be 1..65535 and poll interval 0.5..60 seconds")
    password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")
    api = OpenCodeAPI(args.api_port, password)
    try:
        sessions = api.request("GET", "/session")
        if not isinstance(sessions, list):
            raise APIError("OpenCode /session endpoint returned an unexpected payload")
    except APIError as exc:
        print(f"Cannot connect to OpenCode: {exc}", file=sys.stderr)
        print("Start 'opencode serve --hostname 127.0.0.1 --port 4096' in your project folder first.", file=sys.stderr)
        return 1
    print(f"OpenCode connected at http://127.0.0.1:{args.api_port}; {len(sessions)} sessions visible.")
    try:
        with serial.Serial(args.com, baudrate=9600, timeout=0.25, write_timeout=8) as conn:
            print(f"Opened Windows Bluetooth serial port {args.com}.")
            bridge = BluetoothBridge(conn, api, args.poll)
            bridge.auto_select_newest(sessions)
            bridge.run()
    except (serial.SerialException, OSError, RuntimeError) as exc:
        print(f"Bridge stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())