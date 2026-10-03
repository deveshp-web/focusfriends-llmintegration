-- =============================================================================
--  Just enough of Supabase for the schema and its tests to run on plain Postgres
--  supabase/tests/supabase_stub.sql      (used only by run_local.py)
-- =============================================================================
--
--  NEVER run this against a real Supabase project - it creates roles and an
--  auth schema that Supabase already has.
--
--  It reproduces the three things the schema depends on:
--    * the anon / authenticated / service_role roles;
--    * auth.users and auth.uid(), with uid() read from the request's JWT claims
--      exactly as Supabase's own definition reads them;
--    * Supabase's DEFAULT PRIVILEGES, which grant every new public table and
--      function to anon and authenticated. Copying that matters: it means a
--      revoke the schema forgets shows up here as a failing test, the same way
--      it would leave a hole in a real project.

-- Roles belong to the whole server, and run_local.py builds several databases
-- on one server, so only the first database creates them.
do $roles$ begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end $roles$;

create schema auth;
create table auth.users (
  id         uuid primary key,
  email      text unique,
  created_at timestamptz default now()
);

-- Supabase's definition: the `sub` claim of the request's JWT.
create function auth.uid() returns uuid language sql stable as $$
  select coalesce(
    nullif(current_setting('request.jwt.claim.sub', true), ''),
    (nullif(current_setting('request.jwt.claims', true), '')::jsonb ->> 'sub')
  )::uuid
$$;

grant usage on schema auth to anon, authenticated, service_role;
grant execute on function auth.uid() to anon, authenticated, service_role;

grant usage on schema public to anon, authenticated, service_role;
alter default privileges in schema public grant all on tables    to anon, authenticated, service_role;
alter default privileges in schema public grant all on functions to anon, authenticated, service_role;
alter default privileges in schema public grant all on sequences to anon, authenticated, service_role;
