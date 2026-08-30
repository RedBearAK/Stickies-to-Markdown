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

## Status: Phase 1 (one-shot export)

Working today, verified against a real Stickies container on macOS:

    stickies2md --once                # export every note, then exit
    stickies2md --once --dry-run     # show what would change, write nothing

The live watcher (`--start`), interactive settings menu, and macOS menu bar
app arrive in Phase 2, built on the same engine patterns as
[Duplicate-File-Preventer](https://github.com/RedBearAK/Duplicate-File-Preventer).
Remaining verification items (Stickies' live-write pattern, the last five
colour bands) are tracked in `dev_notes/MAC_FINDINGS.md`.

## Setup

```
pip install .
stickies2md --set output_dir=~/Obsidian/Vault/Synced_from_Stickies
stickies2md --once
```

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

Config lives at `~/Library/Application Support/StickiesToMarkdown/` (macOS)
or `~/.config/stickies-to-markdown/` (Linux), JSON, hot-reload-friendly.

## Converter tiers

1. **textutil** — Apple's built-in RTF→HTML (Cocoa HTML Writer), walked
   into Markdown: bold/italic, ordered and unordered lists, attachments.
   macOS only. Verified against real Stickies output.
2. **text** — a small stdlib RTF text extractor: paragraphs, unicode
   (including emoji), attachments listed. Runs anywhere; this is what the
   Linux test suite exercises and the floor that can never produce nothing.

`auto` tries textutil then text. There is no PyObjC tier: the RTFD loader on
`NSAttributedString` lives in AppKit, which the engine is not allowed to
import (see `dev_notes/MAC_FINDINGS.md`).

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
