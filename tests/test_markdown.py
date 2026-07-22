import pytest

from world_stage.messaging import message_preview
from world_stage.utils import get_markdown_parser


def render(value: str) -> str:
    return get_markdown_parser().renderInline(value)


def test_font_tag_combines_attributes_and_accepts_quoted_values():
    value = (
        "[font style='oblique' weight=semi-bold family=\"ui-monospace\" "
        "size=1 bg='#abc' fg=red][b]Formatted[/b][/font]"
    )

    assert render(value) == (
        '<span style="color: var(--red); background-color: #abc; '
        "font-size: 0.5rem; font-family: ui-monospace; font-weight: 600; "
        'font-style: oblique"><strong>Formatted</strong></span>'
    )


@pytest.mark.parametrize(
    ("value", "css_value"),
    [
        ("1", "0.5rem"),
        ("2", "xx-small"),
        ("3", "x-small"),
        ("4", "small"),
        ("5", "medium"),
        ("6", "large"),
        ("7", "x-large"),
        ("8", "xx-large"),
        ("9", "xxx-large"),
        ("xxx-small", "0.5rem"),
        ("xx-small", "xx-small"),
        ("x-small", "x-small"),
        ("small", "small"),
        ("medium", "medium"),
        ("large", "large"),
        ("x-large", "x-large"),
        ("xx-large", "xx-large"),
        ("xxx-large", "xxx-large"),
    ],
)
def test_font_sizes_are_absolute(value: str, css_value: str):
    assert render(f"[font size={value}]text[/font]") == (
        f'<span style="font-size: {css_value}">text</span>'
    )


def test_xxx_small_is_root_relative_when_font_tags_are_nested():
    assert render("[font size=9]outer [font size=xxx-small]inner[/font][/font]") == (
        '<span style="font-size: xxx-large">outer '
        '<span style="font-size: 0.5rem">inner</span></span>'
    )


@pytest.mark.parametrize(
    "family",
    [
        "serif",
        "sans-serif",
        "monospace",
        "cursive",
        "fantasy",
        "system-ui",
        "ui-serif",
        "ui-sans-serif",
        "ui-monospace",
        "ui-rounded",
        "math",
        "fangsong",
    ],
)
def test_font_families(family: str):
    assert render(f"[font family={family}]text[/font]") == (
        f'<span style="font-family: {family}">text</span>'
    )


@pytest.mark.parametrize(
    ("weight", "css_value"),
    [
        ("thin", "100"),
        ("extra-light", "100"),
        ("ultra-light", "200"),
        ("light", "300"),
        ("normal", "400"),
        ("regular", "400"),
        ("medium", "500"),
        ("semi-bold", "600"),
        ("demi-bold", "600"),
        ("bold", "700"),
        ("extra-bold", "800"),
        ("ultra-bold", "800"),
        ("black", "900"),
        ("heavy", "900"),
        ("extra-black", "950"),
        ("ultra-black", "950"),
        ("1", "1"),
        ("0375", "375"),
        ("1000", "1000"),
    ],
)
def test_font_weights(weight: str, css_value: str):
    assert render(f"[font weight={weight}]text[/font]") == (
        f'<span style="font-weight: {css_value}">text</span>'
    )


@pytest.mark.parametrize("style", ["normal", "italic", "oblique"])
def test_font_styles(style: str):
    assert render(f"[font style={style}]text[/font]") == (
        f'<span style="font-style: {style}">text</span>'
    )


def test_font_colours_support_named_and_hex_values_with_contrast():
    assert render("[font fg=ABC]text[/font]") == ('<span style="color: #abc">text</span>')
    assert render("[font fg=#12ABef]text[/font]") == ('<span style="color: #12abef">text</span>')
    assert render("[font bg=blue]text[/font]") == (
        '<span style="background-color: var(--blue); color: var(--on-blue)">text</span>'
    )
    assert render("[font bg=fff]text[/font]") == (
        '<span style="background-color: #fff; color: var(--black)">text</span>'
    )
    assert render("[font bg=#000000]text[/font]") == (
        '<span style="background-color: #000000; color: var(--white)">text</span>'
    )


def test_explicit_font_foreground_overrides_automatic_background_contrast():
    assert render("[font bg=fff fg=red]text[/font]") == (
        '<span style="color: var(--red); background-color: #fff">text</span>'
    )


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
def test_invalid_font_tags_are_rendered_as_text(value: str):
    rendered = render(value)

    assert '<span style="' not in rendered
    assert "onmouseover=" not in rendered or "&quot;" in rendered


def test_font_tags_are_removed_from_message_previews():
    assert (
        message_preview("[font fg=red size='6']Important[/font] [b]message[/b]")
        == "Important message"
    )
