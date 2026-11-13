"""Safe diagnostics contain codes and curated text, never raw CLI output."""


class AppError(Exception):
    def __init__(self, code: str, message: str, timings: dict[str, float] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.timings = timings or {}


class SafetyError(AppError):
    pass


class ParseError(AppError):
    def __init__(self, kind: str) -> None:
        super().__init__("malformed_output", f"Unrecognized {kind} output; operation refused.")
