-- migrations/007_normalize_taxonomy_version.sql
--
-- Normalize reports.taxonomy_version to one stock-list version label.
--
-- taxonomy_version records the version of the stock list (KRX CSV) used to
-- tag the row. The old tagger built that label from the CSV file's
-- modification time, so the same stock-list content ended up under three
-- labels: KRX@2026-05-08, KRX@2026-05-09 and KRX@2026-05-12. The CSV has a
-- single commit in git; only its file time differed between checkouts.
-- The tagger now reads the label from
-- docs/stock_data/KRX_stocks_data.version.json (KRX@2026-05-08), so this
-- one-time update moves the two stray labels onto it.
--
-- Running it again changes nothing. No table structure changes.
-- No BEGIN/COMMIT here: the caller runs it inside one transaction, checks the
-- result, then commits.

UPDATE reports
   SET taxonomy_version = 'KRX@2026-05-08'
 WHERE taxonomy_version IN ('KRX@2026-05-09', 'KRX@2026-05-12');
