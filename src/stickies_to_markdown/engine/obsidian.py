"""
Obsidian-specific extras the writer maintains for an output whose flavor
includes "obsidian":

- the CSS snippet that styles mirrored notes (tint by color, banner, hidden
  Properties block), installed into <vault>/.obsidian/snippets/ and enabled
  in appearance.json; the vault is found by walking up from the output
  folder to the nearest .obsidian directory.

Both are idempotent and marker-checked: a snippet file we did not write is
never touched, and appearance.json is edited atomically and only when the
snippet is not yet enabled - re-checked on every export, because Obsidian
rewrites appearance.json from memory and can drop an entry it never saw.
Obsidian applies an enabled snippet on its next reload (Settings >
Appearance > CSS snippets > the reload button, or restart).
"""

import os
import json
import tempfile

SNIPPET_NAME = "stickies-mirror"
SNIPPET_MARKER = "/* stickies-to-markdown snippet - managed; edits are overwritten */"

CSS_SNIPPET = SNIPPET_MARKER + r"""
/*
 * Installed by Stickies-to-Markdown for outputs with the "obsidian" flavor,
 * which writes `cssclasses: [stickies-mirror, sticky-<color>]` into every
 * mirrored note (and `stickies-deleted` on orphans under the "mark" policy).
 * No plugin involved: cssclasses is a core Obsidian property. To change the
 * look, copy this file under another name and disable this one - this file
 * is rewritten whenever the tool's version of it changes.
 */

/* ---- hide the Properties block on mirrored notes only ---------------- */
.stickies-mirror .metadata-container { display: none; }

/* ---- banner: this note is a mirror ---------------------------------- */
.stickies-mirror .markdown-preview-view::before,
.stickies-mirror .cm-editor .cm-scroller::before {
    content: "Mirrored from Stickies - edit the sticky, not this file";
    display: block;
    font-size: 0.8em;
    opacity: 0.6;
    padding: 0.2em 0.6em;
    margin-bottom: 0.8em;
    border-left: 3px solid var(--text-muted);
}
.stickies-deleted .markdown-preview-view::before,
.stickies-deleted .cm-editor .cm-scroller::before {
    content: "Deleted in Stickies - this copy is an orphan";
    border-left-color: var(--text-error);
}

/* ---- color tint: the sticky's real color, faintly ------------------ */
.sticky-yellow .markdown-preview-view, .sticky-yellow .cm-editor { background-color: rgba(254, 244, 156, 0.18); }
.sticky-blue   .markdown-preview-view, .sticky-blue   .cm-editor { background-color: rgba(173, 244, 255, 0.18); }
.sticky-green  .markdown-preview-view, .sticky-green  .cm-editor { background-color: rgba(178, 255, 161, 0.18); }
.sticky-pink   .markdown-preview-view, .sticky-pink   .cm-editor { background-color: rgba(255, 199, 199, 0.18); }
.sticky-purple .markdown-preview-view, .sticky-purple .cm-editor { background-color: rgba(182, 202, 255, 0.18); }
.sticky-gray   .markdown-preview-view, .sticky-gray   .cm-editor { background-color: rgba(238, 238, 238, 0.25); }
.stickies-deleted .markdown-preview-view, .stickies-deleted .cm-editor { filter: grayscale(1); opacity: 0.75; }
"""


def find_vault_root(start, max_depth=8):
    """Nearest ancestor of `start` (inclusive) containing .obsidian, or None."""
    path = os.path.abspath(os.path.expanduser(start))
    for _ in range(max_depth):
        if os.path.isdir(os.path.join(path, ".obsidian")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return None


def _atomic_write(path, text):
    fd, temp = tempfile.mkstemp(prefix=".s2m.", dir=os.path.dirname(path))
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temp, path)


def install_snippet(vault_root, dry_run=False):
    """
    Ensure the snippet is present and enabled. Returns a list of actions
    taken ("wrote snippet", "enabled snippet") - empty when already current.
    Refuses (returns ["foreign snippet left alone"]) if a file of that name
    exists without our marker.
    """
    actions = []
    snippets_dir = os.path.join(vault_root, ".obsidian", "snippets")
    css_path = os.path.join(snippets_dir, f"{SNIPPET_NAME}.css")

    existing = None
    if os.path.isfile(css_path):
        with open(css_path, "r", encoding="utf-8", errors="replace") as handle:
            existing = handle.read()
        if not existing.startswith(SNIPPET_MARKER):
            return ["foreign snippet left alone"]
    if existing != CSS_SNIPPET:
        if not dry_run:
            os.makedirs(snippets_dir, exist_ok=True)
            _atomic_write(css_path, CSS_SNIPPET)
        actions.append("wrote snippet")

    appearance_path = os.path.join(vault_root, ".obsidian", "appearance.json")
    appearance = {}
    if os.path.isfile(appearance_path):
        try:
            with open(appearance_path, "r", encoding="utf-8") as handle:
                appearance = json.load(handle)
        except (OSError, ValueError):
            return actions + ["appearance.json unreadable; enable the snippet by hand"]
    enabled = appearance.get("enabledCssSnippets") or []
    if SNIPPET_NAME not in enabled:
        if not dry_run:
            appearance["enabledCssSnippets"] = list(enabled) + [SNIPPET_NAME]
            _atomic_write(appearance_path, json.dumps(appearance, indent=2) + "\n")
        actions.append("enabled snippet")
    return actions


# End of file #
