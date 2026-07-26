from unittest.mock import MagicMock, patch

from world_stage.routes.admin.misc import fuckup_db_post


def test_fuckup_db_limits_restricted_role_to_transaction(app):
    db = MagicMock()
    cursor = db.cursor.return_value
    cursor.description = [("answer",)]
    cursor.fetchall.return_value = [{"answer": 42}]

    with (
        app.test_request_context(
            "/admin/fuckupdb",
            method="POST",
            data={"query": "SELECT 42 AS answer", "kind": "html"},
        ),
        patch("world_stage.routes.admin.misc.get_db", return_value=db),
        patch("world_stage.routes.admin.misc.subprocess.run"),
        patch("world_stage.routes.admin.misc.render_template", return_value="rendered"),
    ):
        response = fuckup_db_post()

    assert response == "rendered"
    assert cursor.execute.call_args_list == [
        (("SET LOCAL ROLE dml_only_role",),),
        (("SELECT 42 AS answer",),),
    ]
    cursor.fetchall.assert_called_once_with()
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()
