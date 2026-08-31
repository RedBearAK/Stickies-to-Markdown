"""
File logging for the engine.

There is deliberately no console handler here, at any log level. The file is
the record; front ends decide what, if anything, reaches a terminal. (The
old DEBUG-mode console handler is what used to scroll the menu away.)
"""

import logging

from logging.handlers import RotatingFileHandler


LOGGER_NAME = 'StickiesToMarkdown'

_FORMATTER = logging.Formatter(
    '%(asctime)s | %(levelname)-8s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)


def get_logger():
    return logging.getLogger(LOGGER_NAME)


# The engine must never write to a terminal, even before setup_logging()
# runs: without a handler, Python's lastResort handler prints warnings to
# stderr. A NullHandler at import time closes that hole.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())
logging.getLogger(LOGGER_NAME).propagate = False


def setup_logging(config):
    """
    (Re)configure the engine logger from config. Idempotent: existing
    handlers are removed so repeated engine starts don't stack handlers.
    """
    logger = get_logger()
    level_name = str(config.get("log_level", "INFO")).upper()
    logger.setLevel(getattr(logging, level_name, logging.INFO))
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    log_file = config.get("log_file")
    max_bytes = int(config.get("log_max_size", 10)) * 1024 * 1024
    backup_count = int(config.get("log_backup_count", 5))

    file_handler = RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding='utf-8')
    file_handler.setFormatter(_FORMATTER)
    logger.addHandler(file_handler)
    return logger


def log_level_is_debug(config):
    return str(config.get("log_level", "INFO")).upper() == "DEBUG"


# End of file #
