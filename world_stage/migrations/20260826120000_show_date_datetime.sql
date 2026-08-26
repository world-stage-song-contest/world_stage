ALTER TABLE show
    ALTER COLUMN date TYPE timestamptz
    USING (
        CASE
            WHEN date IS NULL THEN NULL
            WHEN date < DATE '2025-11-15' THEN
                (date + TIME '20:00') AT TIME ZONE 'Europe/Warsaw'
            ELSE
                (date + TIME '19:30') AT TIME ZONE 'Europe/Warsaw'
        END
    );
