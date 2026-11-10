-- Business positions are independent of access roles and employee accounts.
ALTER TABLE roles ADD COLUMN display_name text;
ALTER TABLE roles ADD COLUMN unit_name text NOT NULL DEFAULT 'Служба не указана';
ALTER TABLE roles ADD COLUMN category text NOT NULL DEFAULT 'other'
    CHECK (category IN ('management','specialists','support','other'));
ALTER TABLE roles ADD COLUMN historical_candidate boolean NOT NULL DEFAULT false;
ALTER TABLE roles ADD COLUMN classification_evidence text NOT NULL DEFAULT '';
CREATE INDEX roles_library_scope ON roles(department_id,historical_candidate,unit_name);
