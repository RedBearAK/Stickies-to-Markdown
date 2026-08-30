#!/usr/bin/env python3
"""Tier-3 RTF extraction and the tier-2 HTML walker (both run anywhere)."""

from _helpers import FIXTURES, check, run_suite

from stickies_to_markdown.engine.convert import (
    rtf_to_text, html_to_markdown, convert, list_attachments)


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


if __name__ == "__main__":
    tests = [test_paragraphs_and_escapes, test_destinations_skipped,
             test_unicode_and_emoji, test_empty_note_is_empty,
             test_convert_entry_point_falls_through,
             test_list_attachments_ignores_rtf_and_hidden, test_html_walker]
    exit(0 if run_suite("conversion tests", tests) else 1)


# End of file #
