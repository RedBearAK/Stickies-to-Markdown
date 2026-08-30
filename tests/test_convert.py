#!/usr/bin/env python3
"""Tier-3 RTF extraction and the tier-2 HTML walker (both run anywhere)."""

from _helpers import FIXTURES, check, run_suite

from stickies_to_markdown.engine.convert import (
    rtf_to_text, html_to_markdown, convert, list_attachments, escape_markdown)


def test_paragraphs_and_escapes():
    text = rtf_to_text((FIXTURES / "11111111-AAAA-4AAA-8AAA-111111111111.rtfd"
                        / "TXT.rtf").read_bytes())
    ok = True
    ok &= check(text == "Grocery list\nMilk and eggs\nCoffee \u2013 dark roast\n",
                "paragraph breaks and \\'xx escape decoded",
                f"unexpected text: {text!r}")
    return ok


def test_destinations_skipped():
    text = rtf_to_text(rb"{\rtf1\ansi{\fonttbl\f0 Helvetica;}"
                       rb"{\*\expandedcolortbl;;}Hello\par}")
    return check(text == "Hello\n",
                 "fonttbl and ignorable destinations skipped",
                 f"leaked destination text: {text!r}")


def test_unicode_and_emoji():
    text = rtf_to_text((FIXTURES / "55555555-EEEE-4EEE-8EEE-555555555555.rtfd"
                        / "TXT.rtf").read_bytes())
    ok = check("Caf\u00e9" in text and "\u2014" in text and "\u2603" in text,
               "accents, em-dash and BMP unicode decoded",
               f"unicode mangled: {text!r}")
    ok &= check("\U0001f694" in text,
                "surrogate-pair emoji fused to one code point",
                f"emoji not fused: {text!r}")
    return ok


def test_empty_note_is_empty():
    text = rtf_to_text((FIXTURES / "44444444-DDDD-4DDD-8DDD-444444444444.rtfd"
                        / "TXT.rtf").read_bytes())
    return check(text.strip() == "", "empty note yields empty text",
                 f"got: {text!r}")


def test_convert_entry_point_falls_through():
    # On Linux tiers 1-2 are unavailable; "auto" must land on tier 3.
    markdown, attachments = convert(
        str(FIXTURES / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd"), "auto")
    ok = check("Whiteboard photo" in markdown,
               "auto converter fell through to tier 3",
               f"no text: {markdown!r}")
    ok &= check(attachments == ["photo.png"],
                "attachment enumerated from the package directory",
                f"attachments: {attachments!r}")
    return ok


def test_list_attachments_ignores_rtf_and_hidden():
    names = list_attachments(
        str(FIXTURES / "77777777-ABAB-4ABA-8ABA-777777777777.rtfd"))
    return check(names == ["photo.png"],
                 "TXT.rtf and dotfiles excluded from attachments",
                 f"got: {names!r}")


def test_html_walker():
    html_text = ("<html><head><style>body{}</style></head><body>"
                 "<p>Title line</p>"
                 "<p><b>Bold</b> and <i>italic</i></p>"
                 "<ul><li>one</li><li>two</li></ul>"
                 "<ol><li>first</li><li>second</li></ol>"
                 "<p><img src='file.png'></p>"
                 "</body></html>")
    markdown = html_to_markdown(html_text)
    ok = True
    ok &= check("**Bold**" in markdown and "*italic*" in markdown,
                "bold/italic tags -> markdown emphasis", markdown)
    ok &= check("- one" in markdown and "- two" in markdown,
                "ul -> dash bullets", markdown)
    ok &= check("1. first" in markdown and "2. second" in markdown,
                "ol -> numbered items", markdown)
    ok &= check("@@ATTACHMENT:file.png@@" in markdown,
                "img -> attachment marker for the writer", markdown)
    ok &= check("body{}" not in markdown, "style content skipped", markdown)
    return ok


COCOA_HTML = """<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 4.01//EN" "http://www.w3.org/TR/html4/strict.dtd">
<html>
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <meta name="Generator" content="Cocoa HTML Writer">
  <title></title>
  <style type="text/css">
    p.p1 {margin: 0.0px 0.0px 0.0px 0.0px; font: 12.0px 'Lucida Grande'}
    span.s1 {color: #003ecc}
    ol.ol1 {list-style-type: decimal}
  </style>
</head>
<body>
<p class="p1">Helpful Excel Functions</p>
<p class="p2"><br></p>
<p class="p1">=MID(<span class="s1">B2</span>,FIND(" ",B2)+1)</p>
<p class="p1"><b>Bold line</b> then <i>italic</i><span class="Apple-converted-space"> </span>end</p>
<p class="p5"><span class="s4"></span><br></p>
<ol class="ol1"><li class="li10">first</li><li class="li10">second</li></ol>
<p class="p1"><img src="file:///tmp/x.rtfd/pivot.png" alt="pivot.png"></p>
</body>
</html>"""


def test_cocoa_html_writer_shape():
    """Built from real textutil output (2026-08-30 log)."""
    markdown = html_to_markdown(COCOA_HTML)
    lines = markdown.strip("\n").split("\n")
    ok = check("Helpful Excel Functions" in markdown,
               "body text survives <head> with void <meta> tags",
               f"lost body: {markdown!r}")
    ok &= check('=MID(B2,FIND(" ",B2)+1)' in markdown,
                "colour spans are transparent", f"{markdown!r}")
    ok &= check("**Bold line** then *italic* end" in markdown,
                "b/i and Apple-converted-space handled", f"{markdown!r}")
    ok &= check(lines[0] == "Helpful Excel Functions" and lines[1] == "",
                "<p> is a line break; <p><br></p> is the blank line",
                f"{lines[:3]}")
    ok &= check("1. first" in markdown and "2. second" in markdown,
                "ol/li -> numbered list", f"{markdown!r}")
    ok &= check("@@ATTACHMENT:pivot.png@@" in markdown,
                "img src basename -> attachment marker", f"{markdown!r}")
    ok &= check("\n\n\n" not in markdown,
                "no double-spacing of consecutive lines", f"{markdown!r}")
    return ok


def test_markdown_punctuation_escaped():
    """Real case (2026-08-30): Excel formulas in notes render as italics/math."""
    formula = '=SUMIF($A$5:$A$57,"*U*",C5:C57)'
    escaped = escape_markdown(formula)
    ok = check(escaped == '=SUMIF(\\$A\\$5:\\$A\\$57,"\\*U\\*",C5:C57)',
               "asterisks and dollars escaped in plain text", escaped)
    cases = {
        "# not a heading": "\\# not a heading",
        "> nor a quote": "\\> nor a quote",
        "- not a bullet": "\\- not a bullet",
        "2024. not a list": "\\2024. not a list",
        "title\n-----": "title\n\\-----",          # setext / rule
        "title\n===": "title\n\\=\\==",
        "    indented": "\u00a0\u00a0\u00a0\u00a0indented",   # not a code block
        "\tindented": "\u00a0\u00a0\u00a0\u00a0indented",
        "see #todo and issue #42": "see \\#todo and issue #42",
        "a < b and <b>": "a < b and \\<b>",
        "AT&T and &copy;": "AT&T and \\&copy;",
        "==hi== ~~x~~ %%hidden%% 50% ~5": "\\==hi\\== \\~~x\\~~ \\%%hidden\\%% 50% ~5",
        "my_var and _word_": "my_var and \\_word\\_",
        "[[wiki]] and [x](y)": "\\[\\[wiki\\]\\] and \\[x\\](y)",
    }
    for raw, want in cases.items():
        got = escape_markdown(raw)
        ok &= check(got == want, f"escape: {raw!r}", f"{raw!r} -> {got!r} (want {want!r})")
    html_text = ('<html><body><p>=SUMIF("*Total*",K14)</p>'
                 '<p><b>Bold</b> and <i>Italic</i></p><ul><li>plain item</li></ul>'
                 '</body></html>')
    markdown = html_to_markdown(html_text)
    ok &= check('"\\*Total\\*"' in markdown and "**Bold**" in markdown
                and "*Italic*" in markdown and "- plain item" in markdown,
                "textutil tier escapes data but keeps real emphasis/list markup",
                f"{markdown!r}")
    return ok

if __name__ == "__main__":
    tests = [test_paragraphs_and_escapes, test_destinations_skipped,
             test_unicode_and_emoji, test_empty_note_is_empty,
             test_convert_entry_point_falls_through,
             test_list_attachments_ignores_rtf_and_hidden, test_html_walker,
             test_cocoa_html_writer_shape, test_markdown_punctuation_escaped]
    exit(0 if run_suite("conversion tests", tests) else 1)


# End of file #
