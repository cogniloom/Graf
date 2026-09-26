-- Explicit administrative migration; runtime never executes DDL.
-- Runtime: SELECT, INSERT, UPDATE on these four tables. No DELETE needed.
CREATE TABLE IF NOT EXISTS public.docworm_workspace (
 id text PRIMARY KEY, home text NOT NULL UNIQUE,
 revision bigint NOT NULL DEFAULT 0, published_revision bigint,
 state text NOT NULL DEFAULT 'empty', phase text NOT NULL DEFAULT 'idle',
 snapshot_id text, publication jsonb, counts jsonb NOT NULL DEFAULT '{}',
 message text NOT NULL DEFAULT 'Add a local source to begin.'
);
CREATE TABLE IF NOT EXISTS public.docworm_sources (
 id text PRIMARY KEY, workspace text NOT NULL REFERENCES public.docworm_workspace(id),
 path text NOT NULL, kind text NOT NULL CHECK (kind IN ('file','directory')),
 enabled boolean NOT NULL DEFAULT true, removed boolean NOT NULL DEFAULT false,
 status text NOT NULL DEFAULT 'pending', inventory jsonb, error text,
 file_count bigint NOT NULL DEFAULT 0
);
CREATE UNIQUE INDEX IF NOT EXISTS docworm_active_path ON public.docworm_sources(workspace,path) WHERE NOT removed;
CREATE TABLE IF NOT EXISTS public.docworm_jobs (
 id text PRIMARY KEY, workspace text NOT NULL REFERENCES public.docworm_workspace(id),
 revision bigint NOT NULL, state text NOT NULL DEFAULT 'queued', phase text NOT NULL DEFAULT 'queued',
 started_at timestamptz, finished_at timestamptz, error text,
 progress jsonb NOT NULL DEFAULT '{}', attempts integer NOT NULL DEFAULT 0,
 UNIQUE(workspace,revision)
);
CREATE INDEX IF NOT EXISTS docworm_jobs_pending ON public.docworm_jobs(workspace,state,revision);
CREATE TABLE IF NOT EXISTS public.docworm_worksets (
 id text NOT NULL, workspace text NOT NULL REFERENCES public.docworm_workspace(id),
 revision bigint NOT NULL, snapshot_id text NOT NULL,
 PRIMARY KEY(workspace,id,revision)
);
