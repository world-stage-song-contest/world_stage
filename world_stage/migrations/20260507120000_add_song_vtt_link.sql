BEGIN;

ALTER TABLE song ADD COLUMN vtt_link text;

CREATE OR REPLACE FUNCTION song_audit_trigger()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_user_id  integer;
    v_changed  jsonb := '{}';
    v_song_changed    boolean := false;
    v_placeholder_changed boolean := false;
    v_ownership_changed boolean := false;
BEGIN
    v_user_id := NULLIF(current_setting('app.current_user_id', true), '')::integer;

    IF TG_OP = 'INSERT' THEN
        INSERT INTO song_audit_log
            (song_id, event_type, changed_by,
             song_title, song_artist, song_country_id, song_year_id)
        VALUES
            (NEW.id, 'create', v_user_id,
             NEW.title, NEW.artist, NEW.country_id, NEW.year_id);
        RETURN NEW;
    END IF;

    IF TG_OP = 'DELETE' THEN
        INSERT INTO song_audit_log
            (song_id, event_type, changed_by,
             song_title, song_artist, song_country_id, song_year_id)
        VALUES
            (OLD.id, 'delete', v_user_id,
             OLD.title, OLD.artist, OLD.country_id, OLD.year_id);
        RETURN OLD;
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF OLD.title IS DISTINCT FROM NEW.title THEN
            v_changed := v_changed || jsonb_build_object('title',
                jsonb_build_object('old', OLD.title, 'new', NEW.title));
            v_song_changed := true;
        END IF;

        IF OLD.artist IS DISTINCT FROM NEW.artist THEN
            v_changed := v_changed || jsonb_build_object('artist',
                jsonb_build_object('old', OLD.artist, 'new', NEW.artist));
            v_song_changed := true;
        END IF;

        IF OLD.native_title IS DISTINCT FROM NEW.native_title THEN
            v_changed := v_changed || jsonb_build_object('native_title',
                jsonb_build_object('old', OLD.native_title, 'new', NEW.native_title));
        END IF;

        IF OLD.video_link IS DISTINCT FROM NEW.video_link THEN
            v_changed := v_changed || jsonb_build_object('video_link',
                jsonb_build_object('old', OLD.video_link, 'new', NEW.video_link));
        END IF;

        IF OLD.poster_link IS DISTINCT FROM NEW.poster_link THEN
            v_changed := v_changed || jsonb_build_object('poster_link',
                jsonb_build_object('old', OLD.poster_link, 'new', NEW.poster_link));
        END IF;

        IF OLD.vtt_link IS DISTINCT FROM NEW.vtt_link THEN
            v_changed := v_changed || jsonb_build_object('vtt_link',
                jsonb_build_object('old', OLD.vtt_link, 'new', NEW.vtt_link));
        END IF;

        IF OLD.snippet_start IS DISTINCT FROM NEW.snippet_start THEN
            v_changed := v_changed || jsonb_build_object('snippet_start',
                jsonb_build_object('old', OLD.snippet_start::text, 'new', NEW.snippet_start::text));
        END IF;

        IF OLD.snippet_end IS DISTINCT FROM NEW.snippet_end THEN
            v_changed := v_changed || jsonb_build_object('snippet_end',
                jsonb_build_object('old', OLD.snippet_end::text, 'new', NEW.snippet_end::text));
        END IF;

        IF OLD.translated_lyrics IS DISTINCT FROM NEW.translated_lyrics THEN
            v_changed := v_changed || jsonb_build_object('translated_lyrics',
                jsonb_build_object('old', OLD.translated_lyrics, 'new', NEW.translated_lyrics));
        END IF;

        IF OLD.romanized_lyrics IS DISTINCT FROM NEW.romanized_lyrics THEN
            v_changed := v_changed || jsonb_build_object('romanized_lyrics',
                jsonb_build_object('old', OLD.romanized_lyrics, 'new', NEW.romanized_lyrics));
        END IF;

        IF OLD.native_lyrics IS DISTINCT FROM NEW.native_lyrics THEN
            v_changed := v_changed || jsonb_build_object('native_lyrics',
                jsonb_build_object('old', OLD.native_lyrics, 'new', NEW.native_lyrics));
        END IF;

        IF OLD.notes IS DISTINCT FROM NEW.notes THEN
            v_changed := v_changed || jsonb_build_object('notes',
                jsonb_build_object('old', OLD.notes, 'new', NEW.notes));
        END IF;

        IF OLD.sources IS DISTINCT FROM NEW.sources THEN
            v_changed := v_changed || jsonb_build_object('sources',
                jsonb_build_object('old', OLD.sources, 'new', NEW.sources));
        END IF;

        IF OLD.is_placeholder IS DISTINCT FROM NEW.is_placeholder THEN
            v_changed := v_changed || jsonb_build_object('is_placeholder',
                jsonb_build_object('old', OLD.is_placeholder::text, 'new', NEW.is_placeholder::text));
            v_placeholder_changed := true;
        END IF;

        IF OLD.admin_approved IS DISTINCT FROM NEW.admin_approved THEN
            v_changed := v_changed || jsonb_build_object('admin_approved',
                jsonb_build_object('old', OLD.admin_approved::text, 'new', NEW.admin_approved::text));
        END IF;

        IF OLD.submitter_id IS DISTINCT FROM NEW.submitter_id THEN
            v_changed := v_changed || jsonb_build_object('submitter_id',
                jsonb_build_object('old', OLD.submitter_id::text, 'new', NEW.submitter_id::text));
            v_ownership_changed := true;
        END IF;

        IF OLD.country_id IS DISTINCT FROM NEW.country_id THEN
            v_changed := v_changed || jsonb_build_object('country_id',
                jsonb_build_object('old', OLD.country_id, 'new', NEW.country_id));
        END IF;

        IF OLD.year_id IS DISTINCT FROM NEW.year_id THEN
            v_changed := v_changed || jsonb_build_object('year_id',
                jsonb_build_object('old', OLD.year_id::text, 'new', NEW.year_id::text));
        END IF;

        IF v_changed != '{}' THEN
            IF v_song_changed THEN
                INSERT INTO song_audit_log
                    (song_id, event_type, changed_by,
                     song_title, song_artist, song_country_id, song_year_id,
                     changed_fields)
                VALUES
                    (NEW.id, 'song_replacement', v_user_id,
                     NEW.title, NEW.artist, NEW.country_id, NEW.year_id,
                     jsonb_build_object('title',
                         jsonb_build_object('old', OLD.title,  'new', NEW.title),
                         'artist',
                         jsonb_build_object('old', OLD.artist, 'new', NEW.artist)));
            END IF;

            IF v_placeholder_changed THEN
                INSERT INTO song_audit_log
                    (song_id, event_type, changed_by,
                     song_title, song_artist, song_country_id, song_year_id)
                VALUES
                    (NEW.id,
                     CASE WHEN NEW.is_placeholder THEN 'placeholder_on' ELSE 'placeholder_off' END,
                     v_user_id,
                     NEW.title, NEW.artist, NEW.country_id, NEW.year_id);
            END IF;

            IF v_ownership_changed THEN
                INSERT INTO song_audit_log
                    (song_id, event_type, changed_by,
                     song_title, song_artist, song_country_id, song_year_id,
                     changed_fields)
                VALUES
                    (NEW.id, 'ownership_change', v_user_id,
                     NEW.title, NEW.artist, NEW.country_id, NEW.year_id,
                     jsonb_build_object('submitter_id',
                         jsonb_build_object('old', OLD.submitter_id::text, 'new', NEW.submitter_id::text)));
            END IF;

            IF (v_changed - CASE WHEN v_song_changed THEN 'title' ELSE '' END
                           - CASE WHEN v_song_changed THEN 'artist' ELSE '' END
                           - CASE WHEN v_placeholder_changed THEN 'is_placeholder' ELSE '' END
                           - CASE WHEN v_ownership_changed THEN 'submitter_id' ELSE '' END) != '{}' THEN
                INSERT INTO song_audit_log
                    (song_id, event_type, changed_by,
                     song_title, song_artist, song_country_id, song_year_id,
                     changed_fields)
                VALUES
                    (NEW.id, 'song_modification', v_user_id,
                     NEW.title, NEW.artist, NEW.country_id, NEW.year_id,
                     v_changed - CASE WHEN v_song_changed THEN 'title' ELSE '' END
                               - CASE WHEN v_song_changed THEN 'artist' ELSE '' END
                               - CASE WHEN v_placeholder_changed THEN 'is_placeholder' ELSE '' END
                               - CASE WHEN v_ownership_changed THEN 'submitter_id' ELSE '' END);
            END IF;
        END IF;

        RETURN NEW;
    END IF;

    RETURN NULL;
END;
$$;

COMMIT;
