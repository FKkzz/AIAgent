from __future__ import annotations


class QuickReadError(Exception):
    """A safe-to-display application error with a stable machine code."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class PdfError(QuickReadError):
    pass

class ModelResponseError(QuickReadError):
    pass


class AuthenticationError(QuickReadError):
    pass


class ProxyError(QuickReadError):
    pass


class QuotaError(QuickReadError):
    pass
