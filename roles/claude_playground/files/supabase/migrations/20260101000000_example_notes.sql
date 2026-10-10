-- Example migration — reference RLS pattern: a per-user "notes" table.
-- Copy this shape for your own tables: RLS ON, anon has no row access, and each
-- authenticated user can read/write ONLY their own rows (owner = auth.uid()).
-- Written to be re-runnable (IF NOT EXISTS / DROP POLICY IF EXISTS).

create table if not exists public.notes (
    id         uuid        primary key default gen_random_uuid(),
    content    text        not null,
    owner      uuid        not null default auth.uid() references auth.users (id) on delete cascade,
    created_at timestamptz not null default now()
);

alter table public.notes enable row level security;

-- PostgREST connects as the anon / authenticated roles. Grant table privileges
-- (RLS still filters the rows); WITHOUT a grant PostgREST returns 401 instead of an
-- empty 200. anon gets SELECT so the endpoint is reachable, but has no policy below
-- -> RLS returns zero rows.
grant usage on schema public to anon, authenticated, service_role;
grant select on public.notes to anon;
grant select, insert, update, delete on public.notes to authenticated, service_role;

-- authenticated users: full CRUD on their OWN rows only.
drop policy if exists "notes_select_own" on public.notes;
create policy "notes_select_own" on public.notes
    for select to authenticated using (owner = auth.uid());

drop policy if exists "notes_insert_own" on public.notes;
create policy "notes_insert_own" on public.notes
    for insert to authenticated with check (owner = auth.uid());

drop policy if exists "notes_update_own" on public.notes;
create policy "notes_update_own" on public.notes
    for update to authenticated using (owner = auth.uid()) with check (owner = auth.uid());

drop policy if exists "notes_delete_own" on public.notes;
create policy "notes_delete_own" on public.notes
    for delete to authenticated using (owner = auth.uid());

-- anon: intentionally NO policy -> RLS denies every row (GET returns 200 with []).
