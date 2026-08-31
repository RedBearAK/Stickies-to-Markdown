"""
macOS menu bar front end (rumps).

The threading contract is the part that must hold: rumps is touched only
from timer/menu callbacks (main thread); the engine's observer and worker
threads only ever touch the queue and counters.

Deliberately minimal: status line, Start/Stop, Export now, About/Help,
Quit. Settings and logs live in the terminal (`stickies2md`,
`stickies2md --follow-log`). The About text is written for NSAlert's
narrow column: label above, command below, no inline comments
(dev_notes/MENUBAR_UI_NOTES.md).
"""

import os
import signal

import rumps    # ImportError here is caught by __main__ with a helpful message

from rumps.rumps import NSApplication    # rumps already imported AppKit; reuse it

from stickies_to_markdown.engine import Config, Engine, EngineError


# Full-colour icons rather than macOS "template" images, so the status
# colour survives: green sticky = watching, grey sticky = stopped, amber
# sticky with "!" = problem. This app's own artwork (icons/make_icons.py),
# distinct from DFP's dot/square/triangle. AppKit picks @2x on Retina.
ICON_DIR = os.path.join(os.path.dirname(__file__), "icons")
ICON_WATCHING = os.path.join(ICON_DIR, "watching.png")
ICON_STOPPED = os.path.join(ICON_DIR, "stopped.png")
ICON_PROBLEM = os.path.join(ICON_DIR, "problem.png")

TICK_SECONDS = 0.5

# NSApplicationActivationPolicyAccessory: no Dock tile, no main menu bar.
# The same thing LSUIElement=true does for a bundle, but set from code so
# it also applies when launched from Terminal.
ACTIVATION_POLICY_ACCESSORY = 1


def become_accessory_app():
    NSApplication.sharedApplication().setActivationPolicy_(ACTIVATION_POLICY_ACCESSORY)


def bring_to_front():
    """An NSAlert from a non-frontmost process opens behind everything and
    bounces the Dock icon; activating first puts it where the user is."""
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)


class StickiesApp(rumps.App):

    def __init__(self, engine):
        super().__init__("Stickies2md", icon=ICON_STOPPED, quit_button=None)
        self.engine = engine
        self.status_item = rumps.MenuItem("Status: stopped")
        self.status_item.set_callback(None)
        self.toggle_item = rumps.MenuItem("Start watching", callback=self.toggle)
        self.export_item = rumps.MenuItem("Export all notes now", callback=self.export_now)
        self.about_item = rumps.MenuItem("About / Help", callback=self.show_about)
        self.quit_item = rumps.MenuItem("Quit", callback=self.quit)
        self.menu = [self.status_item, None, self.toggle_item, self.export_item, None,
                     self.about_item, self.quit_item]
        self._tick_timer = rumps.Timer(self._tick, TICK_SECONDS)
        self._tick_timer.start()

    def show_about(self, _):
        bring_to_front()
        rumps.alert(
            title="Stickies to Markdown",
            message=("One-way mirror of Stickies into Markdown.\n"
                     "This menu: Start/Stop and Export.\n"
                     "Settings and logs live in the terminal.\n\n"
                     "Settings menu:\n"
                     "stickies2md\n\n"
                     "Live log:\n"
                     "stickies2md --follow-log\n\n"
                     "Yellow icon = a problem; the status\n"
                     "line and the log say which."))

    def toggle(self, _):
        if self.engine.status().monitoring:
            self.engine.stop()
        elif not self.engine.config.output_dir():
            bring_to_front()
            rumps.alert(title="No mirror folder yet",
                        message=("Set the folder in the terminal:\n\n"
                                 "stickies2md\n"
                                 "Settings > Mirror folder\n\n"
                                 "Then Start here."))
        else:
            try:
                self.engine.start()
            except EngineError as error:
                bring_to_front()
                rumps.alert(title="Cannot start", message=str(error))
        self._refresh()

    def export_now(self, _):
        if self.engine.status().monitoring:
            self.engine.request_full_export()
        else:
            try:
                self.engine.export_once()
            except Exception as error:      # noqa: BLE001 - report, never crash the app
                bring_to_front()
                rumps.alert(title="Export failed", message=str(error))
        self._refresh()

    def quit(self, _=None):
        self._tick_timer.stop()
        self.engine.stop()
        rumps.quit_application()

    def _tick(self, _timer):
        self.engine.events.drain()          # the log has the details
        self.engine.reload_config_if_changed()
        self._refresh()

    def _refresh(self):
        st = self.engine.status()
        if not st.healthy:
            self.icon = ICON_PROBLEM
            self.status_item.title = f"Problem: {st.last_error or 'see log'}"
        elif st.monitoring:
            self.icon = ICON_WATCHING
            self.status_item.title = (
                f"Watching {st.notes_known} notes - {st.converted_session} converted"
                + (" (dry run)" if st.dry_run else ""))
        elif not self.engine.config.output_dir():
            self.icon = ICON_STOPPED
            self.status_item.title = "No mirror folder - set it in the terminal"
        else:
            self.icon = ICON_STOPPED
            self.status_item.title = "Status: stopped"
        self.toggle_item.title = "Stop watching" if st.monitoring else "Start watching"


def run_menubar(config=None):
    engine = Engine(config or Config())
    become_accessory_app()
    app = StickiesApp(engine)

    def on_signal(_signum, _frame):
        app.quit()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    try:
        if engine.config.output_dir():
            try:
                engine.start()
            except EngineError:
                pass            # icon shows stopped; the menu explains on Start
        app.run()
    finally:
        engine.stop()
    return 0


# End of file #
