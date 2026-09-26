-- Applied explicitly by migrate(dsn), using the migration owner's credentials.
-- Runtime needs only SELECT and INSERT. Payload text is canonical JSON, avoiding
-- jsonb's numeric/Unicode normalization while retaining original evidence text.
CREATE TABLE IF NOT EXISTS public.hybrid_runs (
    id text PRIMARY KEY, identity text NOT NULL, manifest text NOT NULL,
    manifest_sha text NOT NULL
);
CREATE TABLE IF NOT EXISTS public.hybrid_candidates (
    run text NOT NULL REFERENCES public.hybrid_runs(id), sid text NOT NULL,
    position integer NOT NULL CHECK (position >= 0), payload text NOT NULL,
    PRIMARY KEY (run, sid), UNIQUE (run, position)
);
CREATE TABLE IF NOT EXISTS public.hybrid_reviews (
    run text NOT NULL, sid text NOT NULL, receipt text NOT NULL, receipt_sha text NOT NULL,
    PRIMARY KEY (run, sid),
    FOREIGN KEY (run, sid) REFERENCES public.hybrid_candidates(run, sid)
);
CREATE TABLE IF NOT EXISTS public.hybrid_snapshots (
    id text PRIMARY KEY, manifest_sha text NOT NULL, manifest text NOT NULL,
    content_sha text NOT NULL, passage_count integer NOT NULL CHECK (passage_count >= 0),
    edge_count integer NOT NULL CHECK (edge_count >= 0)
);
CREATE TABLE IF NOT EXISTS public.hybrid_documents (
    snapshot text NOT NULL REFERENCES public.hybrid_snapshots(id), id text NOT NULL,
    position integer NOT NULL CHECK (position >= 0), payload text NOT NULL,
    PRIMARY KEY (snapshot, id), UNIQUE (snapshot, position)
);
CREATE TABLE IF NOT EXISTS public.hybrid_passages (
    snapshot text NOT NULL REFERENCES public.hybrid_snapshots(id), id text NOT NULL,
    document_id text NOT NULL, ordinal integer NOT NULL CHECK (ordinal >= 0), payload text NOT NULL,
    PRIMARY KEY (snapshot, id), UNIQUE (snapshot, document_id, ordinal),
    FOREIGN KEY (snapshot, document_id) REFERENCES public.hybrid_documents(snapshot, id)
);
CREATE TABLE IF NOT EXISTS public.hybrid_edges (
    snapshot text NOT NULL REFERENCES public.hybrid_snapshots(id), id text NOT NULL,
    position integer NOT NULL CHECK (position >= 0), from_node text NOT NULL,
    to_node text, relation_type text NOT NULL, payload text NOT NULL,
    PRIMARY KEY (snapshot, id), UNIQUE (snapshot, position)
);
-- Edges can point to historical/non-document nodes or unresolved targets.
CREATE INDEX IF NOT EXISTS hybrid_edges_from ON public.hybrid_edges(snapshot, from_node);
CREATE INDEX IF NOT EXISTS hybrid_edges_to ON public.hybrid_edges(snapshot, to_node);
CREATE TABLE IF NOT EXISTS public.hybrid_results (
    id text PRIMARY KEY, identity text NOT NULL, payload text NOT NULL, payload_sha text NOT NULL
);

CREATE OR REPLACE FUNCTION public.hybrid_reject_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Immutable hybrid persistence row';
END;
$$;
DO $$
DECLARE tab text;
BEGIN
    FOREACH tab IN ARRAY ARRAY['hybrid_runs', 'hybrid_candidates', 'hybrid_reviews',
        'hybrid_snapshots', 'hybrid_documents', 'hybrid_passages', 'hybrid_edges', 'hybrid_results']
    LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_trigger
                       WHERE tgrelid = ('public.' || tab)::regclass AND tgname = 'hybrid_immutable') THEN
            EXECUTE format('CREATE TRIGGER hybrid_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON public.%I '
                           'FOR EACH STATEMENT EXECUTE FUNCTION public.hybrid_reject_mutation()', tab);
        END IF;
    END LOOP;
END;
$$;
