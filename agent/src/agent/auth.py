class TokenHolder:
    """Mutable holder for the agent session token.

    The control channel receives the token in the ``auth_ok`` frame, but the
    result HTTP server has already started by then. They share this holder so
    the server can read the current token per request instead of capturing a
    value at construction time.
    """

    def __init__(self, value: str = "") -> None:
        self.value = value
