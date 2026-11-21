BEGIN;
CREATE TABLE notes (
    id uuid NOT NULL PRIMARY KEY,
    body text NOT NULL
);
COMMIT;
