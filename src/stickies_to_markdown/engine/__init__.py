"""
Engine: everything that reads Stickies and writes Markdown. No UI imports,
no stdout/stderr - enforced by tests/test_isolation.py. `Foundation`
(PyObjC, non-GUI) is permitted inside convert.py only, under a darwin
guard; AppKit and rumps are forbidden everywhere in the engine.
"""

from stickies_to_markdown.engine.config import Config, ConfigError
from stickies_to_markdown.engine.events import Event, Status, EventQueue
from stickies_to_markdown.engine.lock import MonitorLock

__all__ = ["Config", "ConfigError", "Event", "Status", "EventQueue", "MonitorLock"]

# End of file #
