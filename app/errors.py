"""Shared error type carrying stable, machine-readable verdict codes."""


class DecodeError(ValueError):
    """Per-pair decode failure.

    The ``code`` is part of the API contract and must remain stable; the
    human-readable ``message`` may change between releases.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
