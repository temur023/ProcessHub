CREATE TABLE departments (
    id uuid PRIMARY KEY,
    name text NOT NULL CHECK (length(trim(name)) > 0),
    catalog_item_id text UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE roles (
    id uuid PRIMARY KEY,
    department_id uuid NOT NULL REFERENCES departments(id),
    name text NOT NULL CHECK (length(trim(name)) > 0),
    UNIQUE (department_id, name), UNIQUE (id, department_id)
);
CREATE TABLE access_grants (
    user_id bigint NOT NULL CHECK (user_id > 0),
    department_id uuid NOT NULL REFERENCES departments(id),
    permission text NOT NULL CHECK (permission IN ('reader', 'owner', 'reviewer')),
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, department_id, permission)
);
CREATE TABLE instructions (
    id uuid PRIMARY KEY,
    department_id uuid NOT NULL REFERENCES departments(id),
    role_id uuid NOT NULL,
    title text NOT NULL CHECK (length(trim(title)) > 0),
    created_by bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (role_id, department_id) REFERENCES roles(id, department_id)
);
CREATE TABLE instruction_versions (
    id uuid PRIMARY KEY,
    instruction_id uuid NOT NULL REFERENCES instructions(id),
    number integer NOT NULL CHECK (number > 0),
    filename text NOT NULL,
    media_type text NOT NULL,
    content bytea NOT NULL,
    content_hash text NOT NULL,
    byte_size integer NOT NULL CHECK (byte_size > 0 AND byte_size <= 10485760),
    effective_from date,
    effective_to date,
    uploaded_by bigint NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR (effective_from IS NOT NULL AND effective_to >= effective_from)),
    CHECK (octet_length(content) = byte_size),
    UNIQUE (instruction_id, number), UNIQUE (instruction_id, content_hash)
);
CREATE TABLE audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id bigint NOT NULL,
    department_id uuid NOT NULL REFERENCES departments(id),
    action text NOT NULL,
    entity_id uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX instructions_department ON instructions(department_id);
CREATE INDEX audit_department ON audit_events(department_id, created_at);

CREATE FUNCTION reject_document_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Immutable history cannot be changed';
END;
$$;
CREATE TRIGGER immutable_versions BEFORE UPDATE OR DELETE ON instruction_versions
FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();
