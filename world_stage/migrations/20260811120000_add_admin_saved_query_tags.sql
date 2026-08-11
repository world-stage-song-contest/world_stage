ALTER TABLE admin_saved_query
    ADD COLUMN tags text[] NOT NULL DEFAULT '{}';

ALTER TABLE admin_saved_query
    ADD CONSTRAINT admin_saved_query_tags_count_check
    CHECK (cardinality(tags) <= 20);
