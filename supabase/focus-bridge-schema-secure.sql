-- =============================================================================
--  Focus Bridge — database schema, row-level security and server functions
--  supabase/focus-bridge-schema-secure.sql
-- =============================================================================
--
--  WHAT THIS IS
--  ------------
--  The server half of the contract that both copies of the app
--  (focusbridge_sourcecode/ and integrated_version/) are written against: the
--  six tables the teacher dashboard reads and writes over REST, and the
--  database functions ("RPCs") students call without logging in.
--
--  The app has always told people to run a file with this name. Until now the
--  file was not in the repository, so the rules protecting children's records
--  could not be reviewed, tested or reproduced. This is that file.
--
--  HOW TO RUN IT
--  -------------
--  Supabase dashboard -> SQL Editor -> paste this whole file -> Run.
--  Then run supabase/security_tests.sql the same way. It must end with
--  "ALL SECURITY TESTS PASSED" before any real student data is entered.
--
--  Safe to run again: every statement is create-if-missing, create-or-replace,
--  or drop-then-create. It runs as one transaction, so a failure part-way
--  leaves the database exactly as it was.
--
--  WHO CAN DO WHAT
--  ---------------
--    anyone with the page   only the functions marked "anon" below. No table is
--    (the public anon key)  readable or writable directly - every table has RLS
--                           on, anon holds no table privileges, and the secrets
--                           live in a schema the API does not expose at all.
--
--    a student device       only its OWN record, and only by proving it holds a
--                           session token for that student. Tokens are stored as
--                           SHA-256 hashes, never in plain text.
--
--    a signed-in teacher    their own classrooms and everything in them, through
--                           the REST API under row-level security, plus three
--                           teacher-only functions. Never another teacher's room.
--
--  THE THREE THINGS THIS FILE FIXES, BY DESIGN
--  -------------------------------------------
--  1. A class code used to be enough to become any child.
--     "Tap your name" and "type your name + pick your picture" both handed the
--     existing child's token - and with it their whole emotional history - to
--     anyone holding the class code. Class codes are shared by design, and with
--     15 pictures to choose from, a classmate's first name is at most 15
--     guesses. Now a returning child picks up their record only on a device
--     that proves it: either it holds that child's session token (the device
--     they used before), or a teacher has marked it a trusted classroom device.
--     Anyone else with the code can only join as a NEW student.
--     This is what the privacy policy already promises: "students reach only
--     their own records".
--
--  2. Consent records could be forged.
--     bs_record_consent took no secret, so anyone with a class code could write
--     a consent row for any child. It now requires that child's session token,
--     and records the name on file rather than whatever name was sent.
--
--  3. A device with a wrong clock could silence the alert view.
--     The analysis anchors its window to the latest check-in, so one check-in
--     dated next year hid every real one. Both analysis engines now ignore such
--     rows; this file also refuses to store them in the first place.
--
--  Two more, found while writing this: saves now MERGE check-ins instead of
--  overwriting them (a child signed in on two tablets used to lose whichever
--  device saved first), and every input is validated and size-capped.
--
--  UPGRADING AN OLDER INSTALL
--  --------------------------
--  A project that ran the earlier schema (v3, which this replaces) is brought
--  up to date by section 1b, in place, keeping every child's record. Read
--  DEPLOY.md, at the top of the repository, first: the upgrade signs every
--  child out once, and the app and the database have to change on the same day.
-- =============================================================================

begin;

-- ---------------------------------------------------------------------------
-- 0. A schema the API does not expose
-- ---------------------------------------------------------------------------
-- Supabase publishes everything in `public` over its REST API. Secrets and
-- internal helpers go in `private`, which it does not, so nothing here can be
-- reached by URL no matter what grants it has.
create schema if not exists private;
revoke all on schema private from public;


-- ---------------------------------------------------------------------------
-- 1. Tables the app uses (public, behind row-level security)
-- ---------------------------------------------------------------------------

create table if not exists public.classrooms (
  code          text primary key check (code ~ '^[A-Z0-9]{6}$'),
  teacher_id    uuid not null default auth.uid()
                references auth.users (id) on delete cascade,
  name          text not null check (char_length(name) between 1 and 80),
  teacher_name  text check (char_length(teacher_name) <= 80),
  school_name   text check (char_length(school_name) <= 120),
  zip           text check (char_length(zip) <= 10),
  token_reward  text check (char_length(token_reward) <= 80),
  token_goal    integer not null default 5 check (token_goal between 1 and 10),
  -- Exactly the two choices the app's privacy picker offers.
  name_mode     text not null default 'full' check (name_mode in ('full', 'id')),
  schedule_json jsonb not null default '[]'::jsonb
                check (jsonb_typeof(schedule_json) = 'array'),
  -- Off by default: the roster is a list of children's names.
  roster_pick   boolean not null default false,
  created_at    timestamptz not null default now()
);

create table if not exists public.students (
  id             text primary key check (id ~ '^[A-Za-z0-9_-]{4,64}$'),
  classroom_code text not null references public.classrooms (code) on delete cascade,
  name           text not null check (char_length(name) between 1 and 30),
  avatar         text not null check (char_length(avatar) between 1 and 16),
  stars          integer not null default 0 check (stars >= 0),
  coins          integer not null default 0 check (coins >= 0),
  tokens         integer not null default 0 check (tokens >= 0),
  badges         jsonb not null default '[]'::jsonb check (jsonb_typeof(badges) = 'array'),
  emotion_log    jsonb not null default '[]'::jsonb check (jsonb_typeof(emotion_log) = 'array'),
  task_log       jsonb not null default '[]'::jsonb check (jsonb_typeof(task_log) = 'array'),
  schedule_state jsonb not null default '[]'::jsonb check (jsonb_typeof(schedule_state) = 'array'),
  aac_count      integer not null default 0 check (aac_count >= 0),
  emo_count      integer not null default 0 check (emo_count >= 0),
  sentence_count integer not null default 0 check (sentence_count >= 0),
  stories_read   integer not null default 0 check (stories_read >= 0),
  calm_done      jsonb not null default '[]'::jsonb check (jsonb_typeof(calm_done) = 'array'),
  streak         integer not null default 0 check (streak >= 0),
  last_active    text check (char_length(last_active) <= 40),
  last_seen      timestamptz not null default now(),
  joined_at      timestamptz not null default now(),
  -- Lets notes and consent reference (student, classroom) together, so a row
  -- can never claim a student who is in a different classroom.
  unique (id, classroom_code)
);
-- Note what is NOT here: no token column. A teacher's dashboard reads
-- `students` with select=*, so a secret stored in this table would be sent to
-- every teacher's browser. Sessions live in private.student_sessions instead.

create table if not exists public.notes (
  id             uuid primary key default gen_random_uuid(),
  classroom_code text not null,
  student_id     text not null,
  text           text not null check (char_length(text) between 1 and 4000),
  created_at     timestamptz not null default now(),
  foreign key (student_id, classroom_code)
    references public.students (id, classroom_code) on delete cascade
);

create table if not exists public.stories (
  id             text primary key check (id ~ '^[A-Za-z0-9_-]{1,64}$'),
  classroom_code text not null references public.classrooms (code) on delete cascade,
  title          text not null check (char_length(title) between 1 and 120),
  emoji          text check (char_length(emoji) <= 16),
  pages          jsonb not null default '[]'::jsonb check (jsonb_typeof(pages) = 'array'),
  created_at     timestamptz not null default now()
);

create table if not exists public.consent (
  id             uuid primary key default gen_random_uuid(),
  classroom_code text not null,
  student_id     text not null,
  student_name   text not null,
  method         text not null check (method ~ '^[a-z_]{1,40}$'),
  detail         text check (char_length(detail) <= 300),
  recorded_via   text not null default 'student_session',
  created_at     timestamptz not null default now(),
  foreign key (student_id, classroom_code)
    references public.students (id, classroom_code) on delete cascade
);

create table if not exists public.audit_log (
  id             bigint generated always as identity primary key,
  classroom_code text not null references public.classrooms (code) on delete cascade,
  actor          text not null check (char_length(actor) <= 80),
  action         text not null check (action ~ '^[a-z_]{1,40}$'),
  detail         text check (char_length(detail) <= 300),
  created_at     timestamptz not null default now()
);


-- ---------------------------------------------------------------------------
-- 1b. Bringing a database made by the earlier schema up to date
-- ---------------------------------------------------------------------------
-- Until October 2026 the app shipped an earlier schema ("v3"). A project that
-- ran it still has its tables, and `create table if not exists` above leaves
-- them exactly as they were. This block converts them. It runs on every
-- install, and on a database that is already current each step finds nothing
-- to do. supabase/tests/run_local.py checks both: it upgrades a copy of the v3
-- schema holding sample data and requires the result to match a fresh install.
--
-- What changes, and why:
--   * Every policy on the six tables is dropped; section 5 creates the current
--     ones. Postgres ORs policies together, so one old permissive policy left
--     behind would quietly widen everything section 5 narrows.
--   * Every bs_* function is dropped; section 7 creates the current ones. The
--     old ones took VARCHAR and UUID arguments, which makes them different
--     functions to Postgres. They would survive beside the new ones, and the
--     old bs_join_student hands a child's record to anyone who knows the
--     child's name and picture.
--   * students.access_token is dropped. Those tokens were handed out by that
--     same old function, so none of them can be trusted, and they sat in a
--     column every teacher's dashboard reads. Every child signs in again once,
--     and the app tells them so.
--   * Deleting a teacher's account now deletes their classrooms. It used to
--     leave them behind, owned by nobody, with the children's records inside.
--   * Name mode 'initial', which the app no longer offers, becomes 'id', the
--     more private of the two it does offer.
--   * Consent rows written before the upgrade are marked
--     recorded_via = 'legacy_unverified'. The old function let anyone with a
--     class code write one, so on their own they are not evidence of consent.
--   * Column types the functions rely on: badges and calm_done become JSON
--     lists, last_active becomes text, and codes become text.
--
-- Apart from the old tokens, nothing is deleted: no record, check-in, note,
-- story, consent row or access-log entry. A row that breaks a rule the new
-- tables enforce is kept, the rule then applies to new rows only, and a
-- NOTICE names the rule.
do $upgrade$
declare
  r      record;
  v_next bigint;
begin
  -- Old policies, whatever they were called.
  for r in select p.policyname, p.tablename from pg_policies p
            where p.schemaname = 'public'
              and p.tablename in ('classrooms', 'students', 'notes', 'stories', 'consent', 'audit_log')
  loop
    execute format('drop policy %I on public.%I', r.policyname, r.tablename);
  end loop;

  -- Old functions, whatever their arguments. No CASCADE: the policies that
  -- called bs_owns() are gone already, and anything else that depends on one
  -- of these should stop the script rather than silently vanish with it.
  for r in select p.oid::regprocedure::text as sig
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.proname like 'bs\_%'
  loop
    execute 'drop function ' || r.sig;
  end loop;

  -- The usage report reads classrooms.zip, whose type may change below.
  -- Section 3b creates it again.
  drop view if exists public.usage_by_zip;

  -- The v3 indexes, each a duplicate of one in section 3.
  drop index if exists public.idx_students_classroom, public.idx_classrooms_teacher,
                       public.idx_stories_classroom, public.idx_notes_lookup,
                       public.idx_consent_classroom, public.idx_audit_classroom;

  alter table public.students drop column if exists access_token;

  -- Types. VARCHAR(n) becomes text; badges and calm_done go from text[] to a
  -- JSON list; last_active from a date to the 'YYYY-MM-DD' text the app sends.
  for r in select c.table_name, c.column_name from information_schema.columns c
            where c.table_schema = 'public' and c.data_type = 'character varying'
              and c.table_name in ('classrooms', 'students', 'notes', 'stories', 'consent', 'audit_log')
  loop
    execute format('alter table public.%I alter column %I type text', r.table_name, r.column_name);
  end loop;

  for r in select c.column_name from information_schema.columns c
            where c.table_schema = 'public' and c.table_name = 'students'
              and c.column_name in ('badges', 'calm_done') and c.data_type = 'ARRAY'
  loop
    execute format('alter table public.students alter column %I drop default', r.column_name);
    execute format('alter table public.students alter column %1$I type jsonb '
                   'using coalesce(to_jsonb(%1$I), ''[]''::jsonb)', r.column_name);
  end loop;

  if exists (select 1 from information_schema.columns c
              where c.table_schema = 'public' and c.table_name = 'students'
                and c.column_name = 'last_active' and c.data_type = 'date') then
    alter table public.students alter column last_active type text
      using to_char(last_active, 'YYYY-MM-DD');
  end if;

  -- The access log's ids: a serial the client could set, and whose sequence
  -- v3 granted to every signed-in user, becomes an identity column that
  -- carries on numbering after the last existing entry.
  if exists (select 1 from information_schema.columns c
              where c.table_schema = 'public' and c.table_name = 'audit_log'
                and c.column_name = 'id' and c.is_identity = 'NO') then
    select coalesce(max(a.id), 0) + 1 into v_next from public.audit_log a;
    alter table public.audit_log alter column id drop default;
    drop sequence if exists public.audit_log_id_seq;
    execute format('alter table public.audit_log alter column id '
                   'add generated always as identity (start with %s)', v_next);
  end if;

  -- Values. JSON the app reads as a list: an SQL or JSON null becomes [], and
  -- any other value is wrapped in a list rather than thrown away.
  for r in select * from (values
             ('classrooms', 'schedule_json'), ('stories', 'pages'),
             ('students', 'badges'), ('students', 'emotion_log'), ('students', 'task_log'),
             ('students', 'schedule_state'), ('students', 'calm_done')) as t (tbl, col)
  loop
    execute format(
      'update public.%1$I set %2$I = case when %2$I is null or %2$I = ''null''::jsonb '
      'then ''[]''::jsonb else jsonb_build_array(%2$I) end '
      'where %2$I is null or jsonb_typeof(%2$I) <> ''array''', r.tbl, r.col);
  end loop;

  update public.classrooms
     set name_mode = case when name_mode is null then 'full' else 'id' end
   where name_mode is null or name_mode not in ('full', 'id');

  if not exists (select 1 from information_schema.columns c
                  where c.table_schema = 'public' and c.table_name = 'consent'
                    and c.column_name = 'recorded_via') then
    alter table public.consent add column recorded_via text;
    update public.consent set recorded_via = 'legacy_unverified';
  end if;

  -- Defaults, as section 1 declares them. (v3 also required a teacher name;
  -- section 1 does not.)
  alter table public.classrooms
    alter column teacher_name  drop not null,
    alter column teacher_id    set default auth.uid(),
    alter column token_reward  drop default,
    alter column token_goal    set default 5,
    alter column name_mode     set default 'full',
    alter column schedule_json set default '[]'::jsonb,
    alter column roster_pick   set default false,
    alter column created_at    set default now();
  alter table public.students
    alter column avatar         drop default,
    alter column stars          set default 0,
    alter column coins          set default 0,
    alter column tokens         set default 0,
    alter column badges         set default '[]'::jsonb,
    alter column emotion_log    set default '[]'::jsonb,
    alter column task_log       set default '[]'::jsonb,
    alter column schedule_state set default '[]'::jsonb,
    alter column aac_count      set default 0,
    alter column emo_count      set default 0,
    alter column sentence_count set default 0,
    alter column stories_read   set default 0,
    alter column calm_done      set default '[]'::jsonb,
    alter column streak         set default 0,
    alter column last_seen      set default now(),
    alter column joined_at      set default now();
  alter table public.notes
    alter column id         set default gen_random_uuid(),
    alter column created_at set default now();
  alter table public.stories
    alter column emoji      drop default,
    alter column pages      set default '[]'::jsonb,
    alter column created_at set default now();
  alter table public.consent
    alter column id           set default gen_random_uuid(),
    alter column recorded_via set default 'student_session',
    alter column created_at   set default now();
  alter table public.audit_log
    alter column created_at set default now();

  -- Required columns. A counter or flag with no value gets its default; a
  -- column with no sensible default (who the teacher is, when a row was made)
  -- is left optional if any existing row has no value, so that row is kept.
  for r in select * from (values
             ('classrooms', 'teacher_id', null), ('classrooms', 'token_goal', '5'),
             ('classrooms', 'name_mode', null), ('classrooms', 'schedule_json', null),
             ('classrooms', 'roster_pick', 'false'), ('classrooms', 'created_at', null),
             ('students', 'stars', '0'), ('students', 'coins', '0'), ('students', 'tokens', '0'),
             ('students', 'aac_count', '0'), ('students', 'emo_count', '0'),
             ('students', 'sentence_count', '0'), ('students', 'stories_read', '0'),
             ('students', 'streak', '0'), ('students', 'badges', null),
             ('students', 'emotion_log', null), ('students', 'task_log', null),
             ('students', 'schedule_state', null), ('students', 'calm_done', null),
             ('students', 'last_seen', null), ('students', 'joined_at', null),
             ('notes', 'created_at', null),
             ('stories', 'pages', null), ('stories', 'created_at', null),
             ('consent', 'student_id', null), ('consent', 'student_name', null),
             ('consent', 'recorded_via', null), ('consent', 'created_at', null),
             ('audit_log', 'classroom_code', null), ('audit_log', 'actor', null),
             ('audit_log', 'created_at', null)) as t (tbl, col, fill)
  loop
    if r.fill is not null then
      execute format('update public.%I set %I = %s where %I is null', r.tbl, r.col, r.fill, r.col);
    end if;
    begin
      execute format('alter table public.%I alter column %I set not null', r.tbl, r.col);
    exception when not_null_violation then
      raise notice 'upgrade: some %.% values are empty, so the column stays optional to keep those rows',
        r.tbl, r.col;
    end;
  end loop;

  -- Deleting a teacher deletes their classrooms (v3: ON DELETE SET NULL).
  for r in select c.conname from pg_constraint c
            where c.conrelid = 'public.classrooms'::regclass and c.contype = 'f'
              and c.confrelid = 'auth.users'::regclass and c.confdeltype <> 'c'
  loop
    execute format('alter table public.classrooms drop constraint %I', r.conname);
  end loop;
  if not exists (select 1 from pg_constraint c
                  where c.conrelid = 'public.classrooms'::regclass and c.contype = 'f'
                    and c.confrelid = 'auth.users'::regclass) then
    alter table public.classrooms add constraint classrooms_teacher_id_fkey
      foreign key (teacher_id) references auth.users (id) on delete cascade;
  end if;

  -- Notes and consent name a (student, classroom) pair, so neither can claim a
  -- student who is in another class. That needs this key, and replaces v3's
  -- single-column link from notes to students.
  if not exists (select 1 from pg_constraint c
                  where c.conrelid = 'public.students'::regclass
                    and c.conname = 'students_id_classroom_code_key') then
    alter table public.students
      add constraint students_id_classroom_code_key unique (id, classroom_code);
  end if;
  for r in select c.conname from pg_constraint c
            where c.conrelid = 'public.notes'::regclass and c.contype = 'f'
              and c.confrelid = 'public.students'::regclass and cardinality(c.conkey) = 1
  loop
    execute format('alter table public.notes drop constraint %I', r.conname);
  end loop;

  -- Every rule section 1 declares, under the name Postgres gives it there.
  -- Added NOT VALID, then validated: rows that already break a rule are kept.
  for r in select * from (values
      ('classrooms', 'classrooms_code_check',          $c$check (code ~ '^[A-Z0-9]{6}$')$c$),
      ('classrooms', 'classrooms_name_check',          'check (char_length(name) between 1 and 80)'),
      ('classrooms', 'classrooms_teacher_name_check',  'check (char_length(teacher_name) <= 80)'),
      ('classrooms', 'classrooms_school_name_check',   'check (char_length(school_name) <= 120)'),
      ('classrooms', 'classrooms_zip_check',           'check (char_length(zip) <= 10)'),
      ('classrooms', 'classrooms_token_reward_check',  'check (char_length(token_reward) <= 80)'),
      ('classrooms', 'classrooms_token_goal_check',    'check (token_goal between 1 and 10)'),
      ('classrooms', 'classrooms_name_mode_check',     $c$check (name_mode in ('full', 'id'))$c$),
      ('classrooms', 'classrooms_schedule_json_check', $c$check (jsonb_typeof(schedule_json) = 'array')$c$),
      ('students', 'students_id_check',             $c$check (id ~ '^[A-Za-z0-9_-]{4,64}$')$c$),
      ('students', 'students_name_check',           'check (char_length(name) between 1 and 30)'),
      ('students', 'students_avatar_check',         'check (char_length(avatar) between 1 and 16)'),
      ('students', 'students_stars_check',          'check (stars >= 0)'),
      ('students', 'students_coins_check',          'check (coins >= 0)'),
      ('students', 'students_tokens_check',         'check (tokens >= 0)'),
      ('students', 'students_badges_check',         $c$check (jsonb_typeof(badges) = 'array')$c$),
      ('students', 'students_emotion_log_check',    $c$check (jsonb_typeof(emotion_log) = 'array')$c$),
      ('students', 'students_task_log_check',       $c$check (jsonb_typeof(task_log) = 'array')$c$),
      ('students', 'students_schedule_state_check', $c$check (jsonb_typeof(schedule_state) = 'array')$c$),
      ('students', 'students_aac_count_check',      'check (aac_count >= 0)'),
      ('students', 'students_emo_count_check',      'check (emo_count >= 0)'),
      ('students', 'students_sentence_count_check', 'check (sentence_count >= 0)'),
      ('students', 'students_stories_read_check',   'check (stories_read >= 0)'),
      ('students', 'students_calm_done_check',      $c$check (jsonb_typeof(calm_done) = 'array')$c$),
      ('students', 'students_streak_check',         'check (streak >= 0)'),
      ('students', 'students_last_active_check',    'check (char_length(last_active) <= 40)'),
      ('notes', 'notes_text_check', 'check (char_length(text) between 1 and 4000)'),
      ('notes', 'notes_student_id_classroom_code_fkey',
       'foreign key (student_id, classroom_code) references public.students (id, classroom_code) on delete cascade'),
      ('stories', 'stories_id_check',    $c$check (id ~ '^[A-Za-z0-9_-]{1,64}$')$c$),
      ('stories', 'stories_title_check', 'check (char_length(title) between 1 and 120)'),
      ('stories', 'stories_emoji_check', 'check (char_length(emoji) <= 16)'),
      ('stories', 'stories_pages_check', $c$check (jsonb_typeof(pages) = 'array')$c$),
      ('consent', 'consent_method_check', $c$check (method ~ '^[a-z_]{1,40}$')$c$),
      ('consent', 'consent_detail_check', 'check (char_length(detail) <= 300)'),
      ('consent', 'consent_student_id_classroom_code_fkey',
       'foreign key (student_id, classroom_code) references public.students (id, classroom_code) on delete cascade'),
      ('audit_log', 'audit_log_actor_check',  'check (char_length(actor) <= 80)'),
      ('audit_log', 'audit_log_action_check', $c$check (action ~ '^[a-z_]{1,40}$')$c$),
      ('audit_log', 'audit_log_detail_check', 'check (char_length(detail) <= 300)'),
      ('audit_log', 'audit_log_classroom_code_fkey',
       'foreign key (classroom_code) references public.classrooms (code) on delete cascade')
    ) as t (tbl, name, def)
  loop
    if not exists (select 1 from pg_constraint c
                    where c.conrelid = format('public.%I', r.tbl)::regclass and c.conname = r.name) then
      execute format('alter table public.%I add constraint %I %s not valid', r.tbl, r.name, r.def);
      begin
        execute format('alter table public.%I validate constraint %I', r.tbl, r.name);
      exception when check_violation or foreign_key_violation then
        raise notice 'upgrade: some existing % rows break %; they are kept, and the rule applies to new rows',
          r.tbl, r.name;
      end;
    end if;
  end loop;
end
$upgrade$;


-- ---------------------------------------------------------------------------
-- 2. Secrets (private - not reachable through the API)
-- ---------------------------------------------------------------------------

-- One row per signed-in student device. Only the SHA-256 of the token is kept:
-- a leaked table or backup does not let anyone sign in as a child.
create table if not exists private.student_sessions (
  token_hash   text primary key check (token_hash ~ '^[0-9a-f]{64}$'),
  student_id   text not null references public.students (id) on delete cascade,
  created_via  text not null check (created_via in ('join', 'device')),
  created_at   timestamptz not null default now(),
  last_used_at timestamptz not null default now()
);

-- Classroom tablets a teacher has marked as trusted. On a trusted device a
-- child may pick up their record by tapping their name; anywhere else they
-- need their own session token.
create table if not exists private.classroom_devices (
  secret_hash    text primary key check (secret_hash ~ '^[0-9a-f]{64}$'),
  classroom_code text not null references public.classrooms (code) on delete cascade,
  label          text check (char_length(label) <= 60),
  created_at     timestamptz not null default now(),
  revoked_at     timestamptz
);


-- ---------------------------------------------------------------------------
-- 3. Indexes - every lookup the app and the functions below make
-- ---------------------------------------------------------------------------
create index if not exists classrooms_teacher_idx   on public.classrooms (teacher_id);
create index if not exists students_classroom_idx   on public.students (classroom_code, joined_at);
create index if not exists students_name_avatar_idx on public.students (classroom_code, lower(name), avatar);
create index if not exists notes_room_student_idx   on public.notes (classroom_code, student_id, created_at);
create index if not exists stories_classroom_idx    on public.stories (classroom_code);
create index if not exists consent_classroom_idx    on public.consent (classroom_code, created_at desc);
create index if not exists audit_classroom_idx      on public.audit_log (classroom_code, created_at desc);
create index if not exists sessions_student_idx     on private.student_sessions (student_id);
create index if not exists devices_classroom_idx    on private.classroom_devices (classroom_code)
  where revoked_at is null;


-- ---------------------------------------------------------------------------
-- 3b. The project owner's usage report
-- ---------------------------------------------------------------------------
-- Classrooms counted by ZIP code, so regional use can be reported without
-- reading any classroom or child. It is for the SQL Editor only: section 4
-- takes it away from the API roles. security_invoker is a second lock - if it
-- were ever granted to a teacher, they would count only their own rooms.
--
--   select * from public.usage_by_zip;
create view public.usage_by_zip with (security_invoker = true) as
  select c.zip,
         count(*)                      as classrooms,
         count(distinct c.school_name) as schools,
         min(c.created_at)::date       as first_signup
    from public.classrooms c
   where c.zip is not null and c.zip <> ''
   group by c.zip
   order by classrooms desc;


-- ---------------------------------------------------------------------------
-- 4. Table privileges
-- ---------------------------------------------------------------------------
-- Supabase grants ALL on every new public table to anon and authenticated by
-- default, and relies on RLS alone. Here the grants are cut back as well, so a
-- table that ever lost its policies would fail closed rather than open.

revoke all on public.classrooms, public.students, public.notes, public.stories,
              public.consent, public.audit_log
  from anon, authenticated;

grant select, insert, delete on public.classrooms to authenticated;
-- Column-level: a teacher may edit a classroom's settings, but never its code
-- or its owner.
grant update (name, teacher_name, school_name, zip, token_reward, token_goal,
              name_mode, schedule_json, roster_pick)
  on public.classrooms to authenticated;
grant select, delete         on public.students  to authenticated;
grant select, insert, delete on public.notes     to authenticated;
grant select, insert, delete on public.stories   to authenticated;
grant select                 on public.consent   to authenticated;
grant select, insert         on public.audit_log to authenticated;

revoke all on private.student_sessions, private.classroom_devices
  from public, anon, authenticated;

revoke all on public.usage_by_zip from public, anon, authenticated;

-- No API role needs a sequence. The only one left backs the access log's
-- identity column, which an insert fills in without any privilege on it; v3
-- granted every sequence to every signed-in user.
revoke all on all sequences in schema public from anon, authenticated;


-- ---------------------------------------------------------------------------
-- 5. Row-level security: a teacher reaches only their own classrooms
-- ---------------------------------------------------------------------------
-- `(select auth.uid())` rather than `auth.uid()`: Postgres evaluates it once
-- per statement instead of once per row, which is Supabase's own advice.
--
-- Every table a teacher writes to also has a SELECT policy. The app sends
-- `Prefer: return=representation`, so an insert must be able to read back the
-- row it wrote - without that policy every insert would be refused.

alter table public.classrooms enable row level security;
alter table public.students   enable row level security;
alter table public.notes      enable row level security;
alter table public.stories    enable row level security;
alter table public.consent    enable row level security;
alter table public.audit_log  enable row level security;
alter table private.student_sessions  enable row level security;   -- no policies:
alter table private.classroom_devices enable row level security;   -- functions only

drop policy if exists classrooms_select on public.classrooms;
drop policy if exists classrooms_insert on public.classrooms;
drop policy if exists classrooms_update on public.classrooms;
drop policy if exists classrooms_delete on public.classrooms;
create policy classrooms_select on public.classrooms for select to authenticated
  using (teacher_id = (select auth.uid()));
create policy classrooms_insert on public.classrooms for insert to authenticated
  with check (teacher_id = (select auth.uid()));
create policy classrooms_update on public.classrooms for update to authenticated
  using (teacher_id = (select auth.uid()))
  with check (teacher_id = (select auth.uid()));
create policy classrooms_delete on public.classrooms for delete to authenticated
  using (teacher_id = (select auth.uid()));

drop policy if exists students_select on public.students;
drop policy if exists students_delete on public.students;
create policy students_select on public.students for select to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = students.classroom_code
                    and c.teacher_id = (select auth.uid())));
create policy students_delete on public.students for delete to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = students.classroom_code
                    and c.teacher_id = (select auth.uid())));

drop policy if exists notes_select on public.notes;
drop policy if exists notes_insert on public.notes;
drop policy if exists notes_delete on public.notes;
create policy notes_select on public.notes for select to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = notes.classroom_code
                    and c.teacher_id = (select auth.uid())));
create policy notes_insert on public.notes for insert to authenticated
  with check (exists (select 1 from public.classrooms c
                       where c.code = notes.classroom_code
                         and c.teacher_id = (select auth.uid())));
create policy notes_delete on public.notes for delete to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = notes.classroom_code
                    and c.teacher_id = (select auth.uid())));

drop policy if exists stories_select on public.stories;
drop policy if exists stories_insert on public.stories;
drop policy if exists stories_delete on public.stories;
create policy stories_select on public.stories for select to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = stories.classroom_code
                    and c.teacher_id = (select auth.uid())));
create policy stories_insert on public.stories for insert to authenticated
  with check (exists (select 1 from public.classrooms c
                       where c.code = stories.classroom_code
                         and c.teacher_id = (select auth.uid())));
create policy stories_delete on public.stories for delete to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = stories.classroom_code
                    and c.teacher_id = (select auth.uid())));

-- Consent is written only by bs_record_consent, and never edited or deleted
-- except when the student or classroom it belongs to is deleted.
drop policy if exists consent_select on public.consent;
create policy consent_select on public.consent for select to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = consent.classroom_code
                    and c.teacher_id = (select auth.uid())));

-- The access log is append-only: no UPDATE or DELETE grant exists at all.
drop policy if exists audit_select on public.audit_log;
drop policy if exists audit_insert on public.audit_log;
create policy audit_select on public.audit_log for select to authenticated
  using (exists (select 1 from public.classrooms c
                  where c.code = audit_log.classroom_code
                    and c.teacher_id = (select auth.uid())));
create policy audit_insert on public.audit_log for insert to authenticated
  with check (exists (select 1 from public.classrooms c
                       where c.code = audit_log.classroom_code
                         and c.teacher_id = (select auth.uid())));


-- ---------------------------------------------------------------------------
-- 6. Private helpers
-- ---------------------------------------------------------------------------
-- All of these run inside the security-definer functions in section 7. None is
-- callable by anon or authenticated (see the revoke at the end of the file).

-- 64 hex characters from two random UUIDs: 244 bits from Postgres's
-- cryptographic random source, with no extension required.
create or replace function private.new_secret()
returns text language sql volatile set search_path = '' as $$
  select replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '')
$$;

create or replace function private.hash_secret(p_secret text)
returns text language sql immutable set search_path = '' as $$
  select encode(sha256(convert_to(coalesce(p_secret, ''), 'UTF8')), 'hex')
$$;

-- Returns p_id when p_token is a live session for that student, else null.
-- Also notes when the session was last used.
create or replace function private.session_student(p_id text, p_token text)
returns text language plpgsql volatile set search_path = '' as $$
begin
  if p_id is null or p_token is null or p_token !~ '^[0-9a-f]{64}$' then
    return null;
  end if;
  update private.student_sessions s
     set last_used_at = now()
   where s.token_hash = private.hash_secret(p_token)
     and s.student_id = p_id;
  return case when found then p_id end;
end $$;

create or replace function private.device_trusted(p_code text, p_device text)
returns boolean language sql stable set search_path = '' as $$
  select p_device is not null
     and p_device ~ '^[0-9a-f]{64}$'
     and exists (select 1 from private.classroom_devices d
                  where d.classroom_code = p_code
                    and d.secret_hash = private.hash_secret(p_device)
                    and d.revoked_at is null)
$$;

create or replace function private.start_session(p_student_id text, p_via text)
returns text language plpgsql volatile set search_path = '' as $$
declare
  v_token text := private.new_secret();
begin
  insert into private.student_sessions (token_hash, student_id, created_via)
  values (private.hash_secret(v_token), p_student_id, p_via);
  return v_token;
end $$;

-- When a logged row happened, mirroring parse_timestamp() in
-- synthetic_data/focusbridge/core/timeline.py and parseTimestamp() in
-- integrated_version/fb-insights.js, so the database agrees with both engines
-- about which rows are readable:
--   {"ts": "2026-08-07T09:12:00"}          the corpus; an offset is dropped
--   {"iso": "2026-08-07", "time": "9:12 AM"} the app; a bad clock -> midnight
-- A row with a `ts` that does not parse is unreadable - it does not fall back
-- to `iso`, exactly as the engines behave. Returns null when unreadable.
create or replace function private.entry_at(p_entry jsonb)
returns timestamp language plpgsql immutable set search_path = '' as $$
declare
  v_ts    text := p_entry ->> 'ts';
  v_day   text := coalesce(nullif(p_entry ->> 'iso', ''), p_entry ->> 'date');
  v_clock text := p_entry ->> 'time';
  v_base  timestamp;
  v_parts text[];
  v_hour  integer;
  v_min   integer;
begin
  if v_ts is not null and v_ts <> '' then
    if v_ts !~ '^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}' then
      return null;
    end if;
    return left(replace(v_ts, 'T', ' '), 19)::timestamp;
  end if;

  if v_day is null or v_day !~ '^\d{4}-\d{2}-\d{2}' then
    return null;
  end if;
  v_base := left(v_day, 10)::date;

  -- U+202F (narrow no-break space) is what some platforms put before AM/PM.
  v_parts := regexp_match(
    upper(trim(translate(coalesce(v_clock, ''), U&'\202F\00A0', '  '))),
    '^(\d{1,2}):(\d{1,2})(?::\d{1,2})?\s*(AM|PM)?$');
  if v_parts is null then
    return v_base;
  end if;
  v_hour := v_parts[1]::integer;
  v_min  := v_parts[2]::integer;
  if v_parts[3] = 'PM' and v_hour <> 12 then v_hour := v_hour + 12; end if;
  if v_parts[3] = 'AM' and v_hour = 12 then v_hour := 0; end if;
  if v_hour > 23 or v_min > 59 then
    return v_base;
  end if;
  return v_base + make_interval(hours => v_hour, mins => v_min);
exception when others then
  return null;    -- e.g. 2026-02-31: unreadable, not an error
end $$;

-- Check-in / task rows, cleaned before they are stored:
--   * only objects, only the keys the app writes, only short strings;
--   * rows whose time cannot be read are dropped (no engine can place them);
--   * rows dated more than a day in the future are dropped - the same
--     tolerance as FUTURE_TOLERANCE in both engines, measured against UTC so a
--     device in any real timezone is inside it.
-- Rows with an UNRECOGNISED emotion are kept on purpose: the engines keep them
-- so they break a run rather than letting the check-ins either side join up.
create or replace function private.clean_entries(p_entries jsonb, p_keys text[])
returns jsonb language sql stable set search_path = '' as $$
  select coalesce(jsonb_agg(c.entry order by c.at, c.entry::text), '[]'::jsonb)
    from (select e.entry, private.entry_at(e.entry) as at
            from (select (select jsonb_object_agg(k.key, left(k.value #>> '{}', 120))
                            from jsonb_each(r.value) k
                           where k.key = any (p_keys)
                             and jsonb_typeof(k.value) = 'string') as entry
                    from jsonb_array_elements(
                           case when jsonb_typeof(p_entries) = 'array'
                                then p_entries else '[]'::jsonb end) r
                   where jsonb_typeof(r.value) = 'object') e
           where e.entry is not null) c
   where c.at is not null
     and c.at <= (now() at time zone 'utc') + interval '1 day'
$$;

-- Merge two logs without losing anything either side added.
-- A save used to REPLACE the whole log with the device's copy, so a child
-- signed in on two tablets lost whichever tablet's check-ins were saved first
-- - silently, and in a way that can hide a streak. Logs are append-only, so
-- the right merge keeps each distinct row as many times as EITHER copy has it
-- (the "max multiplicity" union): nothing a device appended is lost, and a
-- device re-sending rows it already saved does not duplicate them.
-- Kept in time order, newest p_cap rows.
create or replace function private.merge_entries(p_stored jsonb, p_incoming jsonb, p_cap integer)
returns jsonb language sql stable set search_path = '' as $$
  with rows as (
         select value as entry, 1 as side from jsonb_array_elements(coalesce(p_stored, '[]'::jsonb))
         union all
         select value, 2 from jsonb_array_elements(coalesce(p_incoming, '[]'::jsonb))),
       numbered as (
         select entry, row_number() over (partition by side, entry) as copy from rows),
       merged as (
         select distinct entry, copy from numbered),
       newest as (
         select entry, private.entry_at(entry) as at from merged
          order by private.entry_at(entry) desc nulls last, entry::text desc
          limit p_cap)
  select coalesce(jsonb_agg(entry order by at, entry::text), '[]'::jsonb) from newest
$$;

-- Union of two lists of short strings (badges, calm-corner tools): sets that
-- only ever grow, so a union is the lossless merge.
create or replace function private.merge_strings(p_stored jsonb, p_incoming jsonb, p_cap integer)
returns jsonb language sql stable set search_path = '' as $$
  select coalesce(jsonb_agg(v order by first_seen), '[]'::jsonb)
    from (select v, min(ord) as first_seen
            from (select value #>> '{}' as v, ord
                    from jsonb_array_elements(coalesce(p_stored, '[]'::jsonb))
                         with ordinality as t (value, ord)
                   where jsonb_typeof(value) = 'string'
                  union all
                  select value #>> '{}', ord + 1000000
                    from jsonb_array_elements(case when jsonb_typeof(p_incoming) = 'array'
                                                   then p_incoming else '[]'::jsonb end)
                         with ordinality as t (value, ord)
                   where jsonb_typeof(value) = 'string') s
           where char_length(v) between 1 and 60
           group by v
           order by min(ord)
           limit p_cap) u
$$;

-- A counter from the payload, clamped to 0..1,000,000; the stored value if the
-- payload has no usable number.
create or replace function private.int_or(p_value jsonb, p_fallback integer)
returns integer language sql immutable set search_path = '' as $$
  select case when jsonb_typeof(p_value) = 'number'
              then least(greatest(floor((p_value #>> '{}')::numeric), 0), 1000000)::integer
              else p_fallback end
$$;

-- A schedule: an array of at most 200 objects and 50 KB, else null ("keep
-- what is stored"). Contents are not rewritten - task ids are numbers in some
-- builds and strings in others, and nothing here depends on either.
create or replace function private.clean_schedule(p_schedule jsonb)
returns jsonb language sql immutable set search_path = '' as $$
  select case when jsonb_typeof(p_schedule) = 'array'
               and jsonb_array_length(p_schedule) <= 200
               and octet_length(p_schedule::text) <= 50000
               and not exists (select 1 from jsonb_array_elements(p_schedule) e
                                where jsonb_typeof(e.value) <> 'object')
              then p_schedule end
$$;

-- A student as the app holds one in memory - the same camelCase keys as the
-- app's fromRow(), which is what StudentApp reads straight off this result.
create or replace function private.student_json(s public.students)
returns json language sql stable set search_path = '' as $$
  select json_build_object(
    'name', s.name, 'avatar', s.avatar,
    'stars', s.stars, 'coins', s.coins, 'tokens', s.tokens,
    'badges', s.badges, 'emotionLog', s.emotion_log, 'taskLog', s.task_log,
    'schedule', s.schedule_state,
    'aacCount', s.aac_count, 'emoCount', s.emo_count,
    'sentenceCount', s.sentence_count, 'storiesRead', s.stories_read,
    'calmDone', s.calm_done, 'streak', s.streak,
    'lastActive', coalesce(s.last_active, ''), 'lastSeen', s.last_seen)
$$;

-- The access log's actor is set by the server, not taken from the request:
-- a teacher's insert is recorded as that teacher whatever the client claimed.
create or replace function private.audit_set_actor()
returns trigger language plpgsql set search_path = '' as $$
begin
  if auth.uid() is not null then
    new.actor := 'teacher:' || auth.uid()::text;
  end if;
  return new;
end $$;

drop trigger if exists audit_set_actor on public.audit_log;
create trigger audit_set_actor before insert on public.audit_log
  for each row execute function private.audit_set_actor();


-- ---------------------------------------------------------------------------
-- 7. Functions the app calls
-- ---------------------------------------------------------------------------
-- Every one is SECURITY DEFINER with an empty search_path and fully qualified
-- names - the combination that stops a caller redirecting a name to an object
-- of their own. Each checks its own authority; none trusts the client's word
-- for which classroom or student a row belongs to.

-- Section 1b has already dropped every bs_* function, whatever its arguments,
-- so an old client cannot reach an old, insecure version of any of these.

-- [anon] The classroom details a student's device needs - nothing that
-- identifies the teacher. Used by testConnection() with a dummy code, so an
-- unknown code returns no rows rather than an error.
create or replace function public.bs_get_classroom(p_code text)
returns table (name text, token_reward text, token_goal integer, name_mode text,
               schedule_json jsonb, roster_pick boolean)
language sql stable security definer set search_path = '' as $$
  select c.name, c.token_reward, c.token_goal, c.name_mode, c.schedule_json, c.roster_pick
    from public.classrooms c
   where c.code = upper(trim(coalesce(p_code, '')))
$$;

-- [anon] The classroom's social stories.
create or replace function public.bs_get_stories(p_code text)
returns table (id text, title text, emoji text, pages jsonb, created_at timestamptz)
language sql stable security definer set search_path = '' as $$
  select st.id, st.title, st.emoji, st.pages, st.created_at
    from public.stories st
   where st.classroom_code = upper(trim(coalesce(p_code, '')))
   order by st.created_at
$$;

-- [anon] The tap-your-name list. Only when the teacher has switched it on AND
-- the asking device is one the teacher has trusted; otherwise an empty list,
-- because this list is children's names.
create or replace function public.bs_list_students(p_code text, p_device text default null)
returns table (id text, name text, avatar text)
language sql stable security definer set search_path = '' as $$
  select s.id, s.name, s.avatar
    from public.students s
    join public.classrooms c on c.code = s.classroom_code
   where c.code = upper(trim(coalesce(p_code, '')))
     and c.roster_pick
     and private.device_trusted(c.code, p_device)
   order by lower(s.name), s.id
$$;

-- [anon] Join a class, or come back to it. Returns {id, token, data}.
--
--   1. The device sends an id this class already has (the child was signed in
--      on this device before, or tapped their name on a trusted device).
--      Allowed with that child's own session token, or on a trusted device.
--   2. The name and picture match a child already in the class - "coming back
--      on a new device". Allowed only on a trusted device. Anywhere else this
--      is exactly the attack, so it is refused rather than either handing
--      over the record or quietly creating a duplicate child.
--   3. Otherwise a new student, which anyone with the class code may create.
create or replace function public.bs_join_student(
  p_code text, p_id text, p_name text, p_avatar text,
  p_schedule jsonb default '[]'::jsonb,
  p_token text default null, p_device text default null)
returns json language plpgsql volatile security definer set search_path = '' as $$
declare
  v_code    text := upper(trim(coalesce(p_code, '')));
  v_name    text := trim(coalesce(p_name, ''));
  v_avatar  text := trim(coalesce(p_avatar, ''));
  v_student public.students;
  v_token   text;
begin
  if not exists (select 1 from public.classrooms c where c.code = v_code) then
    raise exception 'That class code was not found.';
  end if;

  -- 1. a known id
  select * into v_student from public.students s where s.id = p_id;
  if found then
    if v_student.classroom_code <> v_code then
      raise exception 'Could not sign in to this class.';
    end if;
    if private.session_student(v_student.id, p_token) is not null then
      v_token := p_token;
    elsif private.device_trusted(v_code, p_device) then
      v_token := private.start_session(v_student.id, 'device');
    else
      raise exception 'To sign back in on this device, ask your teacher to set it up for the class.';
    end if;
    return json_build_object('id', v_student.id, 'token', v_token,
                             'data', private.student_json(v_student));
  end if;

  if char_length(v_name) not between 1 and 30 or char_length(v_avatar) not between 1 and 16 then
    raise exception 'Please type a first name and pick a picture.';
  end if;
  if p_id is null or p_id !~ '^[A-Za-z0-9_-]{4,64}$' then
    raise exception 'Could not join: this app sent an unusable student id.';
  end if;

  -- 2. the same name and picture as someone already here
  select * into v_student from public.students s
   where s.classroom_code = v_code and lower(s.name) = lower(v_name) and s.avatar = v_avatar
   order by s.joined_at
   limit 1;
  if found then
    if private.device_trusted(v_code, p_device) then
      v_token := private.start_session(v_student.id, 'device');
      return json_build_object('id', v_student.id, 'token', v_token,
                               'data', private.student_json(v_student));
    end if;
    raise exception 'That name and picture are already in this class. If they are yours, ask your teacher to set up this device.';
  end if;

  -- 3. somebody new
  if (select count(*) from public.students s where s.classroom_code = v_code) >= 200 then
    raise exception 'This class is full. Ask your teacher.';
  end if;
  insert into public.students (id, classroom_code, name, avatar, schedule_state)
  values (p_id, v_code, v_name, v_avatar, coalesce(private.clean_schedule(p_schedule), '[]'::jsonb))
  returning * into v_student;
  v_token := private.start_session(v_student.id, 'join');
  return json_build_object('id', v_student.id, 'token', v_token,
                           'data', private.student_json(v_student));
end $$;

-- [anon] A student's own record, for the device holding their session token.
-- Null for anything else, which is what sends the app to its fallback path.
create or replace function public.bs_get_student(p_id text, p_token text)
returns json language plpgsql volatile security definer set search_path = '' as $$
declare
  v_student public.students;
begin
  if private.session_student(p_id, p_token) is null then
    return null;
  end if;
  select * into v_student from public.students s where s.id = p_id;
  return private.student_json(v_student);
end $$;

-- [anon] Save a student's progress. The app sends its whole in-memory record
-- (toRow); this takes from it only what a student may change about themselves:
--   * check-in and task logs are MERGED, never replaced (see merge_entries);
--   * badges and calm-corner tools are unioned;
--   * counters are clamped; the picture may change; the name may not;
--   * id and classroom_code in the payload are ignored - a student cannot move
--     their record into another class or onto another child's;
--   * last_seen is the server's clock, not the device's.
-- Raises on a bad session: the app counts any non-error reply as success.
create or replace function public.bs_save_student(p_id text, p_token text, p_payload jsonb)
returns boolean language plpgsql volatile security definer set search_path = '' as $$
declare
  c_emotion_keys constant text[] := array['emoji', 'label', 'emotion', 'time', 'date', 'iso', 'ts'];
  c_task_keys    constant text[] := array['emoji', 'task', 'status', 'time', 'date', 'iso', 'ts'];
begin
  if private.session_student(p_id, p_token) is null then
    raise exception 'Your sign-in has expired. Please sign in again.';
  end if;
  if p_payload is null or jsonb_typeof(p_payload) <> 'object' then
    raise exception 'Nothing to save.';
  end if;
  if octet_length(p_payload::text) > 5000000 then
    raise exception 'This save is too large.';
  end if;

  update public.students s set
    avatar         = case when char_length(trim(coalesce(p_payload ->> 'avatar', ''))) between 1 and 16
                          then trim(p_payload ->> 'avatar') else s.avatar end,
    stars          = private.int_or(p_payload -> 'stars', s.stars),
    coins          = private.int_or(p_payload -> 'coins', s.coins),
    tokens         = private.int_or(p_payload -> 'tokens', s.tokens),
    aac_count      = private.int_or(p_payload -> 'aac_count', s.aac_count),
    emo_count      = private.int_or(p_payload -> 'emo_count', s.emo_count),
    sentence_count = private.int_or(p_payload -> 'sentence_count', s.sentence_count),
    stories_read   = private.int_or(p_payload -> 'stories_read', s.stories_read),
    streak         = private.int_or(p_payload -> 'streak', s.streak),
    badges         = private.merge_strings(s.badges, p_payload -> 'badges', 200),
    calm_done      = private.merge_strings(s.calm_done, p_payload -> 'calm_done', 50),
    emotion_log    = private.merge_entries(
                       s.emotion_log,
                       private.clean_entries(p_payload -> 'emotion_log', c_emotion_keys),
                       20000),
    task_log       = private.merge_entries(
                       s.task_log,
                       private.clean_entries(p_payload -> 'task_log', c_task_keys),
                       20000),
    schedule_state = coalesce(private.clean_schedule(p_payload -> 'schedule_state'), s.schedule_state),
    last_active    = case when jsonb_typeof(p_payload -> 'last_active') = 'string'
                          then left(p_payload ->> 'last_active', 40) else s.last_active end,
    last_seen      = now()
  where s.id = p_id;
  return true;
end $$;

-- [anon] Record that a supervising adult confirmed consent when a child
-- joined. Requires that child's own session token: it used to accept any
-- student id from anyone holding the class code. The stored name is the one
-- on file, whatever name the request carried.
create or replace function public.bs_record_consent(
  p_code text, p_student_id text, p_name text, p_method text, p_detail text,
  p_token text default null)
returns boolean language plpgsql volatile security definer set search_path = '' as $$
declare
  v_code    text := upper(trim(coalesce(p_code, '')));
  v_student public.students;
begin
  if private.session_student(p_student_id, p_token) is null then
    raise exception 'Consent can only be recorded from the device the student just joined on.';
  end if;
  select * into v_student from public.students s
   where s.id = p_student_id and s.classroom_code = v_code;
  if not found then
    raise exception 'That student is not in this class.';
  end if;
  if coalesce(p_method, '') !~ '^[a-z_]{1,40}$' then
    raise exception 'Unknown consent method.';
  end if;

  insert into public.consent (classroom_code, student_id, student_name, method, detail, recorded_via)
  values (v_code, v_student.id, v_student.name, p_method, left(p_detail, 300), 'student_session');
  insert into public.audit_log (classroom_code, actor, action, detail)
  values (v_code, 'student', 'consent_recorded', right(v_student.id, 4));
  return true;
end $$;

-- [anon] A student (or their grown-up) deletes everything about them.
-- Notes, consent rows and every session go with the record.
create or replace function public.bs_self_delete(p_id text, p_token text)
returns boolean language plpgsql volatile security definer set search_path = '' as $$
declare
  v_code text;
begin
  if private.session_student(p_id, p_token) is null then
    raise exception 'Your sign-in has expired. Please sign in again.';
  end if;
  delete from public.students s where s.id = p_id returning s.classroom_code into v_code;
  insert into public.audit_log (classroom_code, actor, action, detail)
  values (v_code, 'student', 'student_self_deleted', right(p_id, 4));
  return true;
end $$;

-- [teacher] Mark this device as a trusted classroom device and return its
-- secret, which the app keeps in the device's own storage. Shown once; only
-- its hash is stored.
create or replace function public.bs_enroll_device(p_code text, p_label text default null)
returns json language plpgsql volatile security definer set search_path = '' as $$
declare
  v_code   text := upper(trim(coalesce(p_code, '')));
  v_secret text;
  v_label  text := nullif(left(trim(coalesce(p_label, '')), 60), '');
begin
  if auth.uid() is null then
    raise exception 'Sign in as the teacher first.';
  end if;
  if not exists (select 1 from public.classrooms c
                  where c.code = v_code and c.teacher_id = auth.uid()) then
    raise exception 'Only this class''s teacher can trust a device for it.';
  end if;
  if (select count(*) from private.classroom_devices d
       where d.classroom_code = v_code and d.revoked_at is null) >= 100 then
    raise exception 'This class already has 100 trusted devices. Stop trusting them all and start again.';
  end if;

  v_secret := private.new_secret();
  insert into private.classroom_devices (secret_hash, classroom_code, label)
  values (private.hash_secret(v_secret), v_code, v_label);
  insert into public.audit_log (classroom_code, actor, action, detail)
  values (v_code, 'teacher', 'device_trusted', v_label);
  return json_build_object('device_secret', v_secret);
end $$;

-- [teacher] Stop trusting every device for a class - for a lost tablet, or at
-- the end of a year. Also ends every sign-in those devices started, so a child
-- signed in on a revoked tablet is signed out there too.
create or replace function public.bs_revoke_devices(p_code text)
returns integer language plpgsql volatile security definer set search_path = '' as $$
declare
  v_code  text := upper(trim(coalesce(p_code, '')));
  v_count integer;
begin
  if auth.uid() is null then
    raise exception 'Sign in as the teacher first.';
  end if;
  if not exists (select 1 from public.classrooms c
                  where c.code = v_code and c.teacher_id = auth.uid()) then
    raise exception 'Only this class''s teacher can do that.';
  end if;

  update private.classroom_devices d set revoked_at = now()
   where d.classroom_code = v_code and d.revoked_at is null;
  get diagnostics v_count = row_count;

  delete from private.student_sessions ss
   using public.students s
   where ss.student_id = s.id
     and s.classroom_code = v_code
     and ss.created_via = 'device';

  insert into public.audit_log (classroom_code, actor, action, detail)
  values (v_code, 'teacher', 'devices_revoked', v_count::text);
  return v_count;
end $$;

-- [teacher] Delete the signed-in teacher's login and, by cascade, every
-- classroom they own and everything in them. One transaction: either all of
-- it goes or none of it does.
-- Needs the caller's identity, which is why the app now sends the teacher's
-- token for this call - it used to send only the public key, so the request
-- carried no identity and no correct server could ever have honoured it.
create or replace function public.bs_delete_my_account()
returns boolean language plpgsql volatile security definer set search_path = '' as $$
declare
  v_uid uuid := auth.uid();
begin
  if v_uid is null then
    raise exception 'Sign in as the teacher first.';
  end if;
  delete from auth.users u where u.id = v_uid;
  return true;
end $$;


-- ---------------------------------------------------------------------------
-- 8. Who may call what
-- ---------------------------------------------------------------------------
-- Postgres lets everyone execute a new function, and Supabase additionally
-- grants every new public function to anon. So: take it all away, then hand
-- back exactly what each audience needs.

revoke all on function
  public.bs_get_classroom(text),
  public.bs_get_stories(text),
  public.bs_list_students(text, text),
  public.bs_join_student(text, text, text, text, jsonb, text, text),
  public.bs_get_student(text, text),
  public.bs_save_student(text, text, jsonb),
  public.bs_record_consent(text, text, text, text, text, text),
  public.bs_self_delete(text, text),
  public.bs_enroll_device(text, text),
  public.bs_revoke_devices(text),
  public.bs_delete_my_account()
  from public, anon, authenticated;

grant execute on function
  public.bs_get_classroom(text),
  public.bs_get_stories(text),
  public.bs_list_students(text, text),
  public.bs_join_student(text, text, text, text, jsonb, text, text),
  public.bs_get_student(text, text),
  public.bs_save_student(text, text, jsonb),
  public.bs_record_consent(text, text, text, text, text, text),
  public.bs_self_delete(text, text)
  to anon, authenticated;

grant execute on function
  public.bs_enroll_device(text, text),
  public.bs_revoke_devices(text),
  public.bs_delete_my_account()
  to authenticated;

revoke all on all functions in schema private from public, anon, authenticated;

commit;
