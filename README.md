# Stickies-to-Markdown

A one-way mirror of Apple Stickies into annotated Markdown files.

Stickies stays exactly what it is — instant, always-visible, zero-friction
capture. This tool watches its storage and maintains a folder of Markdown
files that anything can read: Obsidian, grep, scripts, sync clients, other
machines. **The mirror is read-only by design**: Stickies holds notes in
memory and writes them back on its own schedule, so anything edited in the
mirror would be silently overwritten. One writer, one direction.

```
Stickies (source of truth)  ──▶  Synced_from_Stickies/   (annotated .md)
```

## Status: Phase 2 — watcher, menu, menu bar app

```
stickies2md                   # interactive menu: settings, logs, install/maintain
stickies2md --start           # watch Stickies in the foreground (Ctrl-C stops)
stickies2md --once            # export every note once, then exit
stickies2md --once --dry-run  # show what would change, write nothing
stickies2md --menubar         # macOS menu bar app (pip install '.[menubar]')
```

Edits in Stickies reach the mirror about 15–20 seconds after you stop
typing (Stickies autosaves ~10 s after the last change; the watcher adds a
short debounce and settle). Colour, position and collapse changes rewrite
the note too and are absorbed as "unchanged" unless the colour changed.
Everything behavioural here was measured on a real Mac — see
`dev_notes/MAC_FINDINGS.md`, whose "Phase 2 watcher rules" section is what
the watcher implements.

## Setup

```
pip install '.[convert]'            # + '[menubar]' on macOS for the menu bar app
stickies2md                         # menu > Settings > Mirror folder, then Start
```

or without the menu:

```
stickies2md --set output_dir=~/Obsidian/Vault/Synced_from_Stickies
stickies2md --install-command       # puts `stickies2md` on PATH, recording this venv
stickies2md --start
```

### Terminal command and app bundle

`--install-command` writes a tiny stub to `~/.local/bin/stickies2md` that
records which interpreter (venv) to use; re-run it after rebuilding the
venv and it repairs the path. It never edits shell rc files and never
overwrites a file it did not write. `--uninstall-command` removes it. The
menu's "Install / maintain" screen does the same and shows whether the
stub points at the interpreter you are running.

`--install-app` (macOS) writes `~/Applications/Stickies to Markdown.app`:
a compiled launcher that starts `--menubar` from the recorded venv, ad-hoc
signed with a **stable identifier** so the "access data from other apps"
prompt appears in the app's own name and the grant follows the app. Add
it to System Settings › General › Login Items to start at login. Re-run
after a venv rebuild; `--uninstall-app` removes it. The identifier must
never change — TCC grants are keyed to it.

The menu bar app is deliberately small: a status icon (green watching,
yellow problem, red stopped), Start/Stop, Export now, About, Quit.
Settings and logs stay in the terminal and apply live.

On macOS the first read of the Stickies container triggers a
*"would like to access data from other apps"* prompt, attributed to the app
hosting your terminal (Terminal.app, VS Code...). Allow it. Don't Allow is
a silent, permanent deny (`tccutil reset` to get the prompt back); the tool
reports it as `permission denied` rather than failing quietly. Full Disk
Access is **not** required.

## The output format (the public interface)

Every mirror file carries YAML front matter. **These generic keys are the
contract** — scripts and future consumers may rely on them:

```yaml
---
synced-by: stickies-to-markdown      # ownership marker (see Safety)
stickies-uuid: 5A2B...-...           # identity, survives retitling
color: yellow                        # palette name classified from the RGB below
color-hex: "#fef49c"                 # the exact StickyColor from .SavedStickiesState
body-format: markdown                # or "code" when the body is a fenced block
created: 2026-08-30T09:12:03
modified: 2026-08-30T14:02:41
source: /Users/you/Library/.../5A2B....rtfd
content-hash: sha256:8820...         # of the Markdown body
synced-at: 2026-08-30T14:02:44
---
```

Consumer-specific keys are added only when a **flavor** is configured
(`--set flavor=obsidian` adds `cssclasses: [stickies-mirror, sticky-yellow]`
for CSS-snippet styling). The generic keys appear in every flavor; flavors
only ever add.

Files are named `<slug-of-first-line>--<uuid8>.md` (or `<uuid8>.md` with
`filename_style=uuid`). Attachments are copied to `attachments/<uuid8>/` and
linked from the body.

## Safety rules

- The Stickies container is **never written to**. Ever.
- Mirror files are written atomically and (by default) left `chmod 444`;
  updates go through same-directory rename, which needs directory
  permissions, not file permissions.
- A file **without the `synced-by` marker is never touched**, even when it
  occupies a filename the tool wants.
- A marked file whose body no longer matches its `content-hash` was edited
  externally: it is moved to `_conflicts/` (edit preserved), then rewritten
  from Stickies. Nothing is ever clobbered.
- A note deleted in Stickies is handled per `on_delete` (see below); the
  default archives it with a `deleted-from-stickies` timestamp so an orphan
  is never mistaken for a live note.
- Writes only happen when content actually changed, so sync clients and
  Obsidian stay quiet across idle re-runs.

## Configuration

`stickies2md --show-config` prints everything;
`stickies2md --set KEY=VALUE` persists a change. Highlights:

| key | default | meaning |
| --- | --- | --- |
| `output_dir` | *(unset)* | the mirror folder; must be set once |
| `stickies_dir` | the Stickies container | override for testing |
| `converter` | `auto` | `foundation` → `textutil` → `text` fallback chain |
| `flavor` | `generic` | `obsidian` adds `cssclasses` |
| `filename_style` | `slug-uuid` | or `uuid` |
| `on_delete` | `archive` | `mark` / `delete` / `keep` — see below |
| `deleted_dir` | `_deleted` | archive folder; relative to `output_dir` or absolute |
| `exclude_colors` | `[]` | colours to keep out of the mirror |
| `exclude_title_regex` | *(none)* | first-line pattern to keep out |
| `on_exclude` | `delete` | policy for a note that becomes excluded |
| `code_block_min_escapes` / `code_block_density` | `6` / `4.0` | when a note becomes a fenced code block (see below) |
| `read_only_output` | `true` | chmod 444 mirror files |
| `include_attachments` | `true` | copy package attachments |

### When a note is deleted in Stickies

| `on_delete` | what happens to the mirror file |
| --- | --- |
| `archive` (default) | annotated with `deleted-from-stickies: <time>`, then moved to `deleted_dir` (`--set deleted_dir=Deleted_Stickies` to rename it, or an absolute path to put it elsewhere) |
| `mark` | annotated in place and left where it is — an orphan you can still find, filter or style (`obsidian` flavor adds a `stickies-deleted` class) |
| `delete` | removed |
| `keep` | untouched — an unannotated orphan |

A *retitled* note is not a deletion: its old filename is removed and the
content continues under the new name, whatever `on_delete` says.
Attachments follow the file: archived alongside it, or removed with it.

### Keeping some notes out of the mirror

```
stickies2md --set exclude_colors=gray          # "gray means private"
stickies2md --set exclude_title_regex='^#private\b'
stickies2md --set on_exclude=delete            # archive | mark | delete | keep
```

Exclusion is **reactive**: a note that matches is treated as if it had
been deleted, per `on_exclude` (default `delete`). That matters for
timing. Stickies autosaves a new note about ten seconds after you stop
typing, so a marker typed *after* the content means the note is mirrored
briefly and then removed - and a sync client may have seen it. Colour is
the attribute that avoids this: set it on the empty note first, then
type. Excluded notes are never converted, so their text never leaves the
Stickies container.

### Note text is shown as written

Plain text in a note is escaped so a Markdown renderer displays it
literally: `*U*` stays asterisks rather than italics, `$A$5` stays a
cell reference rather than Obsidian math, a line of `-----` stays a line
rather than turning the line above into a heading, indented lines keep
their indent rather than becoming code blocks, and `#word` does not
become a vault tag. Only the converter's own output (bold/italic from
Stickies formatting, real lists, attachment links) is live Markdown.

A note that would need *heavy* escaping - formulas, passwords, shell
snippets - is not prose, and a body full of backslashes helps nobody.
Such a note is emitted verbatim inside a fenced code block instead
(monospaced, copy button in Obsidian) and its front matter says
`body-format: code`; everything else says `body-format: markdown`. The
trigger is `code_block_min_escapes` (default 6) *and*
`code_block_density` (default 4 per 100 non-space characters); set either
to `0` to always escape instead.

Config lives at `~/Library/Application Support/StickiesToMarkdown/` (macOS)
or `~/.config/stickies-to-markdown/` (Linux), JSON, hot-reload-friendly.

## Converter tiers

1. **textutil** — Apple's built-in RTF→HTML (Cocoa HTML Writer): bold,
   italic, real lists, attachments, indentation. macOS only. Verified
   against real Stickies output.
2. **pandoc** — `pandoc -f rtf -t html` through `pypandoc` (the
   `pypandoc-binary` wheel bundles the binary). Any OS; the formatted path
   on Linux and a fallback on macOS. Optional.
3. **text** — a small stdlib RTF text extractor: paragraphs, unicode
   (including emoji), attachments listed. Runs anywhere; the floor that
   can never produce nothing.

`auto` tries textutil, then pandoc, then text. Both HTML-producing tiers go
through one HTML→Markdown stage (markdownify when installed, a stdlib
walker otherwise) so escaping and line handling are identical. Pandoc's
RTF reader needed two fixes to be usable (blank lines, emoji) — see
`dev_notes/MAC_FINDINGS.md`. There is no PyObjC tier: the RTFD loader on
`NSAttributedString` lives in AppKit, which the engine may not import.

## Development

```
python3 tests/run_all.py      # every module standalone, one score
python3 -m pytest tests/     # the terse version
```

The engine (`src/stickies_to_markdown/engine/`) never prints and never
imports UI frameworks — `tests/test_isolation.py` enforces this with a
source grep plus a captured-stream subprocess run. Fixtures are synthetic
RTFD packages (`tests/fixtures/make_fixtures.py`); replace them with real,
sanitised Stickies packages per the first-session checklist before trusting
tiers 1–2.

GPL-3.0.
