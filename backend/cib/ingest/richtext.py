"""Plain text out of the RTF and HTML wrappers that archive exports arrive in.

Deliberately minimal and dependency-free: these exports are text dumps in a formatting wrapper,
and the goal is to recover the text, not to render the document.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser

_RTF_CONTROL = re.compile(r"\\\*?\\?[a-zA-Z]{1,32}(-?\d{1,10})?[ ]?")
_RTF_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_SKIP_GROUPS = re.compile(
    r"\{\\\*?\\?(?:fonttbl|colortbl|stylesheet|info|pict|header|footer|xmlnstbl)[^{}]*"
    r"(?:\{[^{}]*\}[^{}]*)*\}"
)
_BLANKS = re.compile(r"\n{3,}")


def rtf_to_text(data: str) -> str:
    """Strip RTF control words and groups, leaving the document text."""
    text = data
    for _ in range(4):  # nested groups: a few passes is enough for archive exports
        new = _RTF_SKIP_GROUPS.sub("", text)
        if new == text:
            break
        text = new
    text = text.replace("\\par", "\n").replace("\\line", "\n").replace("\\tab", "\t")
    text = _RTF_HEX.sub(lambda m: bytes([int(m.group(1), 16)]).decode("cp1252", "replace"), text)
    text = _RTF_CONTROL.sub("", text)
    text = text.replace("{", "").replace("}", "")
    text = text.replace("\\~", " ").replace("\\_", "-").replace("\\\\", "\\")
    return _BLANKS.sub("\n\n", text).strip()


class _TextExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "head"})
    _BREAK = frozenset({"p", "br", "div", "tr", "li", "h1", "h2", "h3", "h4",
                        "hr", "table"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self._BREAK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skipping:
            self.parts.append(data)


def html_to_text(data: str) -> str:
    parser = _TextExtractor()
    parser.feed(data)
    text = html.unescape("".join(parser.parts))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


def to_text(path, encoding: str = "utf-8") -> str:
    """Read a file and return its text, whatever wrapper it arrived in."""
    from pathlib import Path

    p = Path(path)
    raw = p.read_text(encoding=encoding, errors="replace")
    suffix = p.suffix.lower()
    if suffix == ".rtf" or raw.lstrip().startswith("{\\rtf"):
        return rtf_to_text(raw)
    if suffix in (".html", ".htm") or raw.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
        return html_to_text(raw)
    return raw
