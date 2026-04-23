import logging
import os
import sys

from world_stage.logging_setup import SystemdFormatter, _make_formatter, _under_systemd

# Configure the root logger so app code inherits the right handler
_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_make_formatter())
logging.getLogger().handlers = [_handler]
logging.getLogger().setLevel(os.environ.get("WORLDSTAGE_LOG_LEVEL", "INFO").upper())

# Tell gunicorn's own loggers to use the same formatter
if _under_systemd():
    _formatter_cls = "world_stage.logging_setup.SystemdFormatter"
    _formatter_fmt = "%(name)s: %(message)s"
else:
    _formatter_cls = "logging.Formatter"
    _formatter_fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": _formatter_cls,
            "format": _formatter_fmt,
        },
    },
    "handlers": {
        "stderr": {
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
            "formatter": "default",
        },
    },
    "loggers": {
        "gunicorn.error":  {"level": "INFO", "handlers": ["stderr"], "propagate": False},
        "gunicorn.access": {"level": "INFO", "handlers": ["stderr"], "propagate": False},
    },
}