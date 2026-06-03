from pathlib import Path


class TokenHolder:
    """Mutable holder for the agent session token.

    The control channel receives the token in the ``auth_ok`` frame, but the
    result HTTP server has already started by then. They share this holder so
    the server can read the current token per request instead of capturing a
    value at construction time.
    """

    def __init__(self, value: str = "") -> None:
        self.value = value


def load_session_token(path: Path) -> str:
    """Read a previously persisted session token, or "" if none exists.

    The token is written on the first successful handshake and reused on every
    reconnect so the agent survives restarts without re-consuming its (single-use)
    bootstrap token. A missing or unreadable file is treated as "no token yet".
    """
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def save_session_token(path: Path, token: str) -> None:
    """Persist the session token with owner-only permissions (0600)."""
    if not token:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token)
    try:
        path.chmod(0o600)
    except OSError:
        # Best-effort hardening; some filesystems (e.g. mounted volumes) reject chmod.
        pass
