from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from .placeholders import prepare_parameters
from .query import RESULT_TYPES, QueryError


def list_saved(connection, owner_id):
    return connection.execute(
        "SELECT id, name, result_type FROM saved_search_query "
        "WHERE owner_id = %s ORDER BY lower(name), id",
        (owner_id,),
    ).fetchall()


def get_saved(connection, owner_id, query_id):
    return connection.execute(
        "SELECT id, name, result_type, query_text, parameters FROM saved_search_query "
        "WHERE owner_id = %s AND id = %s",
        (owner_id, query_id),
    ).fetchone()


def create_saved(connection, owner_id, name, result_type, source, parameters, *, query_id=None):
    if (
        not isinstance(name, str)
        or not 1 <= len(name.strip()) <= 100
        or "\0" in name
        or any(0xD800 <= ord(char) <= 0xDFFF for char in name)
    ):
        raise QueryError("Enter a query name of 1 to 100 characters")
    if not isinstance(result_type, str) or result_type not in RESULT_TYPES:
        raise QueryError("Select a page type")
    parameters = prepare_parameters(source, parameters)
    try:
        with connection.transaction():
            connection.execute("SELECT id FROM account WHERE id = %s FOR UPDATE", (owner_id,))
            if query_id is not None:
                row = connection.execute(
                    "UPDATE saved_search_query SET name = %s, result_type = %s, "
                    "query_text = %s, parameters = %s WHERE id = %s AND owner_id = %s "
                    "RETURNING id, name, result_type",
                    (name.strip(), result_type, source, Jsonb(parameters), query_id, owner_id),
                ).fetchone()
                if row is None:
                    raise QueryError("Saved query not found")
            else:
                count = connection.execute(
                    "SELECT count(*) AS total FROM saved_search_query WHERE owner_id = %s",
                    (owner_id,),
                ).fetchone()["total"]
                if count >= 100:
                    raise QueryError("You can save at most 100 queries")
                row = connection.execute(
                    "INSERT INTO saved_search_query "
                    "(owner_id, name, result_type, query_text, parameters) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id, name, result_type",
                    (owner_id, name.strip(), result_type, source, Jsonb(parameters)),
                ).fetchone()
        connection.commit()
    except UniqueViolation as exc:
        raise QueryError("A saved query already has this name") from exc
    return row
