from importlib.resources import files

from flask import url_for
from markdown_it import MarkdownIt
from markupsafe import Markup

GUIDES = {"wsql": "WSQL help", "search-json": "Search API help"}


def render_guide(document):
    source = files("world_stage").joinpath("docs", document + ".md").read_text(encoding="utf-8")
    parser = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = parser.parse(source)
    if tokens and tokens[0].type == "heading_open" and tokens[0].tag == "h1":
        tokens = tokens[3:]
    for token in tokens:
        if token.type in ("heading_open", "heading_close") and token.tag == "h1":
            token.tag = "h2"
        for child in token.children or []:
            if child.type == "link_open":
                for name in GUIDES:
                    if child.attrGet("href") == name + ".md":
                        child.attrSet("href", url_for("search.help_page", document=name))
    return Markup(parser.renderer.render(tokens, parser.options, {}))
