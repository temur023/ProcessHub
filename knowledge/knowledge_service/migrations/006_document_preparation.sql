-- Preparation is independent of immutable originals and of bank approval.
CREATE TABLE preparation_jobs (
    id uuid PRIMARY KEY,
    version_id uuid NOT NULL REFERENCES instruction_versions(id),
    parser_version text NOT NULL,
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','completed','failed','cancelled')),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    error_code text,
    message text,
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    started_at timestamptz,
    finished_at timestamptz,
    available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    lease_expires_at timestamptz,
    lease_token uuid,
    UNIQUE(version_id, parser_version),
    CHECK ((status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
    CHECK (status <> 'running' OR attempts > 0)
);
CREATE INDEX preparation_queue ON preparation_jobs(available_at,created_at,id) WHERE status='queued';
CREATE INDEX preparation_expired ON preparation_jobs(lease_expires_at,id) WHERE status='running';

CREATE TABLE prepared_sources (
    id uuid PRIMARY KEY,
    version_id uuid NOT NULL REFERENCES instruction_versions(id),
    parser_version text NOT NULL,
    content_hash text NOT NULL,
    status text NOT NULL CHECK (status IN ('ready','partial','needs_ocr')),
    blocks jsonb NOT NULL CHECK (jsonb_typeof(blocks) = 'array'),
    warnings jsonb NOT NULL CHECK (jsonb_typeof(warnings) = 'array'),
    stats jsonb NOT NULL CHECK (jsonb_typeof(stats) = 'object'),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    UNIQUE(version_id,parser_version)
);

CREATE TRIGGER prepared_sources_immutable BEFORE UPDATE OR DELETE ON prepared_sources
    FOR EACH ROW EXECUTE FUNCTION reject_document_mutation();

-- Existing originals become queued work, without impersonating their uploader.
-- The first deployed parser version is also used by extraction.PARSER_VERSION.
INSERT INTO preparation_jobs(id,version_id,parser_version)
SELECT gen_random_uuid(),id,'source-v1' FROM instruction_versions;
