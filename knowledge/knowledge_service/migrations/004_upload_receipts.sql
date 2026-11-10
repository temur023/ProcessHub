-- A browser may safely retry after the successful response was lost.
-- Receipt keys are private to the signed Hub user and bind the entire payload.
CREATE TABLE upload_receipts (
    actor_id bigint NOT NULL CHECK (actor_id > 0),
    upload_id uuid NOT NULL,
    request_hash text NOT NULL,
    instruction_id uuid NOT NULL REFERENCES instructions(id),
    version_id uuid NOT NULL REFERENCES instruction_versions(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_id, upload_id)
);
CREATE TRIGGER immutable_upload_receipts BEFORE UPDATE OR DELETE ON upload_receipts
FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();
