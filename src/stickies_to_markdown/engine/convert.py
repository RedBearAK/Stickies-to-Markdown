r"""
RTFD package -> (markdown text, [attachment filenames], body_format)

Tiers behind one convert() entry point, all funnelling through one
HTML -> Markdown stage so escaping and structure rules live in one place:

1. "textutil"  /usr/bin/textutil -convert html (Cocoa HTML Writer). Apple's
               own reader of Apple's own RTF: lists as <ol>/<ul>, images as
               <img>. macOS only, no dependencies.
2. "pandoc"    pandoc -f rtf -t html via pypandoc, when installed (pip
               pypandoc-binary bundles the binary). Any OS. `\par` is
               rewritten to `\line` first: pandoc's RTF reader otherwise
               turns every line into a paragraph and DROPS blank lines.
3. "text"      stdlib RTF text extraction. Any OS; the floor that can never
               produce nothing.

HTML -> Markdown is markdownify when importable (maintained, handles
nested lists, tables, entities), configured so that all plain-text
escaping goes through escape_markdown() below - markdownify's own escaping
knows CommonMark but not Obsidian (#tags, ==, %%). Without markdownify a
small html.parser walker (verified against real Cocoa output) is used.

Before any tier runs, the plain text is checked for escape density: a note
that is mostly formulas/passwords/snippets is emitted verbatim in a fenced
code block ("code") rather than littered with backslashes ("markdown").
"""

import os
import re
import sys
import html
import subprocess

try:
    import pypandoc                                    # optional
except ImportError:                                    # pragma: no cover
    pypandoc = None

try:
    from markdownify import MarkdownConverter          # optional
except ImportError:                                    # pragma: no cover
    MarkdownConverter = None

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
    "markdown" or "code". `converter` per config: auto | textutil | pandoc | text.

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

    tiers = {"textutil": _convert_textutil, "pandoc": _convert_pandoc,
             "text": _convert_text}
    order = [converter] if converter in tiers else ["textutil", "pandoc", "text"]

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
            logger.debug(f"Converter {name} failed on '{rtfd_path}': {error}")
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


# --- tier 1: textutil -> HTML -> markdown (macOS) -----------------------------

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


# --- tier 2: pandoc -> HTML -> markdown (any OS, optional) --------------------

_RTF_PAR = re.compile(rb"\\par\b")
# \uN escapes for astral characters (emoji) come as a surrogate pair, each
# followed by its fallback char. Pandoc's RTF reader emits the two halves
# as-is and they cannot be encoded; fuse them into the real character.
_RTF_SURROGATE_PAIR = re.compile(
    rb"\\u(-?\d+)\s?(?:\?|\\'[0-9A-Fa-f]{2})?\\u(-?\d+)\s?(?:\?|\\'[0-9A-Fa-f]{2})?")


def _fuse_surrogates(match):
    high, low = (int(v) for v in match.groups())
    high += 65536 if high < 0 else 0
    low += 65536 if low < 0 else 0
    if 0xD800 <= high < 0xDC00 and 0xDC00 <= low < 0xE000:
        code = 0x10000 + ((high - 0xD800) << 10) + (low - 0xDC00)
        return chr(code).encode("utf-8")
    return match.group(0)


_pandoc_state = {"known": False, "available": False, "probing": False}
_pandoc_lock = __import__("threading").Lock()


def _probe_pandoc():
    available = False
    if pypandoc is not None:
        try:
            # Runs `pandoc --version` against every candidate path; the first
            # launch of a fresh binary can take seconds on macOS.
            available = bool(pypandoc.get_pandoc_path())
        except Exception:       # noqa: BLE001 - missing binary raises OSError
            available = False
    with _pandoc_lock:
        _pandoc_state.update(known=True, available=available, probing=False)


def pandoc_available(block=True):
    """
    Whether the pandoc tier can run. The probe is done once and cached.
    With block=False the answer may be None ("still checking") so a UI can
    render immediately; a background probe is kicked off in that case.
    """
    with _pandoc_lock:
        if _pandoc_state["known"]:
            return _pandoc_state["available"]
        if not block:
            if not _pandoc_state["probing"]:
                _pandoc_state["probing"] = True
                __import__("threading").Thread(target=_probe_pandoc, daemon=True).start()
            return None
    _probe_pandoc()
    return _pandoc_state["available"]


def _convert_pandoc(rtfd_path):
    if not pandoc_available():
        return None
    with open(os.path.join(rtfd_path, RTF_NAME), 'rb') as handle:
        raw = handle.read()
    # Every Stickies line is \par. Pandoc maps \par to a Paragraph and drops
    # empty ones, losing blank lines; \line maps to a hard break and keeps
    # them. Decode latin-1 so bytes pass through 1:1 (RTF escapes its own
    # non-ASCII as \'xx, which pandoc decodes).
    pre = _RTF_SURROGATE_PAIR.sub(_fuse_surrogates, _RTF_PAR.sub(rb"\\line", raw))
    # Valid RTF is ASCII (non-ASCII is \'xx / \uN, which pandoc decodes), so
    # utf-8 is safe and carries the fused emoji through intact.
    pre = pre.decode("utf-8", errors="replace")
    html_text = pypandoc.convert_text(pre, "html", format="rtf",
                                      extra_args=["--wrap=none"])
    return html_to_markdown(html_text)


# --- HTML -> Markdown stage ----------------------------------------------------

if MarkdownConverter is not None:

    class _Converter(MarkdownConverter):
        """
        markdownify tuned for note HTML: every <p> is a LINE (Cocoa wraps
        each line in one; pandoc uses <br>), images become attachment marks
        for the writer, and ALL text escaping is escape_markdown() - the
        library's own escape_* options are off.
        """

        class Options(MarkdownConverter.DefaultOptions):
            heading_style = "atx"
            bullets = "-"
            strong_em_symbol = "*"
            newline_style = "spaces"       # "  \n"; _tidy strips the spaces
            escape_asterisks = False
            escape_underscores = False
            escape_misc = False

        def escape(self, text, parent_tags):
            if not text:
                return ""
            return escape_markdown(text)

        # markdownify collapses newlines where child strings meet, so a
        # blank line must be carried as "  \n" (content + newline) rather
        # than a bare "\n"; _tidy strips the spaces afterwards. <br> keeps
        # the library default "  \n" for the same reason.
        BLANK = "  \n"

        def convert_p(self, el, text, parent_tags):
            if "_inline" in parent_tags:
                return " " + text.strip() + " "
            body = text.strip("\n")
            return self.BLANK if not body.strip() else body + "\n"

        convert_div = convert_p

        def convert_list(self, el, text, parent_tags):
            # The library pads top-level lists with a blank line above;
            # in a note the list follows its line directly (blank lines
            # are explicit <p><br></p> anyway).
            if "li" in parent_tags:
                return super().convert_list(el, text, parent_tags)
            return text.strip("\n") + "\n"

        convert_ul = convert_list      # the base class binds these to ITS
        convert_ol = convert_list      # convert_list, so rebind here

        def convert_hN(self, n, el, text, parent_tags):
            return f"\n\n{'#' * n} {text.strip()}\n\n"

        def convert_img(self, el, text, parent_tags):
            src = el.attrs.get("src", "") or ""
            name = os.path.basename(src.split("?")[0])
            return f"\n@@ATTACHMENT:{html.unescape(name)}@@\n" if name else ""

    _converter = _Converter()
else:
    _converter = None


class _HtmlWalker(HTMLParser):
    """
    Cocoa HTML Writer output -> markdown. Verified shape (2026-08-30):
    every line is a <p class="pN">; a blank line is <p><br></p>; color
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
    """markdownify when installed, else the stdlib walker; same contract."""
    if _converter is not None:
        return _tidy(_converter.convert(html_text))
    return html_to_markdown_fallback(html_text)


def html_to_markdown_fallback(html_text):
    """The stdlib walker, unconditionally (tests exercise both paths)."""
    walker = _HtmlWalker()
    walker.feed(html_text)
    walker.close()
    return _tidy(walker.result())


# --- tier 3: stdlib RTF text extraction (any OS) ------------------------------

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
