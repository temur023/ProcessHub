-- Keep the document and immutable original ledgers, UUIDs, bytes, receipts and
-- preparation foreign keys in place. Editions group originals without copying.
ALTER TABLE instructions ALTER COLUMN department_id DROP NOT NULL;
ALTER TABLE audit_events ALTER COLUMN department_id DROP NOT NULL;
-- The same appendix may occur in successive complete editions. Legacy APIs
-- retain their application-level duplicate checks under the document lock.
ALTER TABLE instruction_versions DROP CONSTRAINT instruction_versions_instruction_id_content_hash_key;
CREATE INDEX original_document_hash ON instruction_versions(instruction_id,content_hash);
ALTER TABLE instructions ADD COLUMN library_scope text NOT NULL DEFAULT 'department'
    CHECK (library_scope IN ('department','bank'));
ALTER TABLE instructions ADD COLUMN document_type text NOT NULL DEFAULT 'job_instruction'
    CHECK (document_type IN ('job_instruction','department_regulation','procedure','methodology',
        'order','form','organization','sop','analytics','correspondence','other'));
ALTER TABLE instructions ADD COLUMN owner_department_id uuid REFERENCES departments(id);
ALTER TABLE instructions ADD COLUMN bank_number text;
ALTER TABLE instructions ADD COLUMN document_date date;
ALTER TABLE instructions ADD COLUMN metadata jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(metadata)='object');
UPDATE instructions SET owner_department_id=department_id;
ALTER TABLE instructions ADD CONSTRAINT document_library_scope
    CHECK ((library_scope='department' AND department_id IS NOT NULL)
        OR (library_scope='bank' AND department_id IS NULL AND role_id IS NULL));

CREATE TABLE document_applicability (
    document_id uuid NOT NULL REFERENCES instructions(id),
    department_id uuid NOT NULL REFERENCES departments(id),
    status text NOT NULL DEFAULT 'unconfirmed' CHECK (status IN ('unconfirmed','confirmed')),
    evidence text NOT NULL DEFAULT '',
    PRIMARY KEY(document_id,department_id),
    CHECK (status<>'confirmed' OR length(trim(evidence))>0)
);
CREATE INDEX document_applicability_department ON document_applicability(department_id,document_id);

CREATE TABLE document_editions (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES instructions(id),
    number integer NOT NULL CHECK (number>0),
    label text,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(metadata)='object'),
    uploaded_by bigint NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(document_id,number), UNIQUE(id,document_id)
);
CREATE TABLE document_edition_files (
    edition_id uuid NOT NULL,
    document_id uuid NOT NULL,
    file_id uuid NOT NULL REFERENCES instruction_versions(id) DEFERRABLE INITIALLY DEFERRED,
    position integer NOT NULL CHECK(position>=0),
    purpose text NOT NULL DEFAULT 'main' CHECK(purpose IN ('main','attachment','approval','supplement')),
    source_metadata jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(source_metadata)='object'),
    FOREIGN KEY(edition_id,document_id) REFERENCES document_editions(id,document_id),
    PRIMARY KEY(edition_id,file_id), UNIQUE(edition_id,position)
);
CREATE INDEX document_edition_files_original ON document_edition_files(file_id);

-- Manual confirmation is a separate immutable fact. Legacy effective dates
-- are retained on originals, never promoted to verified dates by migration.
CREATE TABLE document_edition_verifications (
    edition_id uuid PRIMARY KEY REFERENCES document_editions(id),
    valid_from date NOT NULL,
    valid_to date,
    evidence text NOT NULL CHECK(length(trim(evidence))>0),
    verified_by bigint NOT NULL CHECK(verified_by>0),
    verified_at timestamptz NOT NULL DEFAULT now(),
    CHECK(valid_to IS NULL OR valid_to>=valid_from)
);

INSERT INTO document_editions(id,document_id,number,uploaded_by,uploaded_at)
SELECT id,instruction_id,number,uploaded_by,uploaded_at FROM instruction_versions;
INSERT INTO document_edition_files(edition_id,document_id,file_id,position)
SELECT id,instruction_id,id,0 FROM instruction_versions;

CREATE FUNCTION connect_legacy_original_to_edition() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM document_edition_files WHERE file_id=NEW.id) THEN
        PERFORM id FROM instructions WHERE id=NEW.instruction_id FOR UPDATE;
        INSERT INTO document_editions(id,document_id,number,uploaded_by,uploaded_at)
        SELECT NEW.id,NEW.instruction_id,coalesce(max(number),0)+1,NEW.uploaded_by,NEW.uploaded_at
            FROM document_editions WHERE document_id=NEW.instruction_id;
        INSERT INTO document_edition_files(edition_id,document_id,file_id,position)
            VALUES(NEW.id,NEW.instruction_id,NEW.id,0);
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER legacy_original_edition AFTER INSERT ON instruction_versions
    FOR EACH ROW EXECUTE FUNCTION connect_legacy_original_to_edition();

CREATE FUNCTION validate_edition_original() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(SELECT 1 FROM instruction_versions WHERE id=NEW.file_id AND instruction_id=NEW.document_id) THEN
        RAISE EXCEPTION 'Edition original belongs to another document';
    END IF;
    RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER edition_original_document AFTER INSERT ON document_edition_files
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_edition_original();

CREATE FUNCTION reject_overlapping_verified_editions() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_id uuid;
BEGIN
    SELECT document_id INTO parent_id FROM document_editions WHERE id=NEW.edition_id;
    PERFORM id FROM instructions WHERE id=parent_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM document_edition_verifications v JOIN document_editions e ON e.id=v.edition_id
        WHERE e.document_id=parent_id AND v.edition_id<>NEW.edition_id
        AND daterange(v.valid_from,v.valid_to,'[]') && daterange(NEW.valid_from,NEW.valid_to,'[]')) THEN
        RAISE EXCEPTION 'Verified edition periods overlap';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER verified_edition_no_overlap BEFORE INSERT ON document_edition_verifications
    FOR EACH ROW EXECUTE FUNCTION reject_overlapping_verified_editions();
CREATE TRIGGER immutable_editions BEFORE UPDATE OR DELETE ON document_editions
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();
CREATE TRIGGER immutable_edition_files BEFORE UPDATE OR DELETE ON document_edition_files
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();
CREATE TRIGGER immutable_edition_verifications BEFORE UPDATE OR DELETE ON document_edition_verifications
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();
