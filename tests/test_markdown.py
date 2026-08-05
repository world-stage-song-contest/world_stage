import pytest

from world_stage.messaging import message_preview
from world_stage.utils import get_markdown_parser


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
