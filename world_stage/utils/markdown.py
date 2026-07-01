import re
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline


def footnote_plugin(md: MarkdownIt):
    def tokenize_footnote(state: StateInline, silent: bool):
        src = state.src[state.pos :]
        match = re.match(r"\[(\d+)\]", src)
        if not match:
            return False

        if silent:
            return False

        number = match.group(1)
        state.pos += match.end()

        token = state.push("footnote_open", "a", 1)
        token.attrs = {"href": f"#footnote-{number}", "class": "footnote-link"}

        token = state.push("sup_open", "sup", 1)
        token = state.push("text", "", 0)
        token.content = f"[{number}]"
        token = state.push("sup_close", "sup", -1)

        token = state.push("footnote_close", "a", -1)

        return True

    md.inline.ruler.before("emphasis", "footnote", tokenize_footnote)

    def render_token(self, tokens, idx, options, env):
        token = tokens[idx]
        if token.type == "footnote_open":
            href = token.attrs["href"]
            cls = token.attrs["class"]
            return f'<a href="{href}" class="{cls}">'
        elif token.type == "footnote_close":
            return "</a>"
        return ""

    md.add_render_rule("footnote_open", render_token)
    md.add_render_rule("footnote_close", render_token)


def make_bbcode_plugin(allowed_colours):
    tags = {"b": "strong", "i": "em", "u": "ins", "s": "del", "sm": "small", "xl": "big"}

    c_re = re.compile(r"\[c=([a-zA-Z]+)\]")
    close_re = {
        "b": "[/b]",
        "i": "[/i]",
        "u": "[/u]",
        "s": "[/s]",
        "sm": "[/sm]",
        "xl": "[/xl]",
        "c": "[/c]",
    }

    def bbcode_plugin(md: MarkdownIt):

        def tokenizer(state: StateInline, silent: bool):
            src = state.src
            pos = state.pos

            for tag, html_tag in tags.items():
                open_tag = f"[{tag}]"
                close_tag = close_re[tag]
                if src.startswith(open_tag, pos):
                    end_pos = src.find(close_tag, pos + len(open_tag))
                    if end_pos == -1:
                        return False
                    if silent:
                        return True

                    old_max = state.posMax
                    state.pos = pos + len(open_tag)
                    state.posMax = end_pos

                    state.push(f"bb_{tag}_open", html_tag, 1)
                    state.md.inline.tokenize(state)
                    state.push(f"bb_{tag}_close", html_tag, -1)

                    state.pos = end_pos + len(close_tag)
                    state.posMax = old_max
                    return True

            m = c_re.match(src, pos)
            if m:
                colour = m.group(1)

                open_len = m.end()
                close_tag = close_re["c"]
                end_pos = src.find(close_tag, open_len)
                if end_pos == -1:
                    return False
                if silent:
                    return True

                old_max = state.posMax
                state.pos = open_len
                state.posMax = end_pos

                token = state.push("bb_colour_open", "span", 1)
                if colour in allowed_colours:
                    token.attrs = {"class": f"colour-{colour}"}
                state.md.inline.tokenize(state)
                state.push("bb_colour_close", "span", -1)

                state.pos = end_pos + len(close_tag)
                state.posMax = old_max
                return True

            return False

        md.inline.ruler.before("emphasis", "bbcode_all", tokenizer)

        def simple_open(tag):
            def render(self, tokens, idx, opts, env):
                return f"<{tag}>"

            return render

        def simple_close(tag):
            def render(self, tokens, idx, opts, env):
                return f"</{tag}>"

            return render

        for tag, html_tag in tags.items():
            md.add_render_rule(f"bb_{tag}_open", simple_open(html_tag))
            md.add_render_rule(f"bb_{tag}_close", simple_close(html_tag))

        # Register colour
        def render_colour_open(self, tokens, idx, opts, env):
            klass = tokens[idx].attrs["class"]
            return f'<span class="{klass}">'

        md.add_render_rule("bb_colour_open", render_colour_open)
        md.add_render_rule("bb_colour_close", simple_close("span"))

    return bbcode_plugin


def make_entity_plugin(entities=None):
    if entities is None:
        entities = {
            "nbsp": "\u00a0",
            "shy": "\u00ad",
            "tab": "\t",
            "amp": "&",
            "ensp": " ",
            "emsp": " ",
            "ndash": "–",
            "mdash": "—",
            "ellip": "…",
        }

    def entity_plugin(md: MarkdownIt):
        def tokenizer(state: StateInline, silent: bool):
            src = state.src
            pos = state.pos

            if not src.startswith("&", pos):
                return False

            semi = src.find(";", pos + 1)
            if semi == -1:
                return False

            name = src[pos + 1 : semi]
            if name not in entities:
                return False

            if silent:
                return True

            token = state.push("entity", "", 0)
            token.content = entities[name]

            state.pos = semi + 1
            return True

        md.inline.ruler.before("text", "entities", tokenizer)

        def render_entity(self, tokens, idx, opts, env):
            return tokens[idx].content

        md.add_render_rule("entity", render_entity)

    return entity_plugin


@lru_cache(maxsize=1)
def get_markdown_parser():
    colours = {"red", "green", "blue", "yellow", "magenta", "cyan"}
    md = (
        MarkdownIt("zero")
        .enable(["emphasis"])
        .use(footnote_plugin)
        .use(make_bbcode_plugin(colours))
        .use(make_entity_plugin())
    )
    return md
