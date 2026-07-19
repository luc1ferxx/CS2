from __future__ import annotations

import logging


SENSITIVE_CALLBACK_PATHS = (
    "/auth/steam/callback",
    "/auth/oidc/callback",
)
REDACTED_QUERY = "?[query-redacted]"


class AuthCallbackAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        full_path = args[2]
        if not isinstance(full_path, str):
            return True
        for callback_path in SENSITIVE_CALLBACK_PATHS:
            if full_path.startswith(f"{callback_path}?"):
                redacted_args = list(args)
                redacted_args[2] = f"{callback_path}{REDACTED_QUERY}"
                record.args = tuple(redacted_args)
                break
        return True


def install_auth_callback_access_log_redaction() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, AuthCallbackAccessLogFilter) for item in access_logger.filters):
        return
    access_logger.addFilter(AuthCallbackAccessLogFilter())
