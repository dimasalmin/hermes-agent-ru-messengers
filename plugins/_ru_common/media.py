"""Extract MEDIA:/path tags from outgoing agent text.

Hermes Agent emits ``MEDIA:/abs/path/file.ext`` markers when it wants the
platform adapter to attach a local file alongside (or instead of) text. The
helper reuses :py:meth:`BasePlatformAdapter.extract_media` when available, but
we provide a self-contained fallback so unit tests can run without importing
Hermes core.
"""

from __future__ import annotations

import re
from typing import List, Tuple

MEDIA_TAG_RE = re.compile(r"MEDIA:(\S+)")


def extract_media_tags(content: str) -> Tuple[List[str], str]:
    """Return ``(paths, cleaned_text)``.

    ``paths`` preserves order of appearance. ``cleaned_text`` is the original
    string with the tags stripped (and resulting double-blanks collapsed).
    """
    paths = MEDIA_TAG_RE.findall(content)
    if not paths:
        return [], content
    cleaned = MEDIA_TAG_RE.sub("", content)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return paths, cleaned
