from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import NoReturn

from .query import (
    ORDERED,
    Comparison,
    Negation,
    Order,
    Query,
    QueryError,
    Value,
    decode_query,
)

SYMBOLS = {
    "=": "equals",
    "<": "less_than",
    "<=": "less_than_or_equal",
    ">": "greater_than",
    ">=": "greater_than_or_equal",
    "!=": "equals",
    "<>": "equals",
}
WORDS = {
    "CONTAINS": "contains",
    "LIKE": "like",
    "ILIKE": "like",
    "PHRASE": "phrase",
    "FUZZY": "fuzzy",
    "IN": "in",
}
SPELLINGS = {value: key for key, value in SYMBOLS.items() if key not in ("!=", "<>")}
SPELLINGS.update({value: key for key, value in WORDS.items() if key != "ILIKE"})
SPELLINGS.update(starts_with="STARTS WITH", ends_with="ENDS WITH")
ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "\\": "\\", "'": "'", '"': '"'}
ESCAPES.update({"[": "[", "]": "]", "=": "="})
MAX_DELIMITER_EQUALS = 64
LONG_STRING_OPEN = re.compile(r"\[(=*)\[")
BLOCK_COMMENT_OPEN = re.compile(r"--\[(=*)\[")


class WsqlError(QueryError):
    def __init__(self, message, source, offset, *, end=None, path="", code="invalid_wsql"):
        super().__init__(message, path, code)
        lines = re.split(r"\r\n?|\n", source[:offset])
        self.location = {
            "offset": offset,
            "length": max(0, (end if end is not None else offset + 1) - offset),
            "line": len(lines),
            "column": len(lines[-1]) + 1,
        }


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    start: int
    end: int


class Lexer:
    def __init__(self, source):
        self.source = source
        self.position = 0
        self.tokens = 0

    def error(self, message, start) -> NoReturn:
        raise WsqlError(message, self.source, start, end=self.position)

    def unicode_escape(self, start, digits):
        if digits == 4 and self.source[self.position : self.position + 1] == "{":
            match = re.match(r"\{([0-9a-fA-F]{1,6})\}", self.source[self.position :])
            if not match:
                self.error("Expected 1–6 hexadecimal digits inside Unicode braces", start)
            self.position += len(match[0])
            value = int(match[1], 16)
        else:
            value_text = self.source[self.position : self.position + digits]
            if not re.fullmatch(r"[0-9a-fA-F]{" + str(digits) + "}", value_text):
                self.error(f"Expected {digits} hexadecimal digits in Unicode escape", start)
            self.position += digits
            value = int(value_text, 16)
        if value > 0x10FFFF:
            self.error("Unicode escape is outside the Unicode range", start)
        return chr(value)

    def string(self):
        start = self.position
        multiline = self.source[start] == "["
        if multiline:
            opening = LONG_STRING_OPEN.match(self.source, start)
            if opening is None:
                self.error("Expected [[ or [=[ to start a long string", start)
            self.position = opening.end()
            self.check_delimiter(opening[1], start)
            closing = "]" + opening[1] + "]"
        else:
            closing = self.source[start]
            self.position += 1
        chars = []
        while self.position < len(self.source):
            if self.source.startswith(closing, self.position):
                self.position += len(closing)
                try:
                    value = "".join(chars).encode("utf-16", "surrogatepass").decode("utf-16")
                except UnicodeError:
                    self.error("String contains an unpaired Unicode surrogate", start)
                if "\0" in value:
                    self.error("Strings cannot contain NUL", start)
                return Token("string", value, start, self.position)
            char = self.source[self.position]
            self.position += 1
            if not multiline and char in "\r\n":
                self.error("Use a long string or an escape for a line break", self.position - 1)
            if char == "\\":
                escape_start = self.position - 1
                if self.position == len(self.source):
                    self.error("Unfinished string escape", escape_start)
                char = self.source[self.position]
                self.position += 1
                if char in ("u", "U"):
                    char = self.unicode_escape(escape_start, 4 if char == "u" else 8)
                elif char in ESCAPES:
                    char = ESCAPES[char]
                else:
                    self.error(
                        "Unknown string escape; escape a literal backslash with \\\\", escape_start
                    )
            chars.append(char)
        self.error("Unterminated string", start)

    def check_delimiter(self, equals, start):
        if len(equals) > MAX_DELIMITER_EQUALS:
            raise WsqlError(
                "String and comment markers allow at most 64 equals signs",
                self.source,
                start,
                end=self.position,
                code="complexity",
            )

    def skip_comment(self):
        start = self.position
        opening = BLOCK_COMMENT_OPEN.match(self.source, start)
        if opening is None:
            while self.position < len(self.source) and self.source[self.position] not in "\r\n":
                self.position += 1
            return
        self.position = opening.end()
        self.check_delimiter(opening[1], start)
        closing = "--]" + opening[1] + "]"
        end = self.source.find(closing, self.position)
        if end == -1:
            self.error(f"Unterminated block comment; expected {closing}", start)
        self.position = end + len(closing)

    def next(self):
        source = self.source
        self.tokens += 1
        if self.tokens > 4096:
            raise WsqlError("WSQL exceeds 4096 tokens", source, self.position, code="complexity")
        while self.position < len(source):
            if source[self.position].isspace():
                self.position += 1
            elif source.startswith("--", self.position):
                self.skip_comment()
            else:
                break
        start = self.position
        if start == len(source):
            return Token("end", "", start, start)
        char = source[start]
        if char in "\"'[":
            return self.string()
        if char == "@":
            self.position += 1
            while self.position < len(source) and source[self.position] not in " \t\r\n(),<>=!":
                if source[self.position].isspace() or source.startswith("--", self.position):
                    break
                self.position += 1
            return Token("temporal", source[start + 1 : self.position], start, self.position)
        for pattern, kind in (
            (r"\$[A-Za-z_][A-Za-z_0-9]*", "placeholder"),
            (r"[A-Za-z_][A-Za-z_0-9]*", "word"),
            (r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", "number"),
            (r"!=|<>|<=|>=|[=<>(),]", "symbol"),
        ):
            match = re.match(pattern, source[start:])
            if match:
                self.position += len(match[0])
                return Token(kind, match[0], start, self.position)
        self.position += 1
        self.error("Unexpected character", start)


class Parser:
    def __init__(self, source):
        self.source = source
        self.lexer = Lexer(source)
        self.token = self.lexer.next()
        self.locations = {}

    def error(self, message, code="invalid_wsql") -> NoReturn:
        raise WsqlError(message, self.source, self.token.start, end=self.token.end, code=code)

    def advance(self):
        previous = self.token
        self.token = self.lexer.next()
        return previous

    def at(self, value):
        return self.token.kind in ("symbol", "word") and self.token.value.upper() == value

    def accept(self, value):
        if self.at(value):
            self.advance()
            return True
        return False

    def expect(self, value):
        if not self.accept(value):
            self.error(f"Expected {value}")

    def locate(self, node, start):
        self.locations[id(node)] = (start, self.token.start)
        return node

    def value(self):
        token = self.token
        if token.kind == "placeholder":
            self.advance()
            node = {"kind": "placeholder", "name": token.value[1:]}
        elif token.kind == "string":
            self.advance()
            node = {"kind": "string", "value": token.value}
        elif token.kind == "number":
            self.advance()
            try:
                number = (
                    float(token.value)
                    if any(c in token.value.lower() for c in ".e")
                    else int(token.value)
                )
            except ValueError:
                self.error("Invalid number")
            node = {"kind": "number", "value": number}
        elif token.kind == "temporal":
            self.advance()
            kind = "datetime" if "T" in token.value else "time" if ":" in token.value else "date"
            node = {"kind": kind, "value": token.value}
        elif token.kind == "word":
            self.advance()
            word = token.value.lower()
            if word == "null":
                node = {"kind": "null"}
            elif word in ("true", "false"):
                node = {"kind": "boolean", "value": word == "true"}
            elif word in ("now", "today", "tomorrow", "yesterday"):
                node = {"kind": "temporal_sentinel", "name": word}
            else:
                node = {"kind": "field", "name": word}
        else:
            self.error("Expected a field or typed literal")
        return self.locate(node, token.start)

    def modifiers(self):
        result = {}
        if not self.accept("WITH"):
            return result
        while True:
            if self.accept("NULLS"):
                key = "nulls"
                if self.accept("DISTINCT"):
                    value = "distinct"
                else:
                    self.expect("AS")
                    self.expect("EMPTY")
                    value = "as_empty"
            else:
                negative = self.accept("NO")
                if not (self.at("CASE") or self.at("ACCENT")):
                    self.error("Expected CASE, ACCENT, or NULLS modifier")
                key = self.advance().value.lower()
                value = "insensitive" if negative else "sensitive"
            if key in result:
                self.error(f"Repeated {key} modifier")
            result[key] = value
            if not self.accept(","):
                return result

    def comparison(self):
        start = self.token.start
        left = self.value()
        negative = False
        defaults = {}
        if self.accept("IS"):
            negative = self.accept("NOT")
            if self.accept("EMPTY"):
                defaults["nulls"] = "as_empty"
            else:
                self.expect("NULL")
            right = {"kind": "null"}
            operator = "equals"
            chain_family = None
        else:
            negative = self.accept("NOT")
            token = self.advance()
            spelling = token.value.upper()
            chain_family = None
            if token.kind == "symbol" and spelling in SYMBOLS:
                if negative:
                    self.error("Use != for inequality or NOT (comparison) for ordered comparisons")
                operator = SYMBOLS[spelling]
                negative = spelling in ("!=", "<>")
                if not negative:
                    chain_family = "equality" if operator == "equals" else "ordered"
            elif token.kind == "word" and spelling in ("STARTS", "ENDS"):
                self.expect("WITH")
                operator = "starts_with" if spelling == "STARTS" else "ends_with"
            elif token.kind == "word" and spelling in WORDS:
                operator = WORDS[spelling]
                if spelling == "ILIKE":
                    defaults["case"] = "insensitive"
            else:
                raise WsqlError(
                    "Expected a comparison operator", self.source, token.start, end=token.end
                )
            if operator == "in":
                self.expect("(")
                items = [self.value()]
                while self.accept(","):
                    if len(items) >= 100:
                        self.error("IN requires at most 100 values", "complexity")
                    items.append(self.value())
                self.expect(")")
                right = {"kind": "list", "value": items}
            else:
                right = self.value()
        nodes = [{"kind": "comparison", "operator": operator, "left": left, "right": right}]
        while self.token.kind == "symbol" and self.token.value in SYMBOLS:
            spelling = self.token.value
            next_operator = SYMBOLS[spelling]
            if (
                chain_family is None
                or spelling in ("!=", "<>")
                or (chain_family == "equality") != (next_operator == "equals")
            ):
                self.error("Chain only equality operators or only ordered comparisons")
            if len(nodes) >= 99:
                self.error("Comparison chain is too long", "complexity")
            self.advance()
            left, right = right, self.value()
            nodes.append(
                {"kind": "comparison", "operator": next_operator, "left": left, "right": right}
            )
        modifiers = defaults | self.modifiers()
        for node in nodes:
            node["modifiers"] = modifiers
            self.locate(node, start)
        expression = nodes[0] if len(nodes) == 1 else {"kind": "and", "operands": nodes}
        if negative:
            expression = {"kind": "not", "operand": expression}
        return self.locate(expression, start)

    def unary(self, depth):
        if depth > 64:
            self.error("WSQL exceeds 64 syntax nesting levels", "complexity")
        start = self.token.start
        if self.accept("NOT"):
            return self.locate({"kind": "not", "operand": self.unary(depth + 1)}, start)
        if self.accept("("):
            result = self.expression(depth + 1)
            self.expect(")")
            return result
        return self.comparison()

    def conjunction(self, depth):
        nodes = [self.unary(depth)]
        while self.accept("AND"):
            nodes.append(self.unary(depth))
            if len(nodes) > 100:
                self.error("Too many expressions", "complexity")
        return nodes[0] if len(nodes) == 1 else {"kind": "and", "operands": nodes}

    def expression(self, depth=0):
        nodes = [self.conjunction(depth)]
        while self.accept("OR"):
            nodes.append(self.conjunction(depth))
            if len(nodes) > 100:
                self.error("Too many expressions", "complexity")
        return nodes[0] if len(nodes) == 1 else {"kind": "or", "operands": nodes}

    def document(self):
        self.accept("WHERE")
        document = {"version": 1, "where": self.expression()}
        if self.accept("ORDER"):
            self.expect("BY")
            order = []
            while True:
                field = self.value()
                default = "descending" if field.get("name") == "relevance" else "ascending"
                if self.accept("DESC"):
                    direction = "descending"
                elif self.accept("ASC"):
                    direction = "ascending"
                else:
                    direction = default
                order.append({"expression": field, "direction": direction})
                if not self.accept(","):
                    break
                if len(order) >= 5:
                    self.error("Specify at most five sort fields", "complexity")
            document["orderBy"] = order
        if self.token.kind != "end":
            self.error("Expected AND, OR, ORDER BY, or end of query")
        return document

    def parse(self):
        document = self.document()
        try:
            return decode_query(document)
        except QueryError as exc:
            current = document
            span = (0, len(self.source))
            for part in exc.path.strip("/").split("/"):
                if id(current) in self.locations:
                    span = self.locations[id(current)]
                if isinstance(current, dict) and part in current:
                    current = current[part]
                elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
                    current = current[int(part)]
                else:
                    break
            span = self.locations.get(id(current), span)
            raise WsqlError(
                str(exc), self.source, span[0], end=span[1], path=exc.path, code=exc.code
            ) from exc


def parse_wsql(source: str) -> Query:
    if not isinstance(source, str):
        raise QueryError("WSQL must be a string")
    if len(source) > 65536:
        raise QueryError("WSQL must be at most 65536 characters", code="complexity")
    return Parser(source).parse()


def quote_string(value: str) -> str:
    if "\n" in value or "\r" in value or ('"' in value and "'" in value):
        body = escape_string(value, multiline=True)
        for level in range(MAX_DELIMITER_EQUALS + 1):
            equals = "=" * level
            closing = "]" + equals + "]"
            if (body + closing).find(closing) == len(body):
                return "[" + equals + "[" + body + closing
    quote = "'" if '"' in value and "'" not in value else '"'
    return quote + escape_string(value, quote=quote) + quote


def escape_string(value: str, *, quote="", multiline=False) -> str:
    parts = []
    for char in value:
        if char in (quote, "\\"):
            parts.append("\\" + char)
        elif char.isprintable() or (multiline and char in "\r\n"):
            parts.append(char)
        else:
            parts.append(json.dumps(char, ensure_ascii=True)[1:-1])
    return "".join(parts)


def format_value(value: Value) -> str:
    if value.kind == "field":
        return value.value
    if value.kind == "null":
        return "NULL"
    if value.kind in ("string", "pattern"):
        return quote_string(value.value)
    if value.kind in ("date", "time", "datetime"):
        return "@" + value.value
    if value.kind == "temporal_sentinel":
        return value.value.upper()
    if value.kind == "list":
        return "(" + ", ".join(format_value(item) for item in value.value) + ")"
    return json.dumps(value.value).upper()


def modifier_text(node: Comparison) -> str:
    modifiers = []
    if node.type == "text":
        if node.options.case == "sensitive" and node.operator != "like":
            modifiers.append("CASE")
        if node.options.accent == "sensitive":
            modifiers.append("ACCENT")
    if node.options.nulls == "as_empty" and not (
        node.operator == "equals" and node.left.kind == "field" and node.right.kind == "null"
    ):
        modifiers.append("NULLS AS EMPTY")
    return " WITH " + ", ".join(modifiers) if modifiers else ""


def comparison_text(node: Comparison, negative=False) -> str:
    left, right = format_value(node.left), format_value(node.right)
    if node.operator == "equals" and node.left.kind == "field" and node.right.kind == "null":
        operator = "IS NOT" if negative else "IS"
        right = "EMPTY" if node.options.nulls == "as_empty" else "NULL"
    else:
        operator = SPELLINGS[node.operator]
        if node.operator == "like" and node.options.case == "insensitive":
            operator = "ILIKE"
        if negative:
            if node.operator == "equals":
                operator = "!="
            elif node.operator in ORDERED:
                return "NOT (" + comparison_text(node) + ")"
            else:
                operator = "NOT " + operator
    return f"{left} {operator} {right}" + modifier_text(node)


def can_chain(first, second):
    return (
        isinstance(first, Comparison)
        and isinstance(second, Comparison)
        and first.type == second.type
        and first.options == second.options
        and first.right == second.left
        and (
            first.operator == second.operator == "equals"
            or first.operator in ORDERED
            and second.operator in ORDERED
        )
        and first.left.kind != "null"
        and first.right.kind != "null"
        and second.right.kind != "null"
    )


def format_expression(node, precedence=0):
    if isinstance(node, Comparison):
        return comparison_text(node)
    if isinstance(node, Negation):
        if isinstance(node.operand, Comparison):
            return comparison_text(node.operand, negative=True)
        return "NOT (" + format_expression(node.operand) + ")"
    level = 2 if node.kind == "and" else 1
    parts = []
    index = 0
    while index < len(node.operands):
        child = node.operands[index]
        chain = [child]
        index += 1
        if node.kind == "and":
            while index < len(node.operands) and can_chain(chain[-1], node.operands[index]):
                chain.append(node.operands[index])
                index += 1
        if len(chain) > 1:
            text = format_value(child.left)
            for link in chain:
                text += " " + SPELLINGS[link.operator] + " " + format_value(link.right)
            text += modifier_text(child)
        else:
            text = format_expression(child, level)
        parts.append(text)
    text = (" AND " if node.kind == "and" else " OR ").join(parts)
    return "(" + text + ")" if level < precedence else text


def format_wsql(query: Query) -> str:
    text = format_expression(query.where)
    if query.order != (Order("relevance", "descending"),):
        fields = []
        for order in query.order:
            default = "descending" if order.field == "relevance" else "ascending"
            suffix = (
                ""
                if order.direction == default
                else (" DESC" if order.direction == "descending" else " ASC")
            )
            fields.append(order.field + suffix)
        text += " ORDER BY " + ", ".join(fields)
    return text
