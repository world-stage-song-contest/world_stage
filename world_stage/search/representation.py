from .query import Query, QueryError, decode_query, encode_query
from .wsql import format_wsql, parse_wsql


def parse_search_query(value) -> Query:
    return parse_wsql(value) if isinstance(value, str) else decode_query(value)


def convert_query(value, target: str):
    if target not in ("json", "wsql"):
        raise QueryError("Conversion target must be json or wsql", "/to")
    query = parse_search_query(value)
    return encode_query(query) if target == "json" else format_wsql(query)
