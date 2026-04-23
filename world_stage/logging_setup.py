import logging
import os
import sys

SYSTEMD_PRIORITIES = {
    logging.DEBUG: 7,
    logging.INFO: 6,
    logging.WARNING: 4,
    logging.ERROR: 3,
    logging.CRITICAL: 2,
}


def _under_systemd() -> bool:
    return "JOURNAL_STREAM" in os.environ


class SystemdFormatter(logging.Formatter):
    """Prepends <N> priority prefixes that journald understands."""
    def format(self, record):
        priority = SYSTEMD_PRIORITIES.get(record.levelno, 6)
        return f"<{priority}>{super().format(record)}"


def _make_formatter():
    if _under_systemd():
        return SystemdFormatter("%(name)s: %(message)s")
    return logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def configure_logging(level=None):
    if level is None:
        level = os.environ.get("WORLDSTAGE_LOG_LEVEL", "INFO").upper()
    if isinstance(level, str):
        level = getattr(logging, level, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_make_formatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)