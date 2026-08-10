ALTER TABLE admin_saved_query
    DROP CONSTRAINT admin_saved_query_operation_check;

ALTER TABLE admin_saved_query
    ADD CONSTRAINT admin_saved_query_operation_check
    CHECK (operation IN ('select', 'insert', 'update', 'delete'));

ALTER TABLE admin_saved_query
    ADD CONSTRAINT admin_saved_query_builder_delete_check
    CHECK (query_kind = 'sql' OR operation <> 'delete');

ALTER TABLE admin_query_log
    DROP CONSTRAINT admin_query_log_operation_check;

ALTER TABLE admin_query_log
    ADD CONSTRAINT admin_query_log_operation_check
    CHECK (operation IN ('select', 'insert', 'update', 'delete', 'other'));
