import pytest

from world_stage.messaging import message_preview
from world_stage.utils import get_markdown_parser, render_lyrics


def render(value: str) -> str:
    return get_markdown_parser().renderInline(value)


@pytest.mark.parametrize(
    "value",
    [
        "[font]text[/font]",
        "[font fg=orange]text[/font]",
        "[font fg=#12]text[/font]",
        "[font fg=#1234]text[/font]",
        "[font size=0]text[/font]",
        "[font size=10]text[/font]",
        "[font family=Arial]text[/font]",
        "[font weight=0]text[/font]",
        "[font weight=1001]text[/font]",
        "[font style=slanted]text[/font]",
        "[font unknown=red]text[/font]",
        "[font fg=red fg=blue]text[/font]",
        "[font fg='red]text[/font]",
        "[font fg=red]text",
        '[font fg="red" onmouseover="alert(1)"]text[/font]',
    ],
)
def test_invalid_font_tags_cannot_inject_attributes(value: str):
    rendered = render(value)

    assert "[font" in rendered
    assert "text" in rendered
    assert "onmouseover=" not in rendered or "&quot;" in rendered


def test_font_tags_are_removed_from_message_previews():
    assert (
        message_preview("[font fg=red size='6']Important[/font] [b]message[/b]")
        == "Important message"
    )


@pytest.mark.parametrize("language_tag", ["pl", "en-US", "sr-Latn", "zh-Hant-TW"])
def test_lang_tag(language_tag: str):
    assert (
        render(f"[lang={language_tag}]formatted [b]text[/b][/lang]")
        == f'<span lang="{language_tag}">formatted <strong>text</strong></span>'
    )


@pytest.mark.parametrize(
    "value",
    [
        '[lang=en" onmouseover="alert(1)]text[/lang]',
        "[lang=en US]text[/lang]",
        "[lang=]text[/lang]",
        "[lang=en]text",
    ],
)
def test_invalid_lang_tags_cannot_inject_attributes(value: str):
    rendered = render(value)

    assert "[lang" in rendered
    assert "<span lang=" not in rendered


def test_lyrics_language_markers_apply_until_blank_line_or_override():
    assert render_lyrics(
        "{lang=es}\nHola\n[b]mundo[/b]\n{lang=fr}Bonjour\nEncore\n\nDefault"
    ) == [
        {"html": "Hola", "lang": "es"},
        {"html": "<strong>mundo</strong>", "lang": "es"},
        {"html": "Bonjour", "lang": "fr"},
        {"html": "Encore", "lang": "fr"},
        {"html": "", "lang": None},
        {"html": "Default", "lang": None},
    ]


def test_lyrics_language_marker_can_be_reset_explicitly():
    assert render_lyrics("{lang=es}Hola\n{lang=}Default") == [
        {"html": "Hola", "lang": "es"},
        {"html": "Default", "lang": None},
    ]


def test_invalid_lyrics_language_marker_is_rendered_as_text():
    lines = render_lyrics('{lang=en" onmouseover="alert(1)}Text')

    assert lines == [
        {
            "html": "{lang=en&quot; onmouseover=&quot;alert(1)}Text",
            "lang": None,
        }
    ]
