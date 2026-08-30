"""
RTFD package -> (markdown text, [attachment filenames])  (handoff §5.3)

Three tiers behind one convert() entry point:

1. "foundation"  NSAttributedString via PyObjC (macOS). Best fidelity.
                 Imported ONLY here, ONLY under a darwin guard; the engine
                 isolation test allows Foundation in this file and nowhere
                 else, and forbids AppKit/rumps everywhere.
2. "textutil"    /usr/bin/textutil -convert html, walked with html.parser.
                 macOS built-in; no PyObjC needed.
3. "text"        stdlib RTF text extraction. Any OS; the floor that can
                 never produce nothing; what Linux CI exercises.

"auto" tries 1 -> 2 -> 3, falling through on any failure. Attachments are
enumerated from the package directory in every tier (the RTF references
them, but the directory listing is the truth).

NOTE: tier 1 is written to spec but has NOT run on a Mac yet (checklist §7
step 6). Treat it as unverified until the fixtures pass there.
"""

import os
import sys
import html
import subprocess

from html.parser import HTMLParser

from stickies_to_markdown.engine.logsetup import get_logger


RTF_NAME = "TXT.rtf"
TEXTUTIL = "/usr/bin/textutil"


class ConversionError(Exception):
    """No tier could produce output for this package."""


def list_attachments(rtfd_path):
    """Files in the package other than the RTF itself, sorted."""
    try:
        names = os.listdir(rtfd_path)
    except OSError:
        return []
    return sorted(n for n in names
                  if n != RTF_NAME and not n.startswith('.')
                  and os.path.isfile(os.path.join(rtfd_path, n)))


def convert(rtfd_path, converter="auto", logger=None):
    """
    (markdown_text, attachment_filenames). `converter` per config:
    auto | foundation | textutil | text.
    """
    logger = logger or get_logger()
    attachments = list_attachments(rtfd_path)

    tiers = {"foundation": _convert_foundation,
             "textutil": _convert_textutil,
             "text": _convert_text}
    order = [converter] if converter in tiers else ["foundation", "textutil", "text"]

    last_error = None
    unavailable = []
    for name in order:
        try:
            markdown = tiers[name](rtfd_path)
            if markdown is not None:
                return _tidy(markdown), attachments
            unavailable.append(name)     # tier declined (wrong platform)
        except Exception as error:      # noqa: BLE001 - tiers must fall through
            last_error = error
            logger.debug(f"Converter {name} failed on {rtfd_path}: {error}")
    detail = (f"unavailable on this platform: {', '.join(unavailable)}"
              if unavailable and last_error is None
              else f"last error: {last_error}")
    raise ConversionError(
        f"{rtfd_path}: no converter produced output (tried {', '.join(order)}; "
        f"{detail})")


def _tidy(text):
    """Normalise line endings, trim trailing spaces, collapse 3+ blank lines."""
    lines = [line.rstrip() for line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
    out = []
    blanks = 0
    for line in lines:
        blanks = blanks + 1 if not line else 0
        if blanks <= 2:
            out.append(line)
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return '\n'.join(out) + ('\n' if out else '')


# --- tier 1: Foundation / PyObjC (macOS) -----------------------------------

def _convert_foundation(rtfd_path):
    if sys.platform != "darwin":
        return None
    try:
        import Foundation                                  # noqa: PLC0415
    except ImportError:
        return None

    url = Foundation.NSURL.fileURLWithPath_(rtfd_path)
    options = {"DocumentType": "NSRTFD"}     # NSDocumentTypeDocumentAttribute
    attributed, _attrs, error = (
        Foundation.NSAttributedString.alloc()
        .initWithURL_options_documentAttributes_error_(url, options, None, None))
    if attributed is None:
        raise ConversionError(f"NSAttributedString could not load: {error}")

    text = str(attributed.string())
    length = attributed.length()
    pieces = []
    index = 0
    while index < length:
        attrs, rng = attributed.attributesAtIndex_effectiveRange_(index, None)
        run = text[rng.location:rng.location + rng.length]
        pieces.append(_style_run(run, attrs))
        index = rng.location + rng.length
    return ''.join(pieces)


# NSFontDescriptor symbolic traits (verify on Mac - checklist §7 step 6):
_TRAIT_ITALIC = 1 << 0
_TRAIT_BOLD = 1 << 1


def _style_run(run, attrs):
    """Wrap a run in markdown emphasis based on its font traits."""
    if not run.strip():
        return run
    bold = italic = False
    try:
        font = attrs.get("NSFont")
        if font is not None:
            traits = int(font.fontDescriptor().symbolicTraits())
            italic = bool(traits & _TRAIT_ITALIC)
            bold = bool(traits & _TRAIT_BOLD)
    except Exception:       # noqa: BLE001 - styling is best-effort
        pass
    # Attachment placeholder char (U+FFFC) is dropped; links are appended
    # by the writer from the directory listing.
    run = run.replace('\ufffc', '')
    lead = len(run) - len(run.lstrip())
    trail_ws = run[len(run.rstrip()):]
    core = run.strip()
    if not core:
        return run
    if bold and italic:
        core = f"***{core}***"
    elif bold:
        core = f"**{core}**"
    elif italic:
        core = f"*{core}*"
    return run[:lead] + core + trail_ws


# --- tier 2: textutil -> HTML -> markdown (macOS) --------------------------

def _convert_textutil(rtfd_path):
    if sys.platform != "darwin" or not os.path.exists(TEXTUTIL):
        return None
    proc = subprocess.run(
        [TEXTUTIL, "-convert", "html", "-stdout", rtfd_path],
        capture_output=True, timeout=30)
    if proc.returncode != 0 or not proc.stdout:
        raise ConversionError(
            f"textutil rc={proc.returncode}: {proc.stderr.decode(errors='replace')[:200]}")
    return html_to_markdown(proc.stdout.decode('utf-8', errors='replace'))


class _HtmlWalker(HTMLParser):
    """
    Minimal HTML -> markdown for textutil output: bold/italic/underline,
    ul/ol/li, headings from tags, p/br/div boundaries, img -> attachment
    marker (writer turns it into a real link).
    """

    _SKIP = {"head", "style", "script", "title", "meta"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.list_stack = []          # "ul" | "ol" entries; ol carries a counter
        self.skip_depth = 0
        self.pending_prefix = ""

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip_depth += 1
            return
        if tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "u":
            self.out.append("<u>")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._newline()
            self.out.append("#" * int(tag[1]) + " ")
        elif tag == "ul":
            self.list_stack.append(["ul"])
        elif tag == "ol":
            self.list_stack.append(["ol", 0])
        elif tag == "li":
            self._newline()
            indent = "  " * (len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1][0] == "ol":
                self.list_stack[-1][1] += 1
                self.pending_prefix = f"{indent}{self.list_stack[-1][1]}. "
            else:
                self.pending_prefix = f"{indent}- "
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("p", "div"):
            self._paragraph()
        elif tag == "img":
            src = dict(attrs).get("src", "")
            name = os.path.basename(src.split("?")[0])
            if name:
                self._newline()
                self.out.append(f"@@ATTACHMENT:{html.unescape(name)}@@\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "u":
            self.out.append("</u>")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._paragraph()
        elif tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self._paragraph()
        elif tag in ("p", "div", "li"):
            self._newline()

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data.replace('\n', ' ')
        if not text.strip():
            return
        if self.pending_prefix:
            self.out.append(self.pending_prefix)
            self.pending_prefix = ""
        self.out.append(text)

    def _newline(self):
        if self.out and not ''.join(self.out[-2:]).endswith('\n'):
            self.out.append('\n')

    def _paragraph(self):
        joined = ''.join(self.out[-4:])
        if self.out and not joined.endswith('\n\n'):
            self._newline()
            self.out.append('\n')

    def result(self):
        return ''.join(self.out)


def html_to_markdown(html_text):
    walker = _HtmlWalker()
    walker.feed(html_text)
    walker.close()
    return walker.result()


# --- tier 3: stdlib RTF text extraction (any OS) ---------------------------

_RTF_DEST_SKIP = {
    b"fonttbl", b"colortbl", b"stylesheet", b"info", b"pict",
    b"themedata", b"listtable", b"listoverridetable", b"generator",
    b"expandedcolortbl", b"nextgraphic",
}

_RTF_CHAR_MAP = {
    b"par": "\n", b"line": "\n", b"sect": "\n", b"page": "\n",
    b"tab": "\t", b"emdash": "\u2014", b"endash": "\u2013",
    b"lquote": "\u2018", b"rquote": "\u2019",
    b"ldblquote": "\u201c", b"rdblquote": "\u201d",
    b"bullet": "\u2022", b"emspace": " ", b"enspace": " ",
}


def _convert_text(rtfd_path):
    rtf_path = os.path.join(rtfd_path, RTF_NAME)
    with open(rtf_path, 'rb') as handle:
        return rtf_to_text(handle.read())


def rtf_to_text(data):
    """
    Plain text with paragraph breaks from RTF bytes. Handles groups,
    ignorable/known destinations, \\'xx codepage escapes, \\uN unicode
    with \\ucN fallback skipping. Deliberately format-blind: the floor.
    """
    codec = "cp1252"
    out = []
    # Group state: (skipping, uc)
    stack = [(False, 1)]
    ignorable_pending = False
    skip_fallback = 0
    i = 0
    n = len(data)

    def emit(char):
        if not stack[-1][0]:
            out.append(char)

    while i < n:
        byte = data[i:i + 1]
        if byte == b'{':
            stack.append((stack[-1][0] or ignorable_pending, stack[-1][1]))
            ignorable_pending = False
            i += 1
        elif byte == b'}':
            if len(stack) > 1:
                stack.pop()
            i += 1
        elif byte == b'\\':
            i += 1
            if i >= n:
                break
            symbol = data[i:i + 1]
            if symbol == b'*':
                ignorable_pending = True
                i += 1
            elif symbol == b"'":
                hex_pair = data[i + 1:i + 3].decode('ascii', errors='replace')
                i += 3
                if skip_fallback > 0:
                    skip_fallback -= 1
                    continue
                try:
                    emit(bytes([int(hex_pair, 16)]).decode(codec, errors='replace'))
                except ValueError:
                    pass
            elif symbol in (b'\\', b'{', b'}'):
                i += 1
                if skip_fallback > 0:
                    skip_fallback -= 1
                    continue
                emit(symbol.decode('ascii'))
            elif symbol == b'~':
                i += 1
                emit('\u00a0')
            elif symbol == b'\n' or symbol == b'\r':
                i += 1
                emit('\n')
            elif symbol.isalpha():
                j = i
                while j < n and data[j:j + 1].isalpha():
                    j += 1
                word = data[i:j].lower()
                k = j
                if k < n and data[k:k + 1] == b'-':
                    k += 1
                while k < n and data[k:k + 1].isdigit():
                    k += 1
                param = data[j:k]
                if k < n and data[k:k + 1] == b' ':    # delimiter space
                    k += 1
                i = k

                if word in _RTF_DEST_SKIP:
                    skipping, uc = stack[-1]
                    stack[-1] = (True, uc)
                elif ignorable_pending:
                    # \*\unknowndest ... : skip the whole group.
                    skipping, uc = stack[-1]
                    stack[-1] = (True, uc)
                elif word == b'uc':
                    skipping, _uc = stack[-1]
                    try:
                        stack[-1] = (skipping, int(param or b'1'))
                    except ValueError:
                        pass
                elif word == b'u':
                    try:
                        code = int(param)
                    except ValueError:
                        code = None
                    if code is not None:
                        if code < 0:
                            code += 65536
                        emit(chr(code))
                        skip_fallback = stack[-1][1]
                elif word == b'ansicpg':
                    try:
                        codec = f"cp{int(param)}"
                        "".encode(codec)    # validate
                    except (ValueError, LookupError):
                        codec = "cp1252"
                elif word == b'mac':
                    codec = "mac_roman"
                elif word in _RTF_CHAR_MAP:
                    emit(_RTF_CHAR_MAP[word])
                ignorable_pending = False
            else:
                i += 1      # unknown control symbol: ignore
        elif byte in (b'\r', b'\n', b'\x00'):
            i += 1          # raw newlines in RTF source are not content
        else:
            i += 1
            if skip_fallback > 0:
                skip_fallback -= 1
                continue
            emit(byte.decode(codec, errors='replace'))

    text = ''.join(out)
    try:
        # \uN escapes emit emoji as UTF-16 surrogate pairs; fuse them.
        return text.encode('utf-16', 'surrogatepass').decode('utf-16')
    except UnicodeError:
        return text


# End of file #
