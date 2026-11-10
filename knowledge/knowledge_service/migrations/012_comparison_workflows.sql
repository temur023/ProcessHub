-- Explicit assignment only; this does not grant existing staff a new action.
CREATE OR REPLACE FUNCTION valid_document_capabilities(values_array text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT cardinality(values_array) > 0
       AND 'documents.read' = ANY(values_array)
       AND values_array <@ ARRAY['documents.read','documents.download','instructions.create','versions.create',
                                'preparation.manage','instructions.delete','instructions.restore','comparisons.manage']::text[]
       AND array_position(values_array,NULL) IS NULL
       AND cardinality(values_array) = (SELECT count(DISTINCT value) FROM unnest(values_array) AS t(value))
$$;

-- The workflow tracks a person's review, not Nova execution or bank approval.
-- Exact source/report editions are immutable; all changes get an audit event.
CREATE TABLE comparison_workflows (
    id uuid PRIMARY KEY,
    document_id uuid NOT NULL REFERENCES instructions(id),
    edition_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'in_progress'
        CHECK(status IN ('in_progress','report_ready','action_required','completed')),
    revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
    note text NOT NULL CHECK(length(trim(note)) BETWEEN 1 AND 2000),
    report_document_id uuid,
    report_edition_id uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    started_by bigint NOT NULL CHECK(started_by>0),
    updated_by bigint NOT NULL CHECK(updated_by>0),
    UNIQUE(document_id,edition_id),
    FOREIGN KEY(edition_id,document_id) REFERENCES document_editions(id,document_id),
    FOREIGN KEY(report_edition_id,report_document_id) REFERENCES document_editions(id,document_id),
    CHECK((report_document_id IS NULL) = (report_edition_id IS NULL)),
    CHECK(status='in_progress' OR report_edition_id IS NOT NULL),
    CHECK(updated_at>=created_at)
);
CREATE INDEX comparison_workflows_document ON comparison_workflows(document_id,created_at DESC,id);
CREATE TRIGGER preserve_comparison_workflow_history BEFORE DELETE ON comparison_workflows
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();

CREATE FUNCTION protect_comparison_workflow_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.id,NEW.document_id,NEW.edition_id,NEW.created_at,NEW.started_by)
       IS DISTINCT FROM (OLD.id,OLD.document_id,OLD.edition_id,OLD.created_at,OLD.started_by)
       OR NEW.revision<>OLD.revision+1 OR NEW.updated_at<=OLD.updated_at THEN
        RAISE EXCEPTION 'Comparison workflow identity and revision must be preserved';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_comparison_workflow_identity BEFORE UPDATE ON comparison_workflows
    FOR EACH ROW EXECUTE FUNCTION protect_comparison_workflow_identity();
