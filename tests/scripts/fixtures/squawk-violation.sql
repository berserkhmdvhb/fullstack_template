BEGIN;
CREATE INDEX idx_items_name ON items (name);
COMMIT;
