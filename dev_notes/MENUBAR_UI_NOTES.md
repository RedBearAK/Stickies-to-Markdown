# Menu bar UI notes (Phase 2)

Lessons from the DFP menu bar work that apply directly when `menubar.py`
comes over.

## The rumps About/Help alert is NARROW

`rumps.alert` wraps NSAlert, and NSAlert's informative-text column is
narrow (roughly 220–260 pt of usable width, system-dependent). Long lines
wrap at awkward points and there is no way to widen it from rumps.

Consequence: the terminal habit of annotating a command with an inline
comment does **not** survive the dialog. This wraps badly:

    stickies2md            # settings menu
    stickies2md --follow-log

The `# comment` ends up orphaned on its own wrapped line, detached from
the command it annotates. Put the label on its own line ABOVE the command
instead, and keep every line short:

    Settings menu:
    stickies2md

    Live log:
    stickies2md --follow-log

Rules of thumb for alert text in this app:
- One idea per line; label above, command below, blank line between pairs.
- No inline comments, no column alignment (the font is proportional
  anyway - leading spaces for "indentation" are fine, alignment is not).
- Keep commands short enough to survive ~35 characters per line unwrapped.
- The alert is a signpost, not documentation: point at the terminal and
  stop.

# End of file #
