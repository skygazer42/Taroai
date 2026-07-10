ALTER TABLE knowledge_chunks
    ADD COLUMN embedding_vector JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE knowledge_chunks
    ADD COLUMN embedding_model TEXT;

ALTER TABLE knowledge_chunks
    ADD COLUMN embedding_provider TEXT;

ALTER TABLE knowledge_chunks
    ADD COLUMN embedded_at TIMESTAMPTZ;
