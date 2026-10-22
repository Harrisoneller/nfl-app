"""Structured JSON logging using structlog.

Every log line is one JSON object: easy to grep, easy to ship to a log aggregator
later. Request IDs / user IDs can be bound via `structlog.contextvars`.

HTTP client libraries (httpx at INFO, httpcore when enabled) log full request
URLs. Query values for apiKey / key / token parameters are redacted on the way
into the logging system so a key never lands in a log line.
"""
from __future__ import annotations

import logging
import re
import sys

import structlog

from .config import get_settings

# Query parameter names whose values are credentials. Matched case-insensitively
# immediately after '?' or '&' so ordinary words that merely contain "key" are left
# alone. Covers apiKey, api_key, api-key, a bare key, and token parameters.
_SENSITIVE_QUERY_RE = re.compile(
    r"(?i)"
    r"([?&]"
    r"(?:api[_-]?key|key|access[_-]?token|refresh[_-]?token|id[_-]?token|token)"
    r"=)"
    r"([^&#\s\"']*)"
)

_FILTER = None
_FACTORY_INSTALLED = False


def redact_secrets(text: str) -> str:
    """Replace sensitive query-parameter values with ``REDACTED``."""
    return _SENSITIVE_QUERY_RE.sub(r"\1REDACTED", text)


def redact_log_record(record: logging.LogRecord) -> None:
    """Redact secrets in a log record.

    Interpolate first. httpx passes an ``httpx.URL`` as a ``%s`` argument, so the
    key only appears after formatting; clearing ``args`` then keeps a handler from
    inserting the original URL again.
    """
    try:
        rendered = record.getMessage()
    except (TypeError, ValueError):
        if isinstance(record.msg, str):
            record.msg = redact_secrets(record.msg)
        return
    redacted = redact_secrets(rendered)
    if redacted != rendered:
        record.msg = redacted
        record.args = ()


class SecretQueryFilter(logging.Filter):
    """Drop apiKey/key/token query values from any log record that reaches it."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_log_record(record)
        return True


def install_http_secret_redaction() -> None:
    """Install URL redaction for every logger, including httpx and httpcore.

    Logger filters do not run for records that propagate up from a child logger,
    and a handler filter is skipped by any other handler (tests, Sentry). The
    log-record factory runs for every record at creation time, which is what
    actually covers httpx, ``httpcore.*``, and anything else that logs a URL.
    """
    global _FACTORY_INSTALLED, _FILTER
    if _FILTER is None:
        _FILTER = SecretQueryFilter()

    if not _FACTORY_INSTALLED:
        previous = logging.getLogRecordFactory()

        def _redacting_factory(*args, **kwargs):
            record = previous(*args, **kwargs)
            redact_log_record(record)
            return record

        logging.setLogRecordFactory(_redacting_factory)
        _FACTORY_INSTALLED = True

    targets: list[logging.Logger] = [
        logging.getLogger(),
        logging.getLogger("httpx"),
        logging.getLogger("httpcore"),
    ]
    for name, logger in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(logger, logging.Logger) and name.startswith(("httpx.", "httpcore.")):
            targets.append(logger)
    for logger in targets:
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
    for handler in logging.getLogger().handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)


def configure_logging() -> None:
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )
    install_http_secret_redaction()

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if settings.app_env == "development":
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


install_http_secret_redaction()
