BEGIN;

CREATE TABLE saved_search_query (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    owner_id bigint NOT NULL REFERENCES account (id) ON DELETE CASCADE,
    name text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 100),
    result_type text NOT NULL CHECK (result_type IN ('entry', 'year', 'country', 'submitter', 'artist', 'show')),
    query_text text NOT NULL CHECK (char_length(query_text) <= 65536),
    parameters jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (owner_id, name)
);

COMMIT;
