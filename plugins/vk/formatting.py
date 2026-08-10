"""Small Markdown subset renderer for VK ``format_data``."""

from __future__ import annotations

import re
from typing import Any


_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_TOKENS = ("***", "**", "*", "_", "`", "<u>", "</u>")


def markdown_to_vk(text: str) -> tuple[str, dict[str, Any] | None]:
    """Render safe Markdown constructs and return UTF-16 VK spans.

    Unmatched markers remain literal so a malformed model response cannot make
    text disappear.  The renderer intentionally handles a bounded subset and
    leaves unsupported Markdown as plain text.
    """

    output: list[str] = []
    spans: list[dict[str, Any]] = []
    stack: list[tuple[str, tuple[str, ...], int]] = []
    position = 0

    def current_offset() -> int:
        return _utf16_len("".join(output))

    def append(value: str) -> None:
        output.append(value)

    while position < len(text):
        if text[position] == "\\" and position + 1 < len(text) and text[position + 1] in "*_`[]()":
            append(text[position + 1])
            position += 2
            continue

        link = _LINK_RE.match(text, position)
        if link:
            label = link.group(1)
            start = current_offset()
            append(label)
            spans.append(
                {
                    "type": "link",
                    "offset": start,
                    "length": _utf16_len(label),
                    "url": link.group(2),
                }
            )
            position = link.end()
            continue

        token = next((candidate for candidate in _TOKENS if text.startswith(candidate, position)), None)
        if token is None:
            append(text[position])
            position += 1
            continue

        styles = _styles_for_token(token)
        if token == "</u>":
            if stack and stack[-1][0] == token:
                _, active_styles, start = stack.pop()
                _append_spans(spans, active_styles, start, current_offset())
                position += len(token)
                continue
            append(token)
            position += len(token)
            continue

        matching = _matching_token(token)
        if stack and stack[-1][0] == token:
            _, active_styles, start = stack.pop()
            _append_spans(spans, active_styles, start, current_offset())
            position += len(token)
            continue
        if matching and text.find(matching, position + len(token)) >= 0:
            stack.append((token, styles, current_offset()))
            position += len(token)
            continue
        append(token)
        position += len(token)

    if stack:
        # This branch is only reachable for an unmatched HTML opening tag;
        # Markdown markers are checked for a closing token before opening.
        append("".join(item[0] for item in stack))

    if not spans:
        return "".join(output), None
    spans.sort(key=lambda item: (item["offset"], -item["length"], item["type"]))
    return "".join(output), {"version": 1, "items": spans}


def _styles_for_token(token: str) -> tuple[str, ...]:
    return {
        "***": ("bold", "italic"),
        "**": ("bold",),
        "*": ("italic",),
        "_": ("italic",),
        "`": ("code",),
        "<u>": ("underline",),
        "</u>": ("underline",),
    }[token]


def _matching_token(token: str) -> str | None:
    return {"<u>": "</u>"}.get(token, token)


def _append_spans(spans: list[dict[str, Any]], styles: tuple[str, ...], start: int, end: int) -> None:
    if end <= start:
        return
    for style in styles:
        spans.append({"type": style, "offset": start, "length": end - start})


def _utf16_len(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2
