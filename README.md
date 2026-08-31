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
short debounce and settle). Color, position and collapse changes rewrite
the note too and are absorbed as "unchanged" unless the color changed.
Everything behavioral here was measured on a real Mac — see
`dev_notes/MAC_FINDINGS.md`, whose "Phase 2 watcher rules" section is what
the watcher implements.

## Setup

```
pip install '.[convert]'            # + '[menubar]' on macOS for the menu bar app
stickies2md                         # menu > Settings > Mirror folder, then Start
```

or without the menu:

```
stickies2md --add-output vault=~/Obsidian/Vault/Synced_from_Stickies
stickies2md --set vault.flavor=obsidian
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

**If that prompt comes back on every launch**, TCC is storing the grant
session-scoped. The default signing now sets an identifier-based designated
requirement precisely to avoid that (`codesign -dr -` on the bundle should
show `designated => identifier "com.redbearak.stickies-to-markdown"`; re-run
`--install-app` if it shows a `cdhash` instead). Should it persist anyway,
`--install-app --sign-identity NAME` signs with a Keychain certificate; the
identity is remembered for later re-installs. To see what TCC decided:

```
log show --last 10m --predicate 'subsystem == "com.apple.TCC"' | grep -iE 'stickies|AppData'
```

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

Consumer-specific keys are added only when an output's **flavor** says so;
flavors only ever add, and an output may combine several
(`flavor: "obsidian, sticky-notes"`). The plugin vocabularies were read
from each plugin's source, not guessed:

| flavor | adds | for |
| --- | --- | --- |
| `floating-sticky-notes` | nothing — the generic `color:` key and its values are exactly what it reads | [Floating Sticky Notes](https://github.com/kasairo/floating-sticky-notes) (kasairo) |
| `sticky-notes` | `background_color: Yellow` (capitalised; gray → `Base`) | [Sticky Notes](https://github.com/Abdo-reda/obsidian-sticky-notes-plugin) (abdo-reda) and its fork Simple Sticky Notes |
| `colorful-stickynotes` | `colorful-sticky-bg: mint` (green → mint, purple → lavender) | [Colorful StickyNotes](https://github.com/pandanocturne/obsidian-colorful-stickynotes) |
| `obsidian` | `cssclasses: [stickies-mirror, sticky-yellow]` | any theme, no plugin: the tool installs and enables a CSS snippet in the vault (see below) |

Nothing in the plugin ecosystem tints a note from a color named in its
own front matter; the `obsidian` flavor is that feature. When an output
has it, the tool finds the enclosing vault (nearest `.obsidian/` above the
mirror folder), writes `.obsidian/snippets/stickies-mirror.css` and adds
it to `enabledCssSnippets` in `appearance.json` — tint per color, a
"mirrored from Stickies" banner, and the Properties block hidden on
mirrored notes only. Obsidian applies it on its next reload (Settings ›
Appearance › CSS snippets › reload, or restart). The snippet is
marker-checked like everything else: a file of that name the tool did not
write is never touched, and the tool's own copy is refreshed when its
built-in version changes. `obsidian_snippet: false` on the output turns
this off. The floating-window plugins above color only their own windows.

## The folder explains itself

Every mirror folder gets a first-sorted note,
`_About these notes (read-only mirror).md`, stating that the files are
read-only mirrors of Stickies on a named Mac, how fast edits arrive, what
happens on deletion under this output's policy, and where settings live.
It is maintained like a mirror file (rewritten when the policy changes,
read-only, marker-checked, removed by `--purge-mirror`) and never indexed
as a note. `readme_note: false` on the output turns it off.
Desktop Sticky Notes stores colors in its own settings by file path, so
no front matter can reach it. One caveat: plugins that *write* front matter
back into a note (Colorful StickyNotes adds its own id on first open) will
find the mirror files read-only, which is correct — the sticky is the thing
to edit — but expect a complaint from the plugin the first time.

Files are named from the note's first line (Stickies has no titles).
`filename_style` chooses: `slug` — first line only, a uuid8 suffix added
just when two notes share a first line; `slug-uuid` (default) — always
suffixed, so a retitled note is trivially tracked; `uuid` — the uuid8 alone.
Attachments are copied to `attachments/<uuid8>/` and linked from the body.

Every file also records `source-machine` (a label: the hostname unless you
set one) and `source-machine-id` (8 hex characters of the hardware UUID on
macOS, `/etc/machine-id` on Linux — stable across renames and OS
reinstalls). Stickies do not sync between Macs, so two Macs mirroring into
one shared folder would otherwise each see the other's files as vanished
notes; a writer manages only files whose id is its own. `{machine}` or
`{machine_id}` in an output's `subfolder` keeps them in separate folders.

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

The config is JSON at `~/Library/Application Support/StickiesToMarkdown/`
(macOS) or `~/.config/stickies-to-markdown/` (Linux), edited by the menu,
by `--set`, or by hand; a running watcher picks up changes live. It has
two levels:

```json
{
  "stickies_dir": "~/Library/Containers/com.apple.Stickies/Data/Library/Stickies",
  "converter": "auto",
  "debounce_seconds": 3.0,
  "outputs": [
    {"name": "vault", "output_dir": "~/Obsidian/Vault/Synced_from_Stickies",
     "flavor": "obsidian", "on_delete": "archive", "exclude_colors": ["gray"]},
    {"name": "plain", "output_dir": "~/Dropbox/Notes/Stickies",
     "flavor": "generic", "filename_style": "uuid", "on_delete": "delete"}
  ]
}
```

**Global** keys govern reading, converting and watching; **each output
block** is one mirror folder with its own flavor, naming, deletion policy
and exclusions. A note is converted once and written to every output. The
menu's Settings screen shows the globals with the outputs listed beneath
(`A`, `B`, … to edit one, `+` to add, `-` to remove); from the command line:

```
stickies2md --show-config
stickies2md --set converter=pandoc                 # global
stickies2md --set vault.on_delete=mark             # NAME.KEY for an output
stickies2md --add-output plain=~/Dropbox/Notes     # then --set plain.flavor=...
stickies2md --remove-output plain                  # the folder is left alone
stickies2md --once --output-dir /tmp/check         # one folder, this run only
stickies2md --purge-mirror DIR [--yes]             # remove only what the tool wrote in DIR
```

Point an output at your vault (or any folder): the mirror is created
**inside it as `Synced_from_Stickies/`**, so nothing spills into a vault
root. `subfolder` on the output changes the name; blank it to write
directly into the folder. `--purge-mirror DIR` removes only files carrying
the tool's marker (and their attachments), for cleaning up after a folder
mistake.

A config from before multiple outputs (top-level `output_dir`) is migrated
into a single block named `default` the first time it is read.

| global key | default | meaning |
| --- | --- | --- |
| `stickies_dir` | the Stickies container | override for testing |
| `converter` | `auto` | `textutil` → `pandoc` → `text` fallback chain |
| `debounce_seconds` / `settle_seconds` | `3.0` / `1.0` | watcher timing, calibrated to Stickies' autosave |
| `code_block_min_escapes` / `code_block_density` | `6` / `4.0` | when a note becomes a fenced code block (see below) |
| `dry_run` | `false` | log and report, write nothing |
| `machine_label` | *(hostname)* | this Mac's human name: `source-machine`, `{machine}` |
| `machine_id` | *(detected)* | stable identity: `source-machine-id`, `{machine_id}`; set only to pin |

| output key | default | meaning |
| --- | --- | --- |
| `name` | — | handle for `--set NAME.KEY` and the menu |
| `output_dir` | — | the folder the mirror is created inside |
| `subfolder` | `Synced_from_Stickies` | mirror folder name inside `output_dir`; blank = none |
| `flavor` | `generic` | one or more flavors, comma-separated (see The output format) |
| `filename_style` | `slug-uuid` | `slug` / `uuid` (see The output format) |
| `on_delete` | `archive` | `mark` / `delete` / `keep` — see below |
| `deleted_dir` | `_deleted` | archive folder; relative to the output or absolute |
| `exclude_colors` | `[]` | colors to keep out of this output |
| `exclude_title_regex` | *(none)* | first-line pattern to keep out |
| `on_exclude` | `delete` | policy for a note that becomes excluded |
| `read_only_output` | `true` | chmod 444 mirror files |
| `include_attachments` | `true` | copy package attachments |
| `front_matter` | `true` | write the YAML block |
| `readme_note` | `true` | maintain the first-sorted "read-only mirror" note |
| `obsidian_snippet` | `true` | with the `obsidian` flavor: install/enable the vault CSS snippet |

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
stickies2md --set vault.exclude_colors=gray          # "gray means private", this output only
stickies2md --set vault.exclude_title_regex='^#private\b'
stickies2md --set vault.on_exclude=delete            # archive | mark | delete | keep
```

Exclusion is **reactive**: a note that matches is treated as if it had
been deleted, per `on_exclude` (default `delete`). That matters for
timing. Stickies autosaves a new note about ten seconds after you stop
typing, so a marker typed *after* the content means the note is mirrored
briefly and then removed - and a sync client may have seen it. Color is
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
