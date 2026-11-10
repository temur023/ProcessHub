-- Access roles are separate from the professional roles attached to documents.
CREATE TABLE access_roles (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(trim(name)) > 0),
    name_key text NOT NULL UNIQUE CHECK (length(name_key) > 0),
    permission text NOT NULL CHECK (permission IN ('reader','owner','reviewer')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE role_departments (
    role_id uuid NOT NULL REFERENCES access_roles(id),
    department_id uuid NOT NULL REFERENCES departments(id),
    PRIMARY KEY (role_id,department_id)
);
CREATE INDEX role_departments_department ON role_departments(department_id,role_id);
CREATE TABLE role_memberships (
    role_id uuid NOT NULL REFERENCES access_roles(id),
    user_id bigint NOT NULL CHECK (user_id > 0),
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (role_id,user_id)
);
CREATE INDEX role_memberships_user ON role_memberships(user_id,role_id);
CREATE TABLE access_role_audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id bigint NOT NULL CHECK (actor_id > 0),
    role_id uuid NOT NULL REFERENCES access_roles(id),
    user_id bigint CHECK (user_id > 0),
    action text NOT NULL,
    details jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_access_role_audit BEFORE UPDATE OR DELETE ON access_role_audit_events
FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();

-- An original can be registered before its professional role is classified.
ALTER TABLE instructions ALTER COLUMN role_id DROP NOT NULL;
