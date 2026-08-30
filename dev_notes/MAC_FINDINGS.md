# Mac findings (verified, not inferred)

Source: `stickies_verify_20260830-140919.log` and `-142131.log`, macOS with
Python 3.12.13, run from VS Code's embedded terminal. Each item below replaces an
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

## Live-write behavior (partial)

Observed for **note creation** (second log, step 4):

    14:21:49  CREATED  <uuid>.rtfd            <- a FLAT FILE, not a directory
    14:21:54  CREATED  <uuid>.rtfd/TXT.rtf    <- package directory appears
    14:21:54  DELETED  <uuid>.rtfd            <- the flat file is gone
    14:21:54  CHANGED  .SavedStickiesState   REPLACED (temp-and-rename)

- A new note is born as a flat `.rtfd` file (an NSFileWrapper flat
  serialization) and becomes a real package ~5 s later on first content
  save. `enumerate_notes()` skips non-directories with a log line; Phase 2's
  watcher must treat a flat-file create as "not a note yet" and wait for
  the directory. Settle logic on the package covers this naturally (a file
  has no `TXT.rtf` inside; the signature changes when the dir lands).
- `.SavedStickiesState` is rewritten by temp-and-rename on every save.
  Phase 2 should never react to it directly beyond a debounced colour
  refresh; the note's own package is the change signal.
- **Editing an existing note was not captured** in either run: still
  unknown whether `TXT.rtf` is rewritten in place or replaced. The script's
  step 4 now steers a specific edit/focus-change/close sequence.

## Colour calibration (4 of 6)

    yellow  #fef49c  hue  54  sat 0.39
    blue    #adf4ff  hue 188  sat 0.32
    green   #b2ffa1  hue 109  sat 0.37
    pink    #ffc7c7  hue   0  sat 0.22   <- pure red tint; the 0-20 band

All four classify correctly with the current `_HUE_BANDS`. Purple and gray
are unverified; make one note of each and re-run `--steps 6`. The state
file also carries `ControlColor`, `HighlightColor`, `SpineColor` (darker
variants of the same hue) - not used.

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
  placed inline where the image sits rather than appended. Bold/italic
  still unexercised - that note has none.

## Still open

- [ ] **Existing-note edit pattern** (step 4): creation is understood;
      editing is not. Re-run `--steps 4 --watch-seconds 45` and follow the
      on-screen sequence (type into an existing note, change focus, close
      its window). Need: `TXT.rtf` in-place vs replaced, and the delay.
- [ ] **Colour calibration**: purple and gray only (`--steps 6`).
- [ ] **Bold/italic through textutil** (step 7 on a formatted note).
- [ ] **TCC service name** for `tccutil reset` (see Permissions).
- [ ] **Real fixtures** (step 8 with `--capture`, then sanitise).

# End of file #
