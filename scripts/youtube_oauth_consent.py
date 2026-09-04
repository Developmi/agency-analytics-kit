#!/usr/bin/env python3
"""One-time OAuth consent flow for the YouTube connector (desktop app).

Runs the installed-app (loopback) flow on a localhost port, exchanges the
authorization code for a refresh token, and stores it in the project .env
under YOUTUBE_OAUTH_REFRESH_TOKEN (generic key, no client literal).

Usage:
    .venv/bin/python scripts/youtube_oauth_consent.py
    .venv/bin/python scripts/youtube_oauth_consent.py --env-suffix _ACME

After authorizing in the browser, the refresh token is written to .env
automatically and the script exits.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

CLIENT_ID_ENV = "YOUTUBE_OAUTH_CLIENT_ID"
CLIENT_SECRET_ENV = "YOUTUBE_OAUTH_CLIENT_SECRET"
REFRESH_TOKEN_ENV = "YOUTUBE_OAUTH_REFRESH_TOKEN"


def _env_key(base: str, suffix: str) -> str:
    """Compose an env key: base key, optionally suffixed for per-client use."""
    return f"{base}_{suffix}" if suffix else base


def _load_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _save_env_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    out: list[str] = []
    replaced = False
    for line in lines:
        if line.startswith(f"{key}="):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


class _RedirectHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        params = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "error" in params:
            self.server.auth_error = params["error"][0]  # type: ignore[attr-defined]
            self.send_response(400)
            self.end_headers()
            self.wfile.write(b"Authorization failed.")
            return
        self.server.auth_code = params.get("code", [None])[0]  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h3>OK, ya podes cerrar esta pestana.</h3>")

    def log_message(self, *args) -> None:  # noqa: N802
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time YouTube OAuth consent flow.")
    parser.add_argument(
        "--port", type=int, default=0, help="Loopback port to listen on (default: random)."
    )
    parser.add_argument("--no-browser", action="store_true", help="Do not auto-open the browser.")
    parser.add_argument(
        "--env-suffix",
        default="",
        help=(
            "Optional client suffix for the YOUTUBE_OAUTH_* env keys, e.g. "
            "--env-suffix _ACME reads/writes YOUTUBE_OAUTH_CLIENT_ID_ACME. "
            "Default: the generic YOUTUBE_OAUTH_* keys (no client literal)."
        ),
    )
    args = parser.parse_args()

    env_suffix = args.env_suffix.strip()
    client_id_env = _env_key(CLIENT_ID_ENV, env_suffix)
    client_secret_env = _env_key(CLIENT_SECRET_ENV, env_suffix)
    refresh_token_env = _env_key(REFRESH_TOKEN_ENV, env_suffix)

    repo_root = Path(__file__).resolve().parents[1]
    env_path = repo_root / ".env"
    env = _load_env(env_path)

    client_id = env.get(client_id_env)
    client_secret = env.get(client_secret_env)
    if not client_id or not client_secret:
        print(f"Missing {client_id_env} / {client_secret_env} in .env", file=sys.stderr)
        return 1

    server = HTTPServer(("localhost", args.port), _RedirectHandler)
    port = server.server_address[1]
    redirect_uri = f"http://localhost:{port}/"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"

    print("=" * 72)
    print("Open this URL in the browser and authorize with the channel-owner account:")
    print()
    print(auth_url)
    print()
    print("Waiting for authorization... the refresh token is saved to .env automatically.")
    print("=" * 72)
    if not args.no_browser:
        webbrowser.open(auth_url)

    for _ in range(20):
        server.handle_request()
        if getattr(server, "auth_code", None) or getattr(server, "auth_error", None):
            break

    code = getattr(server, "auth_code", None)
    error = getattr(server, "auth_error", None)
    server.server_close()
    if error:
        print(f"Authorization failed: {error}", file=sys.stderr)
        return 1
    if not code:
        print("No authorization code received.", file=sys.stderr)
        return 1

    body = urllib.parse.urlencode(
        {
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode()
    req = urllib.request.Request(TOKEN_URL, data=body)
    with urllib.request.urlopen(req) as resp:
        token_data = json.loads(resp.read())

    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        print(
            "No refresh_token in the token response. Check the consent screen is "
            "published to Production (Testing-mode tokens expire in 7 days), or "
            "revoke app access and retry.",
            file=sys.stderr,
        )
        return 1

    _save_env_value(env_path, refresh_token_env, refresh_token)
    print(f"Refresh token saved to {env_path} as {refresh_token_env}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
