-- Closing an open-ended confirmed period records a new fact; its original
-- verification, evidence and dates remain intact and immutable.
CREATE TABLE document_edition_endings (
    edition_id uuid PRIMARY KEY REFERENCES document_edition_verifications(edition_id),
    valid_to date NOT NULL,
    evidence text NOT NULL CHECK(length(trim(evidence))>0),
    ended_by bigint NOT NULL CHECK(ended_by>0),
    ended_at timestamptz NOT NULL DEFAULT now()
);
CREATE FUNCTION validate_edition_ending() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_id uuid; verification document_edition_verifications%ROWTYPE;
BEGIN
    SELECT document_id INTO parent_id FROM document_editions WHERE id=NEW.edition_id;
    PERFORM id FROM instructions WHERE id=parent_id FOR UPDATE;
    SELECT * INTO verification FROM document_edition_verifications WHERE edition_id=NEW.edition_id;
    IF NEW.valid_to<verification.valid_from OR
       (verification.valid_to IS NOT NULL AND NEW.valid_to>verification.valid_to) THEN
        RAISE EXCEPTION 'Invalid end of verified edition period';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER valid_edition_ending BEFORE INSERT ON document_edition_endings
    FOR EACH ROW EXECUTE FUNCTION validate_edition_ending();
CREATE TRIGGER immutable_edition_endings BEFORE UPDATE OR DELETE ON document_edition_endings
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();

CREATE OR REPLACE FUNCTION reject_overlapping_verified_editions() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_id uuid;
BEGIN
    SELECT document_id INTO parent_id FROM document_editions WHERE id=NEW.edition_id;
    PERFORM id FROM instructions WHERE id=parent_id FOR UPDATE;
    IF EXISTS(SELECT 1 FROM document_edition_verifications v JOIN document_editions e ON e.id=v.edition_id
        LEFT JOIN document_edition_endings ending ON ending.edition_id=v.edition_id
        WHERE e.document_id=parent_id AND v.edition_id<>NEW.edition_id
        AND daterange(v.valid_from,coalesce(ending.valid_to,v.valid_to),'[]') && daterange(NEW.valid_from,NEW.valid_to,'[]')) THEN
        RAISE EXCEPTION 'Verified edition periods overlap';
    END IF;
    RETURN NEW;
END;
$$;
