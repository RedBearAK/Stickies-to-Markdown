# First session on the Mac — verification checklist

The Linux-built Phase 1 rests on documented-but-unverified assumptions
about the real container (handoff §7). Work through these **in order** on
the target Mac, recording findings in the blanks. Steps 1–3 gate trusting
the output; steps 4–6 gate tiers 1–2; step 7 upgrades the test fixtures.

Grant the terminal Full Disk Access first (System Settings → Privacy &
Security → Full Disk Access), or step 1 fails with `permission denied` —
which is itself a useful confirmation of the §3.4 health-probe behavior.

---

## 1. Container path and layout

```sh
ls -la ~/Library/Containers/com.apple.Stickies/Data/Library/Stickies/
```

- [ ] Path exists and lists `<UUID>.rtfd` packages: ______
- [ ] `.SavedStickiesState` present: ______
- [ ] Anything else in there (record names): ______

## 2. Package anatomy

```sh
P=$(ls -d ~/Library/Containers/com.apple.Stickies/Data/Library/Stickies/*.rtfd | head -1)
ls -la "$P"; head -c 400 "$P/TXT.rtf"; echo
```

- [ ] `TXT.rtf` present in every package: ______
- [ ] Attachment naming/layout for a note with an image: ______
- [ ] RTF header (record the first line, e.g. `{\rtf1\ansi\ansicpg...`): ______

## 3. Real .SavedStickiesState keys  ← the parser guesses these

```sh
cd ~/Library/Containers/com.apple.Stickies/Data/Library/Stickies/
plutil -convert xml1 -o /tmp/state.xml .SavedStickiesState && open -e /tmp/state.xml
```

- [ ] Top-level shape (dict wrapping a list? which key?): ______
- [ ] Per-note UUID key name: ______
- [ ] Per-note colour key name and value type (int? string?): ______
- [ ] Integer→colour mapping, if int (make 6 notes, one per colour,
      note creation order, read back the values): ______

Then tighten `engine/stickies.py`: replace the candidate-probing in
`_uuid_from_entry` / `_color_from_entry` with the real keys (keep the
tolerant fallback for truncated files), and fix `_COLOR_NAMES` order.

## 4. Live-write behavior (informs Phase 2 debounce/settle)

With Stickies open, type into a note and watch:

```sh
fswatch ~/Library/Containers/com.apple.Stickies/Data/Library/Stickies/ | head -40
```

- [ ] Writes per keystroke, per pause, or only on quit? ______
- [ ] Whole-package replace (temp + rename) or in-place TXT.rtf write? ______
- [ ] Does `.SavedStickiesState` churn alongside? ______

## 5. Tier 2: textutil on a real package

```sh
textutil -convert html -stdout "$P" | head -50
```

- [ ] Produces HTML with the note content: ______
- [ ] How lists/bold/links appear (spot-check against the walker): ______
- [ ] Behavior on a package with an attachment (img src value): ______

## 6. Tier 1: Foundation load + trait constants

```sh
python3 -c "
import Foundation
url = Foundation.NSURL.fileURLWithPath_('$P')
s, a, e = Foundation.NSAttributedString.alloc()\
    .initWithURL_options_documentAttributes_error_(url, {'DocumentType': 'NSRTFD'}, None, None)
print('loaded:', s is not None, 'len:', s and s.length(), 'err:', e)"
```

- [ ] Loads without AppKit imported: ______
- [ ] On a bold+italic note, run tier 1 via
      `stickies2md --once --converter foundation` and check emphasis: ______
- [ ] Symbolic trait constants correct? (`_TRAIT_ITALIC = 1<<0`,
      `_TRAIT_BOLD = 1<<1` in `engine/convert.py`): ______

## 7. Real fixtures

Copy 6–8 *sanitised* packages (nothing private) + the real state file into
`tests/fixtures/`, replacing the synthetic ones; re-run
`python3 tests/run_all.py`. Keep at least: plain, bold/italic, bulleted
list, an attachment note, an emoji note, an empty note.

- [ ] Suite green on real fixtures: ______

## 8. TCC sanity (before building the Phase 2 .app bundle)

- [ ] Fresh terminal *without* FDA: `stickies2md --once` reports the
      permission-denied message naming Full Disk Access: ______
- [ ] After granting FDA: works with no other change: ______
