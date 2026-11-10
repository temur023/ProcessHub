-- Automatic analysis of one exact instruction edition against the department
-- regulation in Nova. A run is a durable job; a finished run is immutable.
-- Re-analysis creates a new run, so earlier results stay in the history.
CREATE TABLE analysis_runs (
    id uuid PRIMARY KEY,
    kind text NOT NULL CHECK (kind IN ('instruction_regulation')),
    document_id uuid NOT NULL REFERENCES instructions(id),
    edition_id uuid NOT NULL,
    file_id uuid NOT NULL REFERENCES instruction_versions(id),
    department_id uuid NOT NULL REFERENCES departments(id),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','running','completed','failed','cancelled')),
    stage text,
    progress jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(progress)='object'),
    method_version text NOT NULL,
    collection text NOT NULL,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
    lease_token uuid,
    lease_expires_at timestamptz,
    error_code text,
    error_message text,
    result jsonb,
    requested_by bigint NOT NULL CHECK (requested_by>0),
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    finished_at timestamptz,
    FOREIGN KEY (edition_id,document_id) REFERENCES document_editions(id,document_id),
    CHECK ((status='completed') = (result IS NOT NULL)),
    CHECK ((status IN ('completed','failed','cancelled')) = (finished_at IS NOT NULL))
);
-- One active analysis per document: a second click returns the running one.
CREATE UNIQUE INDEX analysis_runs_one_active ON analysis_runs(document_id) WHERE status IN ('queued','running');
CREATE INDEX analysis_runs_document ON analysis_runs(document_id,created_at DESC,id);
CREATE INDEX analysis_runs_queue ON analysis_runs(created_at,id) WHERE status IN ('queued','running');

CREATE FUNCTION protect_finished_analysis() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP='DELETE' OR OLD.status IN ('completed','failed','cancelled') THEN
        RAISE EXCEPTION 'Finished analysis cannot be changed';
    END IF;
    IF (NEW.id,NEW.kind,NEW.document_id,NEW.edition_id,NEW.file_id,NEW.department_id,NEW.method_version,
        NEW.collection,NEW.requested_by,NEW.created_at)
       IS DISTINCT FROM (OLD.id,OLD.kind,OLD.document_id,OLD.edition_id,OLD.file_id,OLD.department_id,
        OLD.method_version,OLD.collection,OLD.requested_by,OLD.created_at) THEN
        RAISE EXCEPTION 'Analysis identity cannot be changed';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER protect_finished_analysis BEFORE UPDATE OR DELETE ON analysis_runs
    FOR EACH ROW EXECUTE FUNCTION protect_finished_analysis();

-- An expert's decision on one finding. Append-only: the latest decision is
-- current, earlier ones remain as the history of the review.
CREATE TABLE analysis_finding_reviews (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id uuid NOT NULL REFERENCES analysis_runs(id),
    finding_id text NOT NULL CHECK (finding_id ~ '^[FM][0-9]{1,4}$'),
    decision text NOT NULL CHECK (decision IN ('confirmed','rejected','clarify')),
    comment text NOT NULL DEFAULT '' CHECK (length(comment)<=2000),
    reviewed_by bigint NOT NULL CHECK (reviewed_by>0),
    reviewed_at timestamptz NOT NULL DEFAULT now(),
    CHECK (decision='confirmed' OR length(trim(comment))>0)
);
CREATE INDEX analysis_finding_reviews_run ON analysis_finding_reviews(run_id,finding_id,id DESC);
CREATE TRIGGER immutable_analysis_finding_reviews BEFORE UPDATE OR DELETE ON analysis_finding_reviews
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();

-- Nova keeps all departments' regulations in one collection. The exact
-- document name (metadata.document) of this department's regulation lets the
-- analysis ignore clauses from other departments. Unknown until confirmed.
ALTER TABLE departments ADD COLUMN nova_regulation_document text
    CHECK (nova_regulation_document IS NULL OR length(trim(nova_regulation_document)) BETWEEN 1 AND 500);
