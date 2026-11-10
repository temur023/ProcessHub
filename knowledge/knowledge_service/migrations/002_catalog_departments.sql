ALTER TABLE departments ADD COLUMN catalog_space_id text;
ALTER TABLE departments ADD COLUMN catalog_number text NOT NULL DEFAULT '';
ALTER TABLE departments ADD COLUMN catalog_position integer NOT NULL DEFAULT 0;
CREATE INDEX departments_catalog_order ON departments(catalog_space_id, catalog_position);
