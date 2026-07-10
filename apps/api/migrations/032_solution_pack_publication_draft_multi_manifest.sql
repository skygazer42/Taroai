ALTER TABLE customer_solution_pack_publication_drafts ADD COLUMN proposed_skill_manifests JSONB NOT NULL DEFAULT '[]'::jsonb;
