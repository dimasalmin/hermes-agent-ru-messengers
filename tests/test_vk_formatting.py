from __future__ import annotations

from plugins.vk.formatting import markdown_to_vk


def test_markdown_to_vk_preserves_plain_text_and_native_spans():
    text, format_data = markdown_to_vk("**Привет**, *мир* [ссылка](https://example.com)")
    assert text == "Привет, мир ссылка"
    assert format_data == {
        "version": 1,
        "items": [
            {"type": "bold", "offset": 0, "length": 6},
            {"type": "italic", "offset": 8, "length": 3},
            {"type": "link", "offset": 12, "length": 6, "url": "https://example.com"},
        ],
    }


def test_markdown_to_vk_handles_emoji_utf16_offsets_and_unmatched_markers():
    text, format_data = markdown_to_vk("🔥 **ok** *literal")
    assert text == "🔥 ok *literal"
    assert format_data == {"version": 1, "items": [{"type": "bold", "offset": 3, "length": 2}]}
