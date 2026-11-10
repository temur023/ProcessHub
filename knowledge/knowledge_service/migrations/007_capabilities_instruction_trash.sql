ALTER TABLE access_roles ADD COLUMN capabilities text[];
UPDATE access_roles SET capabilities = CASE permission WHEN 'owner' THEN
    ARRAY['documents.read','documents.download','instructions.create','versions.create','preparation.manage']::text[]
    ELSE ARRAY['documents.read','documents.download']::text[] END;
ALTER TABLE access_roles ALTER COLUMN capabilities SET NOT NULL;
ALTER TABLE access_roles ALTER COLUMN capabilities SET DEFAULT ARRAY['documents.read','documents.download']::text[];

CREATE FUNCTION valid_document_capabilities(values_array text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT cardinality(values_array) > 0
       AND 'documents.read' = ANY(values_array)
       AND values_array <@ ARRAY['documents.read','documents.download','instructions.create','versions.create',
                                'preparation.manage','instructions.delete','instructions.restore']::text[]
       AND array_position(values_array,NULL) IS NULL
       AND cardinality(values_array) = (SELECT count(DISTINCT value) FROM unnest(values_array) AS t(value))
$$;
ALTER TABLE access_roles ADD CONSTRAINT access_roles_valid_capabilities CHECK(valid_document_capabilities(capabilities));

ALTER TABLE instructions ADD COLUMN deleted_at timestamptz;
ALTER TABLE instructions ADD COLUMN deleted_by bigint CHECK(deleted_by > 0);
ALTER TABLE instructions ADD CONSTRAINT instructions_deletion_state
    CHECK((deleted_at IS NULL) = (deleted_by IS NULL));
CREATE INDEX instructions_active ON instructions(department_id,created_at DESC,id) WHERE deleted_at IS NULL;
CREATE INDEX instructions_deleted ON instructions(department_id,deleted_at DESC,id) WHERE deleted_at IS NOT NULL;
