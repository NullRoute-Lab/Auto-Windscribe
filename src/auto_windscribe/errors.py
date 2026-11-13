"""Safe diagnostics contain codes and curated text, never raw CLI output."""


class AppError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SafetyError(AppError):
    pass


class ParseError(AppError):
    def __init__(self, kind: str) -> None:
        super().__init__("malformed_output", f"Unrecognized {kind} output; operation refused.")
