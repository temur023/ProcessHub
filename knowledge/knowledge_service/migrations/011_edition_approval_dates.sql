-- The bank adoption date belongs to the edition's immutable confirmation,
-- independently of effective dates and the time the file entered Process Hub.
-- Existing confirmations remain unknown; no upload or catalog date is copied.
ALTER TABLE document_edition_verifications ADD COLUMN approved_on date;
COMMENT ON COLUMN document_edition_verifications.approved_on IS
    'Bank adoption/approval date supported by verification evidence; null means not established';
