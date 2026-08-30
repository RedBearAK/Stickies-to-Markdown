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

Working today, on the built-in tier-3 converter (any OS):

    stickies2md --once                # export every note, then exit
    stickies2md --once --dry-run     # show what would change, write nothing

The live watcher (`--start`), interactive settings menu, and macOS menu bar
app arrive in Phase 2, built on the same engine patterns as
[Duplicate-File-Preventer](https://github.com/RedBearAK/Duplicate-File-Preventer).
The macOS-only converter tiers (Foundation, textutil) are written but not
yet verified on a Mac — see `dev_notes/FIRST_SESSION_CHECKLIST.md`.

## Setup

```
pip install .
stickies2md --set output_dir=~/Obsidian/Vault/Synced_from_Stickies
stickies2md --once
```

On macOS the terminal (or, later, the app bundle) needs **Full Disk Access**
to read the Stickies container; a denied grant shows up as exactly one
symptom, `permission denied`, which the tool names explicitly.

## The output format (the public interface)

Every mirror file carries YAML front matter. **These generic keys are the
contract** — scripts and future consumers may rely on them:

```yaml
---
synced-by: stickies-to-markdown      # ownership marker (see Safety)
stickies-uuid: 5A2B...-...           # identity, survives retitling
color: yellow                        # from .SavedStickiesState, or "unknown"
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
- A note deleted in Stickies is, per `on_delete`: moved to `_deleted/`
  (default), deleted, or kept.
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
| `on_delete` | `tombstone` | or `delete` / `keep` |
| `read_only_output` | `true` | chmod 444 mirror files |
| `include_attachments` | `true` | copy package attachments |

Config lives at `~/Library/Application Support/StickiesToMarkdown/` (macOS)
or `~/.config/stickies-to-markdown/` (Linux), JSON, hot-reload-friendly.

## Converter tiers

1. **foundation** — `NSAttributedString` via PyObjC: best fidelity
   (bold/italic as `**`/`*`). macOS only; unverified until first Mac run.
2. **textutil** — Apple's built-in RTF→HTML, walked into Markdown
   (headings, lists, emphasis, images). macOS only.
3. **text** — a small stdlib RTF text extractor: paragraphs, unicode
   (including emoji), attachments listed. Runs anywhere; this is what the
   Linux test suite exercises and the floor that can never produce nothing.

`auto` tries each in order and falls through on any failure.

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
