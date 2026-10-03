-- =============================================================================
--  Before the upgrade: a copy of what a v3 database holds
--  supabase/tests/upgrade_snapshot.sql      (used only by run_local.py)
-- =============================================================================
--  Run against a v3 database just before the current schema is applied to it.
--  upgrade_checks.sql compares the upgraded database with this copy. The copy
--  lives in its own schema, which nothing in the real schema touches.

create schema fbsnap;

create table fbsnap.counts as
  select 'classrooms' as tbl, count(*) as n from public.classrooms
  union all select 'students',  count(*) from public.students
  union all select 'notes',     count(*) from public.notes
  union all select 'stories',   count(*) from public.stories
  union all select 'consent',   count(*) from public.consent
  union all select 'audit_log', count(*) from public.audit_log;

create table fbsnap.classrooms as
  select code::text, teacher_id, name_mode, zip::text from public.classrooms;

-- badges and last_active are stored as they will look after the upgrade (a
-- JSON list, 'YYYY-MM-DD' text) so the checks compare like with like.
create table fbsnap.students as
  select id, classroom_code::text, name, avatar, access_token::text as v3_token,
         emotion_log, task_log, to_jsonb(badges) as badges,
         to_char(last_active, 'YYYY-MM-DD') as last_active
    from public.students;

create table fbsnap.consent as select id from public.consent;
create table fbsnap.last_audit as select coalesce(max(id), 0) as id from public.audit_log;
