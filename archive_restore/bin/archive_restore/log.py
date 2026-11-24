"""KV logger to $SPLUNK_HOME/var/log/splunk/archive_restore.log (pattern from org_frozen_app).

The default var/log/splunk monitor indexes it into index=_internal; no inputs.conf needed.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import socket

LOG_NAME = "archive_restore.log"


def log_path() -> str:
    splunk_home = os.environ.get("SPLUNK_HOME", "/opt/splunk")
    return os.path.join(splunk_home, "var", "log", "splunk", LOG_NAME)


def setup_logger(component: str) -> logging.Logger:
    """Return a logger that appends KV lines to archive_restore.log."""
    logger = logging.getLogger("archive_restore.%s" % component)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            log_path(), maxBytes=25 * 1024 * 1024, backupCount=5
        )
        fmt = logging.Formatter(
            "%(asctime)s level=%(levelname)s component=" + component + " host=%(host)s %(message)s"
        )
        fmt.default_msec_format = "%s.%03d"
        handler.setFormatter(fmt)
        host = socket.gethostname()

        class _Host(logging.Filter):
            def filter(self, record: logging.LogRecord) -> bool:
                record.host = host
                return True

        handler.addFilter(_Host())
        logger.addHandler(handler)
    return logger


def kv_fields(**fields: object) -> str:
    """Format kwargs as key=value pairs; quote values containing space or '='.

    Double quotes and newlines are replaced, so a value can never close its quotes and
    forge extra fields in index=_internal (Splunk's KV extraction ignores backslashes).
    """
    parts = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value).replace("\n", " ").replace("\r", " ").replace('"', "'")
        if any(c in text for c in (" ", "=")):
            text = '"%s"' % text
        parts.append("%s=%s" % (key, text))
    return " ".join(parts)
