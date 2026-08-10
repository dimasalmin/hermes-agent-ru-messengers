"""Smart message splitting respecting platform length limits.

Tries to break on paragraph, then line, then sentence, then whitespace.
Never splits inside a fenced code block when avoidable.
"""

from __future__ import annotations

from typing import List


FENCE = "```"


def split_message(text: str, max_len: int) -> List[str]:
    if max_len <= 0:
        raise ValueError("max_len must be positive")
    if len(text) <= max_len:
        return [text] if text else []

    chunks: List[str] = []
    remaining = text
    in_code = False
    code_lang = ""

    while len(remaining) > max_len:
        # Search for a clean break point near max_len.
        window = remaining[:max_len]
        cut = _best_break(window)
        chunk = remaining[:cut].rstrip()

        # Track unbalanced fences and re-open in the next chunk.
        fence_count = chunk.count(FENCE)
        next_in_code = in_code ^ (fence_count % 2 == 1)
        if in_code and fence_count % 2 == 1:
            # We closed a fence; nothing to carry.
            pass
        elif in_code:
            # Still inside the same fence вЂ” close it for this chunk, reopen next.
            chunk = chunk + "\n" + FENCE
        elif fence_count % 2 == 1:
            # We opened a fence and haven't closed it; close + reopen.
            # Best-effort: detect language hint from the line opening the fence.
            opening = chunk.rfind(FENCE)
            lang_line_end = chunk.find("\n", opening)
            code_lang = chunk[opening + len(FENCE):lang_line_end].strip() if lang_line_end != -1 else ""
            chunk = chunk + "\n" + FENCE

        chunks.append(chunk)

        carry_prefix = ""
        if next_in_code and (in_code and fence_count % 2 == 1 or not in_code and fence_count % 2 == 1):
            carry_prefix = FENCE + (code_lang or "") + "\n"
        remaining = carry_prefix + remaining[cut:].lstrip()
        in_code = next_in_code

    if remaining:
        chunks.append(remaining)
    return chunks


def _best_break(window: str) -> int:
    for sep in ("\n\n", "\n", ". ", "! ", "? ", " "):
        idx = window.rfind(sep)
        if idx > len(window) // 2:
            return idx + len(sep)
    return len(window)
