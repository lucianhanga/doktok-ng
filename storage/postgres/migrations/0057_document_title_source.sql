-- #537: a user-renamed document title must survive reprocessing. 'auto' (default) = the
-- doc_metadata feature may re-derive the title; 'manual' = a user rename - the auto path
-- (set_metadata) must not overwrite it.
ALTER TABLE documents ADD COLUMN title_source text NOT NULL DEFAULT 'auto';
