BEGIN;

-- Keep the existing audit trigger's event grouping while adding the new
-- columns to its changed_fields payload. pg_get_functiondef lets this
-- migration amend whichever current version of the trigger is installed.
DO $migration$
DECLARE
    v_definition text;
    v_original text;
BEGIN
    SELECT pg_get_functiondef('song_audit_trigger()'::regprocedure)
    INTO v_definition;
    v_original := v_definition;

    IF position('OLD.snippet2_start' IN v_definition) = 0 THEN
        v_definition := replace(
            v_definition,
            'IF OLD.translated_lyrics IS DISTINCT FROM NEW.translated_lyrics THEN',
            $replacement$IF OLD.snippet2_start IS DISTINCT FROM NEW.snippet2_start THEN
            v_changed := v_changed || jsonb_build_object('snippet2_start',
                jsonb_build_object('old', OLD.snippet2_start::text,
                                   'new', NEW.snippet2_start::text));
        END IF;

        IF OLD.snippet2_end IS DISTINCT FROM NEW.snippet2_end THEN
            v_changed := v_changed || jsonb_build_object('snippet2_end',
                jsonb_build_object('old', OLD.snippet2_end::text,
                                   'new', NEW.snippet2_end::text));
        END IF;

        IF OLD.translated_lyrics IS DISTINCT FROM NEW.translated_lyrics THEN$replacement$
        );

        IF v_definition = v_original THEN
            RAISE EXCEPTION 'Could not update song_audit_trigger for snippet2 fields';
        END IF;

        EXECUTE v_definition;
    END IF;
END;
$migration$;

COMMIT;
