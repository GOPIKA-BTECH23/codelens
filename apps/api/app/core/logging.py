"""Structured JSON logging. Every line carries the request ID of the request that caused it."""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime

# Set by the request-ID middleware. Contextvars follow the request through awaits, so any
# log line written while handling it picks up the ID without passing it around.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
# Same idea for the worker (M4): set while a job runs, so every line it writes, including
# those from services it calls, carries the job ID and (for review jobs) the run ID.
job_id_var: ContextVar[str | None] = ContextVar("job_id", default=None)
run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)

# Attributes every LogRecord has. Anything else was passed via `extra=` and is emitted as a field.
_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render each record as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        for field, var in (("job_id", job_id_var), ("run_id", run_id_var)):
            context_value = var.get()
            if context_value:
                payload[field] = context_value
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Send all logs, including uvicorn's, through the JSON formatter on stdout."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # Uvicorn installs its own handlers; hand its loggers to the root handler instead.
    for name in ("uvicorn", "uvicorn.error"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True
    # We write our own access line in the middleware (with request ID and duration).
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    # httpx logs every outbound URL at INFO. We never put secrets in URLs, but a log line
    # per GitHub call adds nothing and one careless query parameter would leak a credential.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
