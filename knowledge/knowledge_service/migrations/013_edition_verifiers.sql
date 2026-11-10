-- Confirming an edition's dates becomes an explicit, assignable action.
-- No preset or existing role receives it automatically.
CREATE OR REPLACE FUNCTION valid_document_capabilities(values_array text[]) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT cardinality(values_array) > 0
       AND 'documents.read' = ANY(values_array)
       AND values_array <@ ARRAY['documents.read','documents.download','instructions.create','versions.create',
                                'preparation.manage','instructions.delete','instructions.restore','comparisons.manage',
                                'editions.verify']::text[]
       AND array_position(values_array,NULL) IS NULL
       AND cardinality(values_array) = (SELECT count(DISTINCT value) FROM unnest(values_array) AS t(value))
$$;
