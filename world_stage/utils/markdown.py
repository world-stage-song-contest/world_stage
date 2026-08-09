import re
from functools import lru_cache
from html import escape

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline
from markdown_it.token import Token

BBCODE_COLOURS = frozenset({"red", "green", "blue", "yellow", "magenta", "cyan", "white", "black"})
LANGUAGE_TAG_PATTERN = r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*"
LYRICS_LANGUAGE_MARKER_RE = re.compile(
    rf"^\s*\{{lang=(?P<language>{LANGUAGE_TAG_PATTERN})?\}}[ \t]*"
)
URL_RE = re.compile(r"https?://[^\s<>'\"]+", re.I)
URL_TRAILING_PUNCTUATION = ".,;:!?"
URL_BRACKET_PAIRS = {")": "(", "]": "[", "}": "{"}

FONT_OPEN_TAG_RE = re.compile(
    r"""\[font(?P<attributes>(?:\s+[A-Za-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s\]]+))+\s*)\]"""
)
FONT_ATTRIBUTE_RE = re.compile(r"""\s+([A-Za-z]+)\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s\]]+))""")
FONT_SIZE_VALUES = {
    "xxx-small": "0.5rem",
    "xx-small": "xx-small",
    "x-small": "x-small",
    "small": "small",
    "medium": "medium",
    "large": "large",
    "x-large": "x-large",
    "xx-large": "xx-large",
    "xxx-large": "xxx-large",
}
FONT_SIZE_NUMBERS = {
    str(number): size for number, size in enumerate(FONT_SIZE_VALUES.values(), start=1)
}
FONT_FAMILIES = frozenset(
    {
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
    }
)
FONT_WEIGHT_ALIASES = {
    "thin": "100",
    "extra-light": "100",
    "ultra-light": "200",
    "light": "300",
    "normal": "400",
    "regular": "400",
    "medium": "500",
    "semi-bold": "600",
    "demi-bold": "600",
    "bold": "700",
    "extra-bold": "800",
    "ultra-bold": "800",
    "black": "900",
    "heavy": "900",
    "extra-black": "950",
    "ultra-black": "950",
}
FONT_STYLES = frozenset({"normal", "italic", "oblique"})


def _trim_url_trailing_punctuation(value: str) -> str:
    value = value.rstrip(URL_TRAILING_PUNCTUATION)
    changed = True
    while changed and value:
        changed = False
        if (opener := URL_BRACKET_PAIRS.get(value[-1])) and value.count(
            value[-1]
        ) > value.count(opener):
            value = value[:-1].rstrip(URL_TRAILING_PUNCTUATION)
            changed = True
    return value


def autolink_plugin(md: MarkdownIt):
    """Turn bare HTTP(S) URLs in rendered inline text into safe links."""

    def autolink_text(state):
        for block_token in state.tokens:
            if block_token.type != "inline" or not block_token.children:
                continue

            children = []
            literal_depth = 0
            for token in block_token.children:
                if token.type in {"bb_code_open", "bb_pre_open"}:
                    literal_depth += 1

                if token.type != "text" or literal_depth:
                    children.append(token)
                else:
                    last = 0
                    for match in URL_RE.finditer(token.content):
                        url = _trim_url_trailing_punctuation(match.group(0))
                        if not url:
                            continue
                        if match.start() > last:
                            text_token = Token("text", "", 0)
                            text_token.content = token.content[last : match.start()]
                            children.append(text_token)

                        open_token = Token("link_open", "a", 1)
                        open_token.attrs = {"href": url, "rel": "noopener"}
                        children.append(open_token)
                        link_text = Token("text", "", 0)
                        link_text.content = url
                        children.append(link_text)
                        children.append(Token("link_close", "a", -1))
                        last = match.start() + len(url)

                    if last:
                        if last < len(token.content):
                            text_token = Token("text", "", 0)
                            text_token.content = token.content[last:]
                            children.append(text_token)
                    else:
                        children.append(token)

                if token.type in {"bb_code_close", "bb_pre_close"}:
                    literal_depth -= 1

            block_token.children = children

    md.core.ruler.after("inline", "autolink_text", autolink_text)
    return md


def _normalise_font_colour(value: str, allowed_colours: set[str]) -> str | None:
    value = value.lower()
    if value in allowed_colours:
        return value

    match = re.fullmatch(r"#?([0-9a-f]{3}|[0-9a-f]{6})", value)
    if match:
        return f"#{match.group(1)}"
    return None


def _normalise_font_attributes(
    attributes_text: str, allowed_colours: set[str]
) -> dict[str, str] | None:
    attributes = {}
    pos = 0
    while pos < len(attributes_text):
        match = FONT_ATTRIBUTE_RE.match(attributes_text, pos)
        if not match:
            if attributes_text[pos:].isspace():
                break
            return None

        name = match.group(1).lower()
        value = next(group for group in match.groups()[1:] if group is not None).lower()
        if name in attributes or name not in {"fg", "bg", "size", "family", "weight", "style"}:
            return None

        if name in {"fg", "bg"}:
            normalised = _normalise_font_colour(value, allowed_colours)
        elif name == "size":
            normalised = FONT_SIZE_NUMBERS.get(value, FONT_SIZE_VALUES.get(value))
        elif name == "family":
            normalised = value if value in FONT_FAMILIES else None
        elif name == "weight":
            if value in FONT_WEIGHT_ALIASES:
                normalised = FONT_WEIGHT_ALIASES[value]
            elif value.isascii() and value.isdecimal() and 1 <= int(value) <= 1000:
                normalised = str(int(value))
            else:
                normalised = None
        else:
            normalised = value if value in FONT_STYLES else None

        if normalised is None:
            return None
        attributes[name] = normalised
        pos = match.end()

    return attributes or None


def _parse_font_open_tag(
    src: str, pos: int, allowed_colours: set[str], limit: int | None = None
) -> tuple[int, dict[str, str]] | None:
    match = FONT_OPEN_TAG_RE.match(src, pos, len(src) if limit is None else limit)
    if not match:
        return None
    attributes = _normalise_font_attributes(match.group("attributes"), allowed_colours)
    if attributes is None:
        return None
    return match.end(), attributes


def _hex_relative_luminance(value: str) -> float:
    digits = value.removeprefix("#")
    if len(digits) == 3:
        digits = "".join(character * 2 for character in digits)

    channels = [int(digits[index : index + 2], 16) / 255 for index in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _font_colour_css(value: str) -> str:
    return value if value.startswith("#") else f"var(--{value})"


def _font_background_foreground(value: str) -> str:
    if not value.startswith("#"):
        return f"var(--on-{value})"

    luminance = _hex_relative_luminance(value)
    black_contrast = (luminance + 0.05) / 0.05
    white_contrast = 1.05 / (luminance + 0.05)
    return "var(--black)" if black_contrast >= white_contrast else "var(--white)"


def _font_style(attributes: dict[str, str]) -> str:
    declarations = []
    if foreground := attributes.get("fg"):
        declarations.append(("color", _font_colour_css(foreground)))
    if background := attributes.get("bg"):
        declarations.append(("background-color", _font_colour_css(background)))
        if "fg" not in attributes:
            declarations.append(("color", _font_background_foreground(background)))
    if size := attributes.get("size"):
        declarations.append(("font-size", size))
    if family := attributes.get("family"):
        declarations.append(("font-family", family))
    if weight := attributes.get("weight"):
        declarations.append(("font-weight", weight))
    if style := attributes.get("style"):
        declarations.append(("font-style", style))
    return "; ".join(f"{name}: {value}" for name, value in declarations)


def _find_font_close(src: str, start: int, allowed_colours: set[str], limit: int) -> int | None:
    close_tag = "[/font]"
    depth = 1
    pos = start
    while pos < limit:
        next_open = src.find("[font", pos, limit)
        next_close = src.find(close_tag, pos, limit)
        if next_close == -1:
            return None
        if next_open != -1 and next_open < next_close:
            parsed = _parse_font_open_tag(src, next_open, allowed_colours, limit)
            if parsed:
                depth += 1
                pos = parsed[0]
            else:
                pos = next_open + len("[font")
            continue

        depth -= 1
        if depth == 0:
            return next_close
        pos = next_close + len(close_tag)
    return None


def strip_font_tags(value: str, allowed_colours: set[str] = BBCODE_COLOURS) -> str:
    """Remove valid font tags from a plain-text formatting preview."""
    result = []
    pos = 0
    close_tag = "[/font]"
    while pos < len(value):
        if value.startswith(close_tag, pos):
            pos += len(close_tag)
            continue
        parsed = _parse_font_open_tag(value, pos, allowed_colours)
        if parsed:
            pos = parsed[0]
            continue
        result.append(value[pos])
        pos += 1
    return "".join(result)


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
    allowed_colours = set(allowed_colours)
    tags = {
        "b": "strong",
        "i": "em",
        "u": "ins",
        "s": "del",
        "o": "span",
        "sm": "small",
        "xl": "big",
    }
    literal_tags = {"code": "code", "pre": "pre"}

    colour_names = "|".join(re.escape(colour) for colour in sorted(allowed_colours))
    c_re = re.compile(rf"\[c=({colour_names})\]")
    bg_re = re.compile(rf"\[bg=({colour_names})\]")
    lang_re = re.compile(rf"\[lang=({LANGUAGE_TAG_PATTERN})\]")
    close_re = {
        "b": "[/b]",
        "i": "[/i]",
        "u": "[/u]",
        "s": "[/s]",
        "o": "[/o]",
        "sm": "[/sm]",
        "xl": "[/xl]",
        "code": "[/code]",
        "pre": "[/pre]",
        "c": "[/c]",
        "bg": "[/bg]",
        "font": "[/font]",
        "lang": "[/lang]",
    }

    def bbcode_plugin(md: MarkdownIt):

        def tokenizer(state: StateInline, silent: bool):
            src = state.src
            pos = state.pos

            for tag, html_tag in literal_tags.items():
                open_tag = f"[{tag}]"
                close_tag = close_re[tag]
                if src.startswith(open_tag, pos):
                    end_pos = src.find(close_tag, pos + len(open_tag))
                    if end_pos == -1:
                        return False
                    if silent:
                        return True

                    state.push(f"bb_{tag}_open", html_tag, 1)
                    token = state.push("text", "", 0)
                    token.content = src[pos + len(open_tag) : end_pos]
                    state.push(f"bb_{tag}_close", html_tag, -1)
                    state.pos = end_pos + len(close_tag)
                    return True

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
                token.attrs = {"class": f"colour-{colour}"}
                state.md.inline.tokenize(state)
                state.push("bb_colour_close", "span", -1)

                state.pos = end_pos + len(close_tag)
                state.posMax = old_max
                return True

            m = bg_re.match(src, pos)
            if m:
                colour = m.group(1)

                open_len = m.end()
                close_tag = close_re["bg"]
                end_pos = src.find(close_tag, open_len)
                if end_pos == -1:
                    return False
                if silent:
                    return True

                old_max = state.posMax
                state.pos = open_len
                state.posMax = end_pos

                token = state.push("bb_background_colour_open", "span", 1)
                token.attrs = {"class": f"background-colour-{colour}"}
                state.md.inline.tokenize(state)
                state.push("bb_background_colour_close", "span", -1)

                state.pos = end_pos + len(close_tag)
                state.posMax = old_max
                return True

            m = lang_re.match(src, pos)
            if m:
                language_tag = m.group(1)

                open_len = m.end()
                close_tag = close_re["lang"]
                end_pos = src.find(close_tag, open_len, state.posMax)
                if end_pos == -1:
                    return False
                if silent:
                    return True

                old_max = state.posMax
                state.pos = open_len
                state.posMax = end_pos

                token = state.push("bb_lang_open", "span", 1)
                token.attrs = {"lang": language_tag}
                state.md.inline.tokenize(state)
                state.push("bb_lang_close", "span", -1)

                state.pos = end_pos + len(close_tag)
                state.posMax = old_max
                return True

            parsed_font = _parse_font_open_tag(src, pos, allowed_colours, state.posMax)
            if parsed_font:
                open_end, attributes = parsed_font
                close_tag = close_re["font"]
                end_pos = _find_font_close(src, open_end, allowed_colours, state.posMax)
                if end_pos is None:
                    return False
                if silent:
                    return True

                old_max = state.posMax
                state.pos = open_end
                state.posMax = end_pos

                token = state.push("bb_font_open", "span", 1)
                token.attrs = {"style": _font_style(attributes)}
                state.md.inline.tokenize(state)
                state.push("bb_font_close", "span", -1)

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
            if tag == "o":
                md.add_render_rule(
                    "bb_o_open",
                    lambda self, tokens, idx, opts, env: '<span class="overline">',
                )
            else:
                md.add_render_rule(f"bb_{tag}_open", simple_open(html_tag))
            md.add_render_rule(f"bb_{tag}_close", simple_close(html_tag))
        for tag, html_tag in literal_tags.items():
            md.add_render_rule(f"bb_{tag}_open", simple_open(html_tag))
            md.add_render_rule(f"bb_{tag}_close", simple_close(html_tag))

        # Register colour
        def render_colour_open(self, tokens, idx, opts, env):
            klass = tokens[idx].attrs["class"]
            return f'<span class="{klass}">'

        md.add_render_rule("bb_colour_open", render_colour_open)
        md.add_render_rule("bb_colour_close", simple_close("span"))
        md.add_render_rule("bb_background_colour_open", render_colour_open)
        md.add_render_rule("bb_background_colour_close", simple_close("span"))

        def render_lang_open(self, tokens, idx, opts, env):
            language_tag = escape(tokens[idx].attrs["lang"], quote=True)
            return f'<span lang="{language_tag}">'

        md.add_render_rule("bb_lang_open", render_lang_open)
        md.add_render_rule("bb_lang_close", simple_close("span"))

        def render_font_open(self, tokens, idx, opts, env):
            style = escape(tokens[idx].attrs["style"], quote=True)
            return f'<span style="{style}">'

        md.add_render_rule("bb_font_open", render_font_open)
        md.add_render_rule("bb_font_close", simple_close("span"))

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


@lru_cache(maxsize=2)
def get_markdown_parser(*, autolink: bool = False):
    md = (
        MarkdownIt("zero")
        .enable(["emphasis"])
        .use(footnote_plugin)
        .use(make_bbcode_plugin(BBCODE_COLOURS))
        .use(make_entity_plugin())
    )
    if autolink:
        md.use(autolink_plugin)
    return md


def render_lyrics(value: str) -> list[dict[str, str | None]]:
    """Render lyrics line by line, applying passage-level language markers."""
    md = get_markdown_parser()
    rendered_lines: list[dict[str, str | None]] = []
    active_language: str | None = None

    for raw_line in value.split("\n"):
        line = raw_line.removesuffix("\r")

        if not line.strip():
            active_language = None
            rendered_lines.append({"html": "", "lang": None})
            continue

        marker = LYRICS_LANGUAGE_MARKER_RE.match(line)
        if marker:
            active_language = marker.group("language")
            line = line[marker.end() :]
            if not line:
                continue

        rendered_lines.append({"html": md.renderInline(line), "lang": active_language})

    return rendered_lines
