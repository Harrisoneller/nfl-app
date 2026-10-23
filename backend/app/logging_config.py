"""Structured JSON logging using structlog.

Every log line is one JSON object: easy to grep, easy to ship to a log aggregator
later. Request IDs / user IDs can be bound via `structlog.contextvars`.

HTTP client libraries (httpx at INFO, httpcore when enabled) log full request
URLs. Credentials are redacted in three places so a key cannot bypass the filter:

* a log-record factory plus a stdlib formatter, including traceback text
* a structlog processor that runs after exception formatting
* a Sentry ``before_send`` hook, attached even if the SDK was already initialized
"""
from __future__ import annotations

import logging
import re
import sys
import traceback
from collections.abc import Mapping
from typing import Any

import structlog

from .config import get_settings

# Credential parameter names. ``x-api-key`` is listed before ``api[_-]?key`` so the
# longer header name wins. Matched case-insensitively.
_SECRET_NAME = (
    r"x-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|id[_-]?token|token|key"
)

# ?apiKey=value / &token=value. The prefix keeps ordinary words that contain "key".
_QUERY_RE = re.compile(
    rf"(?i)([?&](?:{_SECRET_NAME})=)([^&#\s\"']*)"
)
# apiKey%3Dvalue, including a fully percent-encoded URL (%3F / %26 prefixes).
_PCT_RE = re.compile(
    rf"(?i)((?:%3[fF]|%26|[?&]|^)(?:{_SECRET_NAME})%3[dD])([^%&#\s\"']*)"
)
# {"apiKey": "value"} and {'apiKey': 'value'}.
_JSON_RE = re.compile(
    rf"""(?ix)((?<![A-Za-z0-9_])['\"](?:{_SECRET_NAME})['\"]\s*:\s*['\"])([^'\"]*)"""
)
# Header / bare ``name=value`` form. Stop at ``&`` so a query string's later
# parameters (``regions=us``) survive after the secret value is replaced.
_HEADER_RE = re.compile(rf"(?i)((?:{_SECRET_NAME})\s*[:=]\s*)([^&\s,;\"']*)")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)([^\s'\"]+)")

_FILTER: logging.Filter | None = None
_FACTORY_INSTALLED = False
_REDACTING = False


def redact_secrets(text: str) -> str:
    """Replace credential values with ``REDACTED``.

    Covers query strings, percent-encoded ``key%3D`` forms, JSON/dict literals,
    ``x-api-key`` headers, and ``Bearer`` tokens. Idempotent.
    """
    text = _QUERY_RE.sub(r"\1REDACTED", text)
    text = _PCT_RE.sub(r"\1REDACTED", text)
    text = _JSON_RE.sub(r"\1REDACTED", text)
    text = _HEADER_RE.sub(r"\1REDACTED", text)
    return _BEARER_RE.sub(r"\1REDACTED", text)


def _redact_arg(arg: object) -> object:
    """Redact one logging interpolation argument without dropping it.

    uvicorn's access formatter unpacks ``record.args`` by position, so the tuple
    length and non-string arguments (the status code) have to stay intact.
    Objects such as ``httpx.URL`` and ``HTTPStatusError`` only reveal the key
    once formatted, so those are replaced with redacted text.
    """
    if isinstance(arg, str):
        return redact_secrets(arg)
    if isinstance(arg, bytes):
        text = arg.decode("utf-8", "replace")
        redacted = redact_secrets(text)
        return arg if redacted == text else redacted.encode()
    if arg is None or isinstance(arg, (int, float, bool)):
        return arg
    try:
        as_repr = repr(arg)
    except Exception:  # noqa: BLE001 — repr() can raise anything; keep the record
        as_repr = ""
    try:
        as_text = str(arg)
    except Exception:  # noqa: BLE001 — str() can raise anything; keep the record
        as_text = ""
    red_repr = redact_secrets(as_repr)
    if red_repr != as_repr:
        return red_repr
    red_text = redact_secrets(as_text)
    if red_text != as_text:
        return red_text
    return arg


def redact_log_record(record: logging.LogRecord) -> None:
    """Redact secrets in the message, arguments, and traceback text."""
    global _REDACTING
    if _REDACTING:
        return
    _REDACTING = True
    try:
        args = record.args
        if isinstance(args, dict):
            record.args = {key: _redact_arg(value) for key, value in args.items()}
        elif isinstance(args, tuple):
            record.args = tuple(_redact_arg(value) for value in args)
        # Redact the formatted line, not the template. A template like
        # ``api_key=%s`` must keep its placeholder until the argument is merged,
        # or the formatter raises "not all arguments converted".
        try:
            rendered = record.getMessage()
        except (TypeError, ValueError):
            if isinstance(record.msg, str):
                record.msg = redact_secrets(record.msg)
        else:
            redacted = redact_secrets(rendered)
            if redacted != rendered:
                record.msg = redacted
                record.args = ()
        if isinstance(record.stack_info, str):
            record.stack_info = redact_secrets(record.stack_info)
        exc_info = record.exc_info
        if isinstance(exc_info, tuple) and exc_info[0] is not None:
            # Pre-set exc_text so stock formatters append the scrubbed traceback
            # instead of rendering exc_info again. Sentry still reads exc_info;
            # before_send covers that path.
            rendered = record.exc_text or "".join(traceback.format_exception(*exc_info))
            record.exc_text = redact_secrets(rendered)
        elif isinstance(record.exc_text, str):
            record.exc_text = redact_secrets(record.exc_text)
    finally:
        _REDACTING = False


class SecretQueryFilter(logging.Filter):
    """Drop credential values from any log record that reaches this filter."""

    def filter(self, record: logging.LogRecord) -> bool:
        redact_log_record(record)
        return True


class SecretRedactingFormatter(logging.Formatter):
    """Stdlib formatter that scrubs the rendered line and any traceback."""

    def format(self, record: logging.LogRecord) -> str:
        redact_log_record(record)
        return redact_secrets(super().format(record))

    def formatException(self, ei: Any) -> str:
        return redact_secrets(super().formatException(ei))


def _scrub(value: object, seen: set[int] | None = None) -> object:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, bytes):
        text = value.decode("utf-8", "replace")
        redacted = redact_secrets(text)
        return value if redacted == text else redacted.encode()
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if seen is None:
        seen = set()
    marker = id(value)
    if marker in seen:
        return value
    if isinstance(value, Mapping):
        seen.add(marker)
        return {_scrub(key, seen): _scrub(item, seen) for key, item in value.items()}
    if isinstance(value, list):
        seen.add(marker)
        return [_scrub(item, seen) for item in value]
    if isinstance(value, tuple):
        seen.add(marker)
        return tuple(_scrub(item, seen) for item in value)
    return value


def scrub_sentry_event(event: dict[str, Any] | None, hint: dict[str, Any] | None) -> Any:
    """Sentry ``before_send`` hook. ``hint`` is not serialized into the envelope."""
    del hint
    if not isinstance(event, dict):
        return event
    return _scrub(event)


scrub_sentry_event._nfl_secret_scrub = True  # type: ignore[attr-defined]


def _install_sentry_before_send() -> None:
    """Attach the scrubber to an already-initialized Sentry client, if any.

    ``sentry_sdk.init`` replaces the client, so callers that initialize first
    (and ``init_sentry``) must both end up with this hook. A previous
    ``before_send`` is chained, not replaced.
    """
    try:
        import sentry_sdk
    except ImportError:
        return
    client = sentry_sdk.get_client()
    options = getattr(client, "options", None)
    if not isinstance(options, dict):
        return
    current = options.get("before_send")
    if getattr(current, "_nfl_secret_scrub", False):
        return
    if current is None:
        options["before_send"] = scrub_sentry_event
        return

    def _chained(event: dict[str, Any] | None, hint: dict[str, Any] | None) -> Any:
        event = scrub_sentry_event(event, hint)
        if event is None:
            return None
        return current(event, hint)

    _chained._nfl_secret_scrub = True  # type: ignore[attr-defined]
    options["before_send"] = _chained


def redact_event_dict(
    logger: Any, method_name: str, event_dict: dict[str, Any],
) -> dict[str, Any]:
    """structlog processor. Must run after ``format_exc_info``."""
    del logger, method_name
    return _scrub(event_dict)  # type: ignore[return-value]


def install_http_secret_redaction() -> None:
    """Install redaction for every logger, including httpx and httpcore.

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

        def _redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
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
    for name, found in list(logging.Logger.manager.loggerDict.items()):
        if isinstance(found, logging.Logger) and name.startswith(("httpx.", "httpcore.")):
            targets.append(found)
    for found in targets:
        if _FILTER not in found.filters:
            found.addFilter(_FILTER)
    root = logging.getLogger()
    for handler in root.handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
        formatter = handler.formatter
        if formatter is None or type(formatter) is logging.Formatter:
            style = formatter._fmt if isinstance(formatter, logging.Formatter) else "%(message)s"
            handler.setFormatter(SecretRedactingFormatter(style))
    _install_sentry_before_send()


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

    processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        redact_event_dict,
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
