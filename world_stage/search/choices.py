from .catalog import ENTRY_CTE, ENTRY_FIELDS
from .query import FIELDS, RESULT_TYPES, QueryError, Value
from .wsql import format_value

REFERENCE_CHOICES = {
    "genre": "SELECT name AS value FROM subgenre",
    "language": "SELECT name AS value FROM language",
    "country": "SELECT name AS value FROM country",
    "code": "SELECT id AS value FROM country",
    "year": "SELECT id AS value FROM year",
    "show_type": "SELECT id AS value FROM show_types",
}
CHOICE_FIELDS = frozenset((*REFERENCE_CHOICES, "type", "status", "artist", "submitter"))


def search_choices(connection, result_type, field):
    if result_type not in RESULT_TYPES:
        raise QueryError("Select a page type", "/type")
    if field not in CHOICE_FIELDS or result_type not in FIELDS[field].categories:
        raise QueryError("This field has no value suggestions for this page type", "/field")
    if field == "type":
        values = list(RESULT_TYPES)
    else:
        if field == "status":
            source = (
                "SELECT name AS value FROM year_status"
                if result_type == "year"
                else "SELECT name AS value FROM show_status"
            )
        elif field in ("artist", "submitter"):
            expression = f"unnest({ENTRY_FIELDS['artist']})" if field == "artist" else "e.username"
            source = ENTRY_CTE + f"SELECT {expression} AS value FROM entries e"
        else:
            source = REFERENCE_CHOICES[field]
        with connection.transaction():
            previous = connection.execute(
                "SELECT current_setting('statement_timeout') AS timeout"
            ).fetchone()["timeout"]
            connection.execute("SELECT set_config('statement_timeout', '5000', true)")
            rows = connection.execute(
                f"SELECT DISTINCT value FROM ({source}) choices WHERE value IS NOT NULL"
            ).fetchall()
            connection.execute("SELECT set_config('statement_timeout', %s, true)", (previous,))
        values = [row["value"] for row in rows]
    values.sort(key=(lambda value: value.casefold()) if FIELDS[field].type == "text" else None)
    kind = "string" if FIELDS[field].type == "text" else "number"
    return [{"value": value, "literal": format_value(Value(kind, value))} for value in values]
