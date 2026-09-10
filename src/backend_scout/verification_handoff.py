"""Temporary, Tailscale-only remote control for a human verification page."""

import ipaddress
import json
import queue
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Self
from urllib.parse import urlparse

TAILSCALE_IPV4_NETWORK = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_IPV6_NETWORK = ipaddress.ip_network("fd7a:115c:a1e0::/48")
MAX_ACTION_BODY_BYTES = 2_048
MAX_TYPED_TEXT_LENGTH = 256
ALLOWED_KEYS = {
    "Tab",
    "Enter",
    "Space",
    "Escape",
    "ArrowUp",
    "ArrowDown",
    "ArrowLeft",
    "ArrowRight",
}


@dataclass(frozen=True)
class HandoffAction:
    kind: str
    x: float | None = None
    y: float | None = None
    delta_y: int | None = None
    key: str | None = None
    text: str | None = None


def is_tailscale_address(value: str) -> bool:
    """Return whether an address belongs to Tailscale's documented address ranges."""
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address in TAILSCALE_IPV4_NETWORK or address in TAILSCALE_IPV6_NETWORK


def find_tailscale_executable() -> Path | None:
    """Find either a PATH-installed CLI or the official macOS application binary."""
    path_command = shutil.which("tailscale")
    candidates = [
        Path(path_command) if path_command else None,
        Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
    ]
    return next((candidate for candidate in candidates if candidate and candidate.is_file()), None)


def discover_tailscale_ipv4(executable: Path | None = None) -> str | None:
    """Return the Mac's active Tailscale IPv4 address, or None when unavailable."""
    command = executable or find_tailscale_executable()
    if command is None:
        return None
    try:
        result = subprocess.run(
            [str(command), "ip", "-4"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    address = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return address if is_tailscale_address(address) else None


class VerificationHandoffServer:
    """Serve cached page screenshots and queue candidate touch actions."""

    def __init__(
        self,
        bind_host: str,
        port: int = 0,
        *,
        allow_loopback_for_tests: bool = False,
    ) -> None:
        address = ipaddress.ip_address(bind_host)
        if not is_tailscale_address(bind_host) and not (
            allow_loopback_for_tests and address.is_loopback
        ):
            raise ValueError("Verification handoff must bind to a Tailscale address")
        self.bind_host = bind_host
        self.port = port
        self.token = secrets.token_urlsafe(32)
        self._actions: queue.SimpleQueue[HandoffAction] = queue.SimpleQueue()
        self._frame_lock = threading.Lock()
        self._frame = b""
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Verification handoff server has not started")
        host = f"[{self.bind_host}]" if ":" in self.bind_host else self.bind_host
        return f"http://{host}:{self._server.server_port}/{self.token}/"

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                parsed = urlparse(self.path)
                if parsed.path == f"/{owner.token}/":
                    self._send(HTTPStatus.OK, _handoff_html().encode(), "text/html; charset=utf-8")
                    return
                if parsed.path == f"/{owner.token}/frame.png":
                    with owner._frame_lock:
                        frame = owner._frame
                    if not frame:
                        self._send(HTTPStatus.SERVICE_UNAVAILABLE, b"Frame not ready", "text/plain")
                        return
                    self._send(HTTPStatus.OK, frame, "image/png")
                    return
                self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")

            def do_POST(self) -> None:
                if urlparse(self.path).path != f"/{owner.token}/action":
                    self._send(HTTPStatus.NOT_FOUND, b"Not found", "text/plain")
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > MAX_ACTION_BODY_BYTES:
                    self._send(HTTPStatus.BAD_REQUEST, b"Invalid request", "text/plain")
                    return
                try:
                    action = _parse_action(json.loads(self.rfile.read(length)))
                except (ValueError, TypeError, json.JSONDecodeError):
                    self._send(HTTPStatus.BAD_REQUEST, b"Invalid action", "text/plain")
                    return
                owner._actions.put(action)
                self._send(HTTPStatus.ACCEPTED, b"Queued", "text/plain")

            def log_message(self, format: str, *args: object) -> None:
                return

            def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' data:; connect-src 'self'",
                )
                self.end_headers()
                self.wfile.write(body)

        self._server = ThreadingHTTPServer((self.bind_host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None

    def publish_frame(self, frame: bytes) -> None:
        with self._frame_lock:
            self._frame = frame

    def drain_actions(self) -> tuple[HandoffAction, ...]:
        actions: list[HandoffAction] = []
        while True:
            try:
                actions.append(self._actions.get_nowait())
            except queue.Empty:
                return tuple(actions)

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def apply_handoff_actions(page, actions: tuple[HandoffAction, ...]) -> None:
    """Apply queued human gestures from the main Playwright thread."""
    for action in actions:
        if action.kind == "click" and action.x is not None and action.y is not None:
            page.mouse.click(action.x, action.y)
        elif action.kind == "scroll" and action.delta_y is not None:
            page.mouse.wheel(0, action.delta_y)
        elif action.kind == "key" and action.key is not None:
            page.keyboard.press(action.key)
        elif action.kind == "type" and action.text:
            page.keyboard.type(action.text)


def _parse_action(payload: object) -> HandoffAction:
    if not isinstance(payload, dict):
        raise TypeError("Action must be an object")
    kind = payload.get("kind")
    if kind == "click":
        x = float(payload["x"])
        y = float(payload["y"])
        if x < 0 or y < 0 or x > 10_000 or y > 10_000:
            raise ValueError("Click is outside the supported viewport")
        return HandoffAction(kind="click", x=x, y=y)
    if kind == "scroll":
        delta_y = max(-1_000, min(1_000, int(payload["delta_y"])))
        return HandoffAction(kind="scroll", delta_y=delta_y)
    if kind == "key":
        key = str(payload["key"])
        if key not in ALLOWED_KEYS:
            raise ValueError("Key is not allowed")
        return HandoffAction(kind="key", key=key)
    if kind == "type":
        text = str(payload["text"])
        if not text or len(text) > MAX_TYPED_TEXT_LENGTH or any(ord(char) < 32 for char in text):
            raise ValueError("Text is not allowed")
        return HandoffAction(kind="type", text=text)
    raise ValueError("Unknown action")


def _handoff_html() -> str:
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>BackendScout verification</title><style>
body{margin:0;background:#151515;color:#fff;font:16px system-ui,sans-serif}header{padding:10px 12px;background:#222}
#frame{display:block;width:100%;height:auto;touch-action:manipulation;background:#fff}.controls{display:flex;gap:8px;flex-wrap:wrap;padding:10px}
button,input{min-height:42px;font:inherit}button{padding:8px 12px}input{flex:1;min-width:180px;padding:0 8px}
</style></head><body><header>Human verification only. This link expires when the browser handoff ends.</header>
<img id="frame" alt="Current application browser"><div class="controls">
<button data-scroll="-650">Scroll up</button><button data-scroll="650">Scroll down</button>
<button data-key="Tab">Tab</button><button data-key="Space">Space</button><button data-key="Enter">Enter</button>
<input id="text" placeholder="Type into focused control"><button id="type">Type</button></div><script>
const base=location.pathname;const frame=document.querySelector('#frame');
async function send(body){await fetch(base+'action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});refresh();}
function refresh(){frame.src=base+'frame.png?t='+Date.now()}setInterval(refresh,800);refresh();
frame.addEventListener('click',event=>{const rect=frame.getBoundingClientRect();send({kind:'click',x:(event.clientX-rect.left)*frame.naturalWidth/rect.width,y:(event.clientY-rect.top)*frame.naturalHeight/rect.height})});
document.querySelectorAll('[data-scroll]').forEach(button=>button.onclick=()=>send({kind:'scroll',delta_y:Number(button.dataset.scroll)}));
document.querySelectorAll('[data-key]').forEach(button=>button.onclick=()=>send({kind:'key',key:button.dataset.key}));
document.querySelector('#type').onclick=()=>{const input=document.querySelector('#text');if(input.value){send({kind:'type',text:input.value});input.value=''}};
</script></body></html>"""
