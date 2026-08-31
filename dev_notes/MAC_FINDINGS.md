# Mac findings (verified, not inferred)

Source: `stickies_verify_20260830-140919.log`, `-142131.log`, `-143623.log`,
`-144310.log`, `-144917.log`, `-150158.log` (typing test), `-151147.log`
(colours), `-151320.log`, `-152249.log` (all-package converter check);
macOS with Python 3.12.13, VS Code terminal. Each item below replaces an
assumption in the handoff §4 or in Phase 1 code.

## Permissions

- The Stickies container **is** TCC-protected, but under the
  *"would like to access data from other apps"* category - a **prompting**
  one - not Full Disk Access. FDA is not needed. This is the best case for
  the Phase 2 bundle: the compiled launcher will get a prompt in the app's
  own name on first run instead of requiring a manual System Settings trip.
- The grant attaches to the app hosting the terminal: this run granted
  **VS Code**. Terminal.app will prompt separately. (Consistent with
  handoff §3.1 - TCC credits the responsible process's code identity.)
- Allow / Don't Allow are both silent and permanent; the engine's
  `container_readable()` reports the deny as `permission denied` with the
  System Settings pointer. To re-prompt: `tccutil reset <Service>` - the
  exact service name for this category is still to be confirmed via
  `log show --last 5m --predicate 'subsystem == "com.apple.TCC"'`.

## Container and packages

- Path exactly as assumed: `~/Library/Containers/com.apple.Stickies/Data/Library/Stickies/`
- Contents: `<UUID>.rtfd` packages + `.SavedStickiesState`, nothing else.
- Every package has `TXT.rtf`; attachments sit beside it under their
  original filenames (e.g. `pivottable_sorted_by_column.png`).
- RTF header: `{\rtf1\ansi\ansicpg1252\cocoartf2761 \cocoatextscaling0\cocoaplatform0{\fonttbl ...`
  - cp1252 as assumed; multiple fonts in the table (Lucida Grande,
    Helvetica, Times) - font-size-as-heading inference would be unreliable
    and is not attempted.

## .SavedStickiesState  (this was the biggest correction)

- Binary plist. Top level is a **list** of per-note dicts (not a dict
  wrapper).
- Per-note keys: `UUID`, `StickyColor`, `ControlColor`, `HighlightColor`,
  `SpineColor` (each `{Red, Green, Blue, Alpha}` floats 0..1), `Frame`,
  `ExpandedSize`, `ExpandFrameY`, `Floating`, `Translucent`, `ZOrder`,
  `SpellCheckingTypes`.
- Colour is therefore a float RGB, **not** an enum. `stickies.py` now
  classifies `StickyColor` by saturation (grey) then hue band into the six
  palette names, and also exposes the exact hex (`color-hex` in front
  matter). One calibration point so far: yellow = `#fef49c`, hue 54°.
- Order comes from `ZOrder`.

## Live-write behavior (verified, five runs)

**Creation.** A new note appears as a flat `.rtfd` *file* and becomes a
package directory (`<uuid>.rtfd/TXT.rtf`) on its first content save -
observed 3 s, 5 s and 12 s later, so "until first save", not a fixed
delay. A note created and closed at once shows as a flat file that
appears and vanishes without ever becoming a package. 16 notes created in
3 s (Cmd-N with key repeat, apparently) all became packages in one batch
6 s later.

**Attribute changes save the package.** One note was replaced at +0, +9,
+27, +35 s during continuous fiddling with its colour, position,
collapsed/expanded state and focus - no typing. So recolouring, moving
and collapsing all rewrite `TXT.rtf` (content unchanged) within seconds,
not just the state file. Every save is `TXT.rtf` **replaced** (new
inode), the package directory's mtime touched, and `.SavedStickiesState`
rewritten. Once the replace was caught mid-flight as `TXT.rtf` DELETED
then CREATED 0.5 s later - the file briefly does not exist during a save.

**Typing alone: idle autosave ~10-12 s.** Hands-off test (sixth log): a
note typed into and then left untouched was saved once, 12 s after its
package appeared, and never again during minutes of idle. Together with
the 8-18 s spacing of attribute-change saves this reads as a ~10 s
debounce after the last change. So typed text reaches disk on its own;
the mirror can promise "edits appear within about 15-20 s" (debounce +
settle on top). One data point for pure typing - re-measure if a user
reports otherwise.

**Closing is deleting.** Stickies has no close-but-keep: Cmd-W removes
the note - silently when empty, after a confirmation when it has text.
The 16-note burst was repeated Cmd-N, then Cmd-W on each blank note.

**Deletion** (= closing the window). The package directory is removed
immediately. The state file is NOT updated for it - not on later rewrites
for other reasons, not in any tolerable wait; observed to clear only on
quit.

**State file.** Rewritten by temp-and-rename on every save, on activation,
and at times with no package activity at all. Since attribute changes
also rewrite the package, it carries no signal the package does not.

## Phase 2 watcher rules (derived from the above)

1. **Existence = the `<uuid>.rtfd` directory.** A flat `.rtfd` file is
   "not a note yet" - ignore until it becomes a directory. A missing
   `TXT.rtf` inside an existing directory is "mid-save" - wait, do not
   treat as deletion. Deletion is the directory itself vanishing.
2. **Change signal = any event under `<uuid>.rtfd/`** (create, move,
   delete of TXT.rtf or attachments) or the directory's mtime. Map it to
   the uuid, then debounce per note. Do not expect `on_modified` on
   TXT.rtf; it is replaced, not rewritten. Expect the stream to be noisy:
   a move or collapse rewrites the package with identical text, so most
   events end as `unchanged` after the byte-compare - count them, don't
   suppress them, so the status line shows the watcher is alive.
3. **Defaults fit the observed cadence:** `settle_seconds = 1` (the
   delete-then-create gap was 0.5 s), `debounce_seconds = 3` (saves are
   8+ s apart; an edit reaches the mirror ~10-15 s after the keystroke).
4. **State file: never a trigger.** Re-read it (cheap, tolerant) as part
   of handling a package event, so the colour written with that note is
   current. A recolour rewrites the package anyway (verified), so no
   separate state-file watch is needed. Never derive existence from it;
   stale deleted entries persist until quit.
5. **Batches are normal.** Sixteen creations in three seconds must not
   produce sixteen concurrent conversions; a single worker draining a
   per-uuid pending set, oldest first, is enough.

## Colour calibration (6 of 6)

    yellow  #fef49c  hue  54  sat 0.39
    green   #b2ffa1  hue 109  sat 0.37
    blue    #adf4ff  hue 188  sat 0.32
    purple  #b6caff  hue 224  sat 0.29   <- a periwinkle, not a violet
    pink    #ffc7c7  hue   0  sat 0.22   <- pure red tint; the 0-20 band
    gray    #eeeeee  sat 0.00

Blue and purple are only 36 degrees apart; the band boundary is at 206.
(Assumes note 45bdae71 in the colour log was the purple one - it appeared
when a purple was requested.) The state file also carries `ControlColor`,
`HighlightColor`, `SpineColor` (darker variants of the same hue) - unused.

## Conversion

- **textutil works** on real packages (rc=0). Output shape (Cocoa HTML
  Writer): every line is `<p class="pN">`, blank lines are `<p><br></p>`,
  colours/kerning are `<span class="sN">`, `<span class="Apple-converted-space">`
  carries inter-word spaces, `ol.ol1`/`li.liN` for lists. The walker was
  rewritten for this shape; two bugs fixed (void `<meta>` tags poisoned the
  skip counter and discarded the whole body; `<p>` was treated as a
  paragraph, double-spacing everything).
- **Foundation tier is not viable and was removed**:
  `NSAttributedString.initWithURL:options:documentAttributes:error:` is an
  AppKit category; Foundation-only PyObjC raises `AttributeError` on it.
  Importing AppKit into the engine is off the table by design. If a
  higher-fidelity tier is ever wanted it must be an AppKit helper in a
  subprocess - which is what textutil already is.
- Tier "text" (stdlib RTF) produced clean output on a 4.7 KB real note.
- After the walker rewrite, **textutil output matches the text tier
  byte-for-byte** on that note (second log), plus the attachment marker
  placed inline where the image sits rather than appended.
- **Bold and italic survive textutil** as `**`/`*` (all-package check:
  a note with both, and a bold heading in another). Both tiers converted
  all 10 real packages. Sizes differ by a few percent on some notes
  (text tier longer) - most likely tabs/trailing whitespace handling; not
  investigated, cosmetic.
- **Note text needs Markdown escaping** - found by the same check: Excel
  formulas containing `"*U*"` and `$A$5` would render as italics and, in
  Obsidian, inline math. Both tiers now backslash-escape `* _ \` $ < [ ]`
  and line-start `# > - +` in plain text (converter-generated markup is
  added after escaping, so real emphasis and lists are unaffected).
  Consequence for existing mirrors: every file rewrites once on the next
  export, since the body (and its hash) changes.

## Pandoc as a converter (probed on Linux with pypandoc-binary 1.17 / pandoc 3.9)

- The RTF reader works: bold/italic, `\'xx` and BMP `\uN` decode correctly.
- **It drops empty paragraphs and makes every `\par` a paragraph**, so a
  raw conversion double-spaces a note and loses its blank lines. Fix in
  use: rewrite `\par` -> `\line` before conversion; each line becomes a hard
  break and blank lines survive.
- **Surrogate-pair emoji come out as `\ufffd\ufffd`.** Fix in use: fuse the
  `\uHIGH ?\uLOW ?` pairs into the real character before conversion.
- Its Markdown writer knows CommonMark escaping but not Obsidian (`#tag`,
  `==`, `%%` pass through), and in `markdown_strict` a `- dash` line or a
  trailing `---` after a hard break is still live syntax. So pandoc is used
  as an RTF -> **HTML** reader only; the shared HTML -> Markdown stage
  (markdownify + escape_markdown) does the escaping for every tier.
- Leading tabs/indentation are dropped by the RTF reader (textutil keeps
  them). Tier order is therefore textutil -> pandoc -> text; pandoc mainly
  gives Linux the formatted path and macOS a fallback.

## Still open

- [x] **Write mechanics** (replace, not rewrite; attribute changes save the
      package; close = delete) - settled.
- [x] **Typing autosave interval** - ~10-12 s idle debounce (one run).
- [x] **Colour calibration** - all six.
- [x] **Bold/italic through textutil** - verified.
- [ ] **TCC service name** for `tccutil reset` (see Permissions).
- [ ] **Real fixtures** (step 8 with `--capture`, then sanitise).

# End of file #
