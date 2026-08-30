"""
RTFD package -> (markdown text, [attachment filenames])  (handoff §5.3)

Two tiers behind one convert() entry point:

1. "textutil"  /usr/bin/textutil -convert html (Cocoa HTML Writer), walked
               with html.parser into Markdown: bold/italic, ordered and
               unordered lists, attachments. macOS built-in; no PyObjC.
2. "text"      stdlib RTF text extraction. Any OS; the floor that can never
               produce nothing; what Linux CI exercises.

"auto" tries 1 -> 2, falling through on any failure. Attachments are
enumerated from the package directory in every tier (the RTF references
them, but the directory listing is the truth).

There is deliberately no PyObjC/Foundation tier. Verified 2026-08-30: the
RTFD-loading initializer on NSAttributedString
(initWithURL:options:documentAttributes:error:) is an AppKit category, so
Foundation-only PyObjC cannot reach it, and AppKit is forbidden in the
engine. A future high-fidelity tier would have to be an AppKit helper run
in a subprocess - which is what textutil already is, courtesy of Apple.
"""

import os
import re
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


DEFAULT_CODE_BLOCK_MIN = 6        # escapes needed before considering a fence
DEFAULT_CODE_BLOCK_DENSITY = 4.0  # escapes per 100 non-space characters


def convert(rtfd_path, converter="auto", logger=None,
            code_block_min=DEFAULT_CODE_BLOCK_MIN,
            code_block_density=DEFAULT_CODE_BLOCK_DENSITY):
    """
    (markdown_text, attachment_filenames, body_format) where body_format is
    "markdown" or "code". `converter` per config: auto | textutil | text.

    A note whose plain text would need heavy escaping (formulas, passwords,
    shell snippets) is not prose: rather than litter it with backslashes it
    is emitted verbatim in a fenced code block. The decision is made on the
    tier-3 plain text so it is the same whichever tier renders. Either
    threshold at 0 disables it.
    """
    logger = logger or get_logger()
    attachments = list_attachments(rtfd_path)

    if code_block_min and code_block_density:
        try:
            with open(os.path.join(rtfd_path, RTF_NAME), 'rb') as handle:
                plain = rtf_to_text(handle.read())
            if needs_code_block(plain, code_block_min, code_block_density):
                return _fence(_tidy(plain)), attachments, "code"
        except OSError as error:
            logger.debug(f"Plain-text probe failed on {rtfd_path}: {error}")

    tiers = {"textutil": _convert_textutil, "text": _convert_text}
    order = [converter] if converter in tiers else ["textutil", "text"]

    last_error = None
    unavailable = []
    for name in order:
        try:
            markdown = tiers[name](rtfd_path)
            if markdown is not None:
                return _tidy(markdown), attachments, "markdown"
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


# Inline: characters that misrender wherever they appear. `_` only at a
# word boundary (my_var is safe; _word_ is not); `#` only when it would
# form an Obsidian tag; `<` only when it could open a tag/autolink; `&`
# only when it forms an entity; `~~`, `==`, `%%` only doubled.
_INLINE_ESCAPE = re.compile(
    r"([\\*`$\[\]])"                      # always
    r"|(?<![A-Za-z0-9])(_)|(_)(?![A-Za-z0-9])"  # underscore at word edge
    r"|(#)(?=[A-Za-z_/])"                 # #tag
    r"|(<)(?=[A-Za-z/!?])"                # <tag> <http:...>
    r"|(&)(?=#?[A-Za-z0-9]+;)"            # &entity;
    r"|(~)(?=~)|(=)(?==)|(%)(?=%)"        # ~~ == %%
)
# Line start: headings, quotes, bullets, numbered items, thematic breaks
# and setext underlines (a line of --- or === makes the line ABOVE a
# heading). Escaping the first character defuses all of them.
_LINE_START_ESCAPE = re.compile(
    r"^(?P<c>[#>]|[-+*](?=\s)|(?=\d{1,9}[.)]\s)\d|(?=[-*_=]\s*[-*_=\s]*$)[-*_=])",
    re.MULTILINE)
# 4+ spaces or a tab at line start = indented code block. Non-breaking
# spaces keep the visual indent without that meaning.
_INDENT = re.compile(r"^( {4,}|\t+| *\t[ \t]*)", re.MULTILINE)


def count_escapes(text):
    """How many characters escape_markdown() would touch."""
    return (len(_INLINE_ESCAPE.findall(text))
            + len(_LINE_START_ESCAPE.findall(text))
            + len(_INDENT.findall(text)))


def escape_density(text):
    """Escapes per 100 non-whitespace characters."""
    length = len(re.sub(r"\s", "", text))
    return 100.0 * count_escapes(text) / length if length else 0.0


def needs_code_block(text, minimum=DEFAULT_CODE_BLOCK_MIN,
                     density=DEFAULT_CODE_BLOCK_DENSITY):
    return count_escapes(text) >= minimum and escape_density(text) >= density


def _fence(text):
    """Wrap verbatim text in a fence longer than any backtick run inside."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}\n{text.rstrip(chr(10))}\n{fence}\n"


def first_content_line(markdown):
    """First non-empty line, ignoring a leading code fence."""
    for line in markdown.split("\n"):
        if line.strip() and not line.startswith("```"):
            return line.strip()
    return ""


def escape_markdown(text):
    """
    Backslash-escape punctuation that a Markdown renderer (CommonMark or
    Obsidian) would otherwise interpret, so note text is shown as written.
    Verified need: Excel formulas like "*U*" (italic) and $A$5 (Obsidian
    inline math). Only plain text goes through here - markup the converter
    itself produces (**, *, list prefixes, attachment marks) is added
    afterwards and is not escaped.
    """
    def inline(match):
        return "\\" + next(g for g in match.groups() if g)

    text = _INLINE_ESCAPE.sub(inline, text)
    text = _LINE_START_ESCAPE.sub(lambda m: "\\" + m.group("c"), text)
    return _INDENT.sub(lambda m: m.group(1).replace("\t", "\u00a0" * 4)
                       .replace(" ", "\u00a0"), text)


def _tidy(text):
    """Normalise line endings, trim trailing spaces, collapse 3+ blank lines."""
    lines = [line.rstrip() for line in
             text.replace('\r\n', '\n').replace('\r', '\n').split('\n')]
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


# --- tier 1: textutil -> HTML -> markdown (macOS) --------------------------

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
    Cocoa HTML Writer output -> markdown. Verified shape (2026-08-30):
    every line is a <p class="pN">; a blank line is <p><br></p>; colour
    and kerning are <span class="sN"> (ignored); bold/italic are <b>/<i>;
    lists are <ol class="ol1"><li class="liN">; images are <img src>.

    So <p> is a LINE break, not a paragraph break - otherwise the whole
    note double-spaces. Headings (never emitted by Stickies, but harmless)
    keep paragraph spacing.
    """

    # Containers whose text is not content. Void tags (meta, link, br, img)
    # must NOT be here: they have no end tag, so a depth counter never
    # comes back down and the entire body gets discarded.
    _SKIP = {"head", "style", "script", "title"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.list_stack = []          # ["ul"] or ["ol", counter]
        self.skip_depth = 0
        self.pending_prefix = ""

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in ("b", "strong"):
            self.out.append("**")
        elif tag in ("i", "em"):
            self.out.append("*")
        elif tag == "u":
            self.out.append("<u>")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._paragraph()
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
            self._newline()
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
        if self.skip_depth:
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
            self._newline()
        elif tag in ("p", "div", "li"):
            self._newline()

    def handle_data(self, data):
        if self.skip_depth:
            return
        text = data.replace('\n', ' ')
        if not text.strip():
            # Cocoa's <span class="Apple-converted-space"> </span> carries a
            # real space between words; keep single spaces, drop pure
            # whitespace runs that came from HTML source formatting.
            if text == " " and self.out and not self.out[-1].endswith(("\n", " ")):
                self.out.append(" ")
            return
        if self.pending_prefix:
            self.out.append(self.pending_prefix)
            self.pending_prefix = ""
        self.out.append(escape_markdown(text))

    def _newline(self):
        if self.out and not ''.join(self.out[-2:]).endswith('\n'):
            self.out.append('\n')

    def _paragraph(self):
        joined = ''.join(self.out[-4:])
        if self.out and not joined.endswith('\n\n'):
            self._newline()
            self.out.append('\n')

    def result(self):
        text = ''.join(self.out)
        # <b></b> (empty) and <b>a</b><b>b</b> (adjacent runs) both leave
        # "****"; dropping it is correct in both cases.
        return text.replace("****", "")


def html_to_markdown(html_text):
    walker = _HtmlWalker()
    walker.feed(html_text)
    walker.close()
    return walker.result()


# --- tier 2: stdlib RTF text extraction (any OS) ---------------------------

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
        return escape_markdown(rtf_to_text(handle.read()))


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
