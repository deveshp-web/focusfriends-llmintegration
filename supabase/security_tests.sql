-- =============================================================================
--  Focus Bridge — security tests for focus-bridge-schema-secure.sql
--  supabase/security_tests.sql
-- =============================================================================
--
--  HOW TO RUN
--    Supabase: SQL Editor -> paste this whole file -> Run.
--    Locally:  python supabase/tests/run_local.py
--
--  WHAT A PASS LOOKS LIKE
--    The run finishes without an error. Every test that fails stops the run
--    with "FAIL <id> <what went wrong>". (psql also prints "pass <id>" for each
--    test and a final "ALL n SECURITY TESTS PASSED"; Supabase's editor shows
--    only "Success. No rows returned".)
--
--  IT LEAVES NOTHING BEHIND
--    Everything runs in one transaction that ends in ROLLBACK, including the
--    two test teachers it creates in auth.users. Safe on a live project.
--
--  HOW IT TESTS
--    It switches between the three kinds of caller with SET LOCAL ROLE and a
--    JWT claims setting - the same mechanism Supabase's API uses - and calls
--    the functions and tables exactly as the app does:
--      anon            anyone holding the page and the public key
--      authenticated   a signed-in teacher (teacher A or teacher B)
--
--  Test ids: J join, L1/L2 the class-code loophole, E device trust,
--  C consent, S saves, R row-level security, G the public lookup,
--  T anon's reach, F the function and grant inventory, D deletion.
-- =============================================================================

begin;

-- ---------------------------------------------------------------------------
-- harness (rolled back with everything else)
-- ---------------------------------------------------------------------------
create schema fbtest;

create function fbtest.check(p_label text, p_ok boolean, p_detail text default '')
returns void language plpgsql as $$
begin
  if p_ok is not true then
    raise exception 'FAIL  %  %', p_label, p_detail;
  end if;
  perform set_config('fbtest.passed',
    (coalesce(nullif(current_setting('fbtest.passed', true), ''), '0')::integer + 1)::text, true);
  raise notice 'pass  %', p_label;
end $$;

-- Passes only if p_sql raises an error whose message contains p_expect - so a
-- statement refused for the WRONG reason (a typo, say) does not count.
create function fbtest.refused(p_label text, p_sql text, p_expect text)
returns void language plpgsql as $$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlerrm ilike '%' || p_expect || '%' then
      perform fbtest.check(p_label, true);
      return;
    end if;
    raise exception 'FAIL  %  refused, but with "%" rather than "%"', p_label, sqlerrm, p_expect;
  end;
  raise exception 'FAIL  %  it was allowed: %', p_label, p_sql;
end $$;

grant usage on schema fbtest to anon, authenticated;
grant execute on all functions in schema fbtest to anon, authenticated;
select set_config('fbtest.passed', '0', true);

-- ---------------------------------------------------------------------------
-- fixtures: two teachers, one classroom each, made the way the app makes them
-- ---------------------------------------------------------------------------
insert into auth.users (id, email) values
  ('aaaaaaaa-0000-4000-8000-00000000000a', 'fbtest-teacher-a@example.invalid'),
  ('bbbbbbbb-0000-4000-8000-00000000000b', 'fbtest-teacher-b@example.invalid');

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"aaaaaaaa-0000-4000-8000-00000000000a","role":"authenticated"}', true);
insert into public.classrooms (code, teacher_id, name, teacher_name, roster_pick)
values ('TESTAA', 'aaaaaaaa-0000-4000-8000-00000000000a', 'Room A', 'Teacher A', true);
select fbtest.refused('R1 a teacher cannot create a classroom in another teacher''s name',
  $q$insert into public.classrooms (code, teacher_id, name)
     values ('TESTXX', 'bbbbbbbb-0000-4000-8000-00000000000b', 'Forged')$q$,
  'row-level security');
reset role;

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"bbbbbbbb-0000-4000-8000-00000000000b","role":"authenticated"}', true);
insert into public.classrooms (code, teacher_id, name, teacher_name, roster_pick)
values ('TESTBB', 'bbbbbbbb-0000-4000-8000-00000000000b', 'Room B', 'Teacher B', true);
reset role;

-- ---------------------------------------------------------------------------
-- J: three children join, from anonymous devices
-- ---------------------------------------------------------------------------
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);

select set_config('fbtest.t1',
  public.bs_join_student('TESTAA', 'stu_ava_0001', 'Ava', '🐼') ->> 'token', true);
select set_config('fbtest.t2',
  public.bs_join_student('TESTAA', 'stu_bo_0002', 'Bo', '🦊') ->> 'token', true);
select set_config('fbtest.t3',
  public.bs_join_student('testbb', 'stu_cy_0003', 'Cy', '🐸') ->> 'token', true);

select fbtest.check('J1 joining hands back a 64-hex session token',
  current_setting('fbtest.t1') ~ '^[0-9a-f]{64}$');
select fbtest.check('J2 every child gets their own token',
  current_setting('fbtest.t1') <> current_setting('fbtest.t2'));
select fbtest.refused('J3 an unknown class code is refused',
  $q$select public.bs_join_student('NOPE00', 'stu_zed_0009', 'Zed', '🐙')$q$, 'not found');
select fbtest.refused('J4 a name and a picture are both required',
  $q$select public.bs_join_student('TESTAA', 'stu_zed_0009', '   ', '🐙')$q$, 'first name');

-- ---------------------------------------------------------------------------
-- T: an anonymous caller reaches nothing but the student functions
-- ---------------------------------------------------------------------------
select fbtest.refused('T1 anon cannot read students directly',
  'select * from public.students', 'permission denied');
select fbtest.refused('T2 anon cannot read classrooms directly',
  'select * from public.classrooms', 'permission denied');
select fbtest.refused('T3 anon cannot read notes', 'select * from public.notes', 'permission denied');
select fbtest.refused('T4 anon cannot read consent', 'select * from public.consent', 'permission denied');
select fbtest.refused('T5 anon cannot read the access log',
  'select * from public.audit_log', 'permission denied');
select fbtest.refused('T6 anon cannot write a student row directly',
  'update public.students set stars = 999', 'permission denied');
select fbtest.refused('T7 anon cannot reach the session secrets',
  'select * from private.student_sessions', 'permission denied');
select fbtest.refused('T8 anon cannot call the private helpers',
  $q$select private.hash_secret('x')$q$, 'permission denied');
select fbtest.refused('T9 anon cannot trust a device',
  $q$select public.bs_enroll_device('TESTAA')$q$, 'permission denied');
select fbtest.refused('T10 anon cannot delete an account',
  'select public.bs_delete_my_account()', 'permission denied');

-- ---------------------------------------------------------------------------
-- G: the public classroom lookup
-- ---------------------------------------------------------------------------
select fbtest.check('G1 the public classroom lookup reveals nothing about the teacher or school',
  (select array_agg(k order by k)
     from jsonb_object_keys((select to_jsonb(g) from public.bs_get_classroom('testaa') g)) k)
  = array['name', 'name_mode', 'roster_pick', 'schedule_json', 'token_goal', 'token_reward']);
select fbtest.check('G2 an unknown code returns no rows rather than an error',
  (select count(*) from public.bs_get_classroom('ZZZZZZ')) = 0);

-- ---------------------------------------------------------------------------
-- L1: a class code is no longer enough to become a child
-- ---------------------------------------------------------------------------
select fbtest.check('L1a with no trusted device the class list is empty, even with roster sign-in on',
  (select count(*) from public.bs_list_students('TESTAA')) = 0);
select fbtest.refused('L1b a class code plus a child''s id does not sign you in as them',
  $q$select public.bs_join_student('TESTAA', 'stu_ava_0001', 'Ava', '🐼')$q$,
  'ask your teacher to set it up');
select fbtest.refused('L1c nor does their name and picture, on a device the teacher has not trusted',
  $q$select public.bs_join_student('TESTAA', 'stu_mallory9', 'ava', '🐼')$q$,
  'already in this class');
select fbtest.refused('L1d another child''s token does not open this record',
  format($q$select public.bs_join_student('TESTAA', 'stu_ava_0001', 'Ava', '🐼', '[]', %L)$q$,
         current_setting('fbtest.t2')),
  'ask your teacher to set it up');
select fbtest.check('L1e the child''s own token signs them back in, keeping their session',
  public.bs_join_student('TESTAA', 'stu_ava_0001', 'Ava', '🐼', '[]',
                         current_setting('fbtest.t1')) ->> 'token' = current_setting('fbtest.t1'));
select fbtest.check('L1f a record is readable only with its own token',
  public.bs_get_student('stu_ava_0001', current_setting('fbtest.t2')) is null
  and public.bs_get_student('stu_ava_0001', 'not-a-token') is null
  and public.bs_get_student('stu_ava_0001', current_setting('fbtest.t1')) ->> 'name' = 'Ava');
select fbtest.refused('L1g a valid token does not carry a child into a different class',
  format($q$select public.bs_join_student('TESTBB', 'stu_ava_0001', 'Ava', '🐼', '[]', %L)$q$,
         current_setting('fbtest.t1')),
  'Could not sign in to this class');
reset role;
select fbtest.check('L1h the refused name-and-picture attempt did not create a duplicate child',
  (select count(*) from public.students where classroom_code = 'TESTAA') = 2);

-- ---------------------------------------------------------------------------
-- E + L2: a teacher trusts a classroom tablet; tap-your-name works there only
-- ---------------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"aaaaaaaa-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select set_config('fbtest.d1',
  public.bs_enroll_device('TESTAA', 'Reading corner iPad') ->> 'device_secret', true);
select fbtest.check('E1 trusting a device hands back a 64-hex secret',
  current_setting('fbtest.d1') ~ '^[0-9a-f]{64}$');
select fbtest.refused('E2 a teacher cannot trust a device for another teacher''s class',
  $q$select public.bs_enroll_device('TESTBB')$q$, 'Only this class');
reset role;
select fbtest.check('E3 only a hash of the device secret is stored',
  not exists (select 1 from private.classroom_devices
               where secret_hash = current_setting('fbtest.d1'))
  and exists (select 1 from private.classroom_devices
               where secret_hash = encode(sha256(convert_to(current_setting('fbtest.d1'), 'UTF8')), 'hex')));
select fbtest.check('E4 ...and the same for student sessions',
  not exists (select 1 from private.student_sessions where token_hash = current_setting('fbtest.t1')));

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select fbtest.check('L2a on a trusted device the class list appears',
  (select count(*) from public.bs_list_students('TESTAA', current_setting('fbtest.d1'))) = 2);
select fbtest.check('L2b a device trusted for one class shows nothing for another',
  (select count(*) from public.bs_list_students('TESTBB', current_setting('fbtest.d1'))) = 0);
select set_config('fbtest.t1d',
  public.bs_join_student('TESTAA', 'stu_ava_0001', 'Ava', '🐼', '[]', null,
                         current_setting('fbtest.d1')) ->> 'token', true);
select fbtest.check('L2c tapping a name on a trusted device starts a new session for that child',
  current_setting('fbtest.t1d') ~ '^[0-9a-f]{64}$'
  and current_setting('fbtest.t1d') <> current_setting('fbtest.t1')
  and public.bs_get_student('stu_ava_0001', current_setting('fbtest.t1d')) ->> 'name' = 'Ava');
select fbtest.check('L2d name and picture on a trusted device finds the existing child, not a duplicate',
  public.bs_join_student('TESTAA', 'stu_newtablet', 'BO', '🦊', '[]', null,
                         current_setting('fbtest.d1')) ->> 'id' = 'stu_bo_0002');
reset role;

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"aaaaaaaa-0000-4000-8000-00000000000a","role":"authenticated"}', true);
update public.classrooms set roster_pick = false where code = 'TESTAA';
reset role;
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select fbtest.check('L2e with roster sign-in switched off, even a trusted device gets no list',
  (select count(*) from public.bs_list_students('TESTAA', current_setting('fbtest.d1'))) = 0);
reset role;

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"aaaaaaaa-0000-4000-8000-00000000000a","role":"authenticated"}', true);
update public.classrooms set roster_pick = true where code = 'TESTAA';
select fbtest.check('E5 revoking reports how many devices it stopped trusting',
  public.bs_revoke_devices('TESTAA') = 1);
reset role;
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select fbtest.refused('L2f a revoked device can no longer sign children in',
  format($q$select public.bs_join_student('TESTAA', 'stu_ava_0001', 'Ava', '🐼', '[]', null, %L)$q$,
         current_setting('fbtest.d1')),
  'ask your teacher to set it up');
select fbtest.check('L2g sessions a revoked device started are ended with it',
  public.bs_get_student('stu_ava_0001', current_setting('fbtest.t1d')) is null);
select fbtest.check('L2h while the child''s session on their own device still works',
  public.bs_get_student('stu_ava_0001', current_setting('fbtest.t1')) is not null);

-- ---------------------------------------------------------------------------
-- C: consent is bound to the child's own session
-- ---------------------------------------------------------------------------
select fbtest.refused('C1 consent cannot be recorded without the child''s token',
  $q$select public.bs_record_consent('TESTAA', 'stu_ava_0001', 'Ava', 'school_authorized', 'x')$q$,
  'Consent can only');
select fbtest.refused('C2 nor with another child''s token',
  format($q$select public.bs_record_consent('TESTAA', 'stu_ava_0001', 'Ava',
                                            'school_authorized', 'x', %L)$q$,
         current_setting('fbtest.t2')),
  'Consent can only');
select fbtest.check('C3 with the child''s own token it is recorded',
  public.bs_record_consent('TESTAA', 'stu_ava_0001', 'Mallory', 'school_authorized',
                           'Confirmed by supervising adult at join', current_setting('fbtest.t1')));

-- ---------------------------------------------------------------------------
-- S: saves validate, clamp, and merge instead of overwrite
-- ---------------------------------------------------------------------------
select fbtest.refused('S1 a save needs the child''s own token',
  format($q$select public.bs_save_student('stu_ava_0001', %L, '{"stars": 5}')$q$,
         current_setting('fbtest.t2')),
  'expired');

-- Tablet 1 saves: a real check-in, one dated five days ahead, one with an
-- unreadable date, one with a feeling the app does not know, and one carrying
-- a key the app never writes. The payload also tries to move the record into
-- another class, onto another child, and to rename it.
select public.bs_save_student('stu_ava_0001', current_setting('fbtest.t1'), jsonb_build_object(
  'id', 'stu_cy_0003', 'classroom_code', 'TESTBB', 'name', 'Mallory', 'stars', 7,
  'emotion_log', jsonb_build_array(
    jsonb_build_object('emoji', '😢', 'label', 'Sad', 'time', '09:10 AM',
                       'iso', to_char(current_date - 1, 'YYYY-MM-DD')),
    jsonb_build_object('emoji', '😊', 'label', 'Happy', 'time', '09:00 AM',
                       'iso', to_char(current_date + 5, 'YYYY-MM-DD')),
    jsonb_build_object('label', 'Sad', 'iso', 'not-a-date'),
    jsonb_build_object('emoji', '❓', 'label', 'Blorp', 'time', '10:00 AM',
                       'iso', to_char(current_date - 1, 'YYYY-MM-DD')),
    jsonb_build_object('label', 'Sad', 'time', '11:00 AM', 'evil', 'x',
                       'iso', to_char(current_date - 1, 'YYYY-MM-DD')))));
-- Tablet 2, which never saw tablet 1's rows, saves its own afternoon check-in.
select public.bs_save_student('stu_ava_0001', current_setting('fbtest.t1'), jsonb_build_object(
  'emotion_log', jsonb_build_array(
    jsonb_build_object('emoji', '😠', 'label', 'Angry', 'time', '2:00 PM',
                       'iso', to_char(current_date - 1, 'YYYY-MM-DD')))));
-- Tablet 1 saves its same rows again.
select public.bs_save_student('stu_ava_0001', current_setting('fbtest.t1'), jsonb_build_object(
  'emotion_log', jsonb_build_array(
    jsonb_build_object('emoji', '😢', 'label', 'Sad', 'time', '09:10 AM',
                       'iso', to_char(current_date - 1, 'YYYY-MM-DD')))));

select set_config('fbtest.log',
  (public.bs_get_student('stu_ava_0001', current_setting('fbtest.t1')) -> 'emotionLog')::text, true);
select fbtest.check('S2 a check-in dated in the future is not stored',
  not (current_setting('fbtest.log')::jsonb @> '[{"label": "Happy"}]'), current_setting('fbtest.log'));
select fbtest.check('S3 a check-in whose date cannot be read is not stored',
  not (current_setting('fbtest.log')::jsonb @> '[{"iso": "not-a-date"}]'));
select fbtest.check('S4 an unrecognised feeling IS stored - it must still break a streak',
  current_setting('fbtest.log')::jsonb @> '[{"label": "Blorp"}]');
select fbtest.check('S5 keys the app never writes are stripped',
  current_setting('fbtest.log') not like '%evil%');
select fbtest.check('S6 neither tablet''s check-ins were lost, and re-sending did not duplicate',
  jsonb_array_length(current_setting('fbtest.log')::jsonb) = 4, current_setting('fbtest.log'));
select fbtest.check('S7 the merged log is in time order',
  (select array_agg(e ->> 'label' order by ord)
     from jsonb_array_elements(current_setting('fbtest.log')::jsonb) with ordinality as t (e, ord))
  = array['Sad', 'Blorp', 'Sad', 'Angry']);
select fbtest.check('S8 counters the child may change were saved',
  (public.bs_get_student('stu_ava_0001', current_setting('fbtest.t1')) ->> 'stars')::integer = 7);
reset role;

-- ---------------------------------------------------------------------------
-- R: row-level security, as teacher A
-- ---------------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"aaaaaaaa-0000-4000-8000-00000000000a","role":"authenticated"}', true);

select fbtest.check('R2 a teacher sees only their own classroom',
  (select array_agg(code) from public.classrooms) = array['TESTAA']);
select fbtest.check('R3 ...and only their own students',
  (select array_agg(id order by id) from public.students) = array['stu_ava_0001', 'stu_bo_0002']);
select fbtest.check('R4 a student row carries no session secret',
  not exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'students'
                 and column_name ilike '%token%' and column_name <> 'tokens'));
select fbtest.check('R5 the save could not move, re-assign or rename the child',
  (select classroom_code = 'TESTAA' and name = 'Ava'
     from public.students where id = 'stu_ava_0001'));
select fbtest.check('C4 consent records the name on file, not the name the request sent',
  (select student_name from public.consent where student_id = 'stu_ava_0001') = 'Ava');

insert into public.notes (classroom_code, student_id, text)
values ('TESTAA', 'stu_ava_0001', 'Struggled with the transition to Gym; needed support.');
select fbtest.check('R6 a teacher can write a note about their own student',
  (select count(*) from public.notes) = 1);
select fbtest.refused('R7 ...but not about another teacher''s student',
  $q$insert into public.notes (classroom_code, student_id, text) values ('TESTBB', 'stu_cy_0003', 'x')$q$,
  'row-level security');
select fbtest.refused('R8 ...nor by claiming that student is in their own room',
  $q$insert into public.notes (classroom_code, student_id, text) values ('TESTAA', 'stu_cy_0003', 'x')$q$,
  'foreign key');

update public.classrooms set name = 'Room A, renamed', token_goal = 6 where code = 'TESTAA';
select fbtest.check('R9 a teacher can change their own classroom''s settings',
  (select name from public.classrooms where code = 'TESTAA') = 'Room A, renamed');
select fbtest.refused('R10 ...but cannot give the classroom to someone else',
  $q$update public.classrooms set teacher_id = 'bbbbbbbb-0000-4000-8000-00000000000b'$q$,
  'permission denied');
select fbtest.refused('R11 ...or change its code',
  $q$update public.classrooms set code = 'NEWCOD'$q$, 'permission denied');
update public.classrooms set name = 'Hijacked' where code = 'TESTBB';     -- RLS: matches no row
delete from public.students where id = 'stu_cy_0003';                    -- RLS: matches no row
select fbtest.refused('R12 the session secrets are out of a teacher''s reach too',
  'select * from private.student_sessions', 'permission denied');

insert into public.audit_log (classroom_code, actor, action, detail)
values ('TESTAA', 'teacher:someone-else', 'viewed_roster', '');
select fbtest.check('R13 the access log records the real teacher, not the one claimed',
  (select actor from public.audit_log where action = 'viewed_roster')
  = 'teacher:aaaaaaaa-0000-4000-8000-00000000000a');
select fbtest.check('R14 ...and includes the entries the server wrote itself',
  exists (select 1 from public.audit_log where action = 'consent_recorded')
  and exists (select 1 from public.audit_log where action = 'device_trusted'));
select fbtest.refused('R15 the access log cannot be edited',
  $q$update public.audit_log set detail = 'x'$q$, 'permission denied');
select fbtest.refused('R16 ...or deleted', 'delete from public.audit_log', 'permission denied');
select fbtest.refused('R17 consent rows cannot be edited by a teacher',
  $q$update public.consent set method = 'x'$q$, 'permission denied');
reset role;

select fbtest.check('R18 teacher A''s update did not touch teacher B''s classroom',
  (select name from public.classrooms where code = 'TESTBB') = 'Room B');
select fbtest.check('R19 ...and the delete did not touch teacher B''s student',
  exists (select 1 from public.students where id = 'stu_cy_0003'));

-- ---------------------------------------------------------------------------
-- F: the inventory - nothing exposed that the list above does not name
-- ---------------------------------------------------------------------------
select fbtest.check('F1 anon can execute exactly the eight student functions, and no helper',
  (select array_agg(p.proname::text order by p.proname)
     from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where ((n.nspname = 'public' and p.proname like 'bs\_%') or n.nspname = 'private')
      and has_function_privilege('anon', p.oid, 'execute'))
  = array['bs_get_classroom', 'bs_get_stories', 'bs_get_student', 'bs_join_student',
          'bs_list_students', 'bs_record_consent', 'bs_save_student', 'bs_self_delete']);
select fbtest.check('F2 RLS is on for every table',
  not exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
               where c.relkind = 'r'
                 and (n.nspname, c.relname) in (
                   ('public', 'classrooms'), ('public', 'students'), ('public', 'notes'),
                   ('public', 'stories'), ('public', 'consent'), ('public', 'audit_log'),
                   ('private', 'student_sessions'), ('private', 'classroom_devices'))
                 and not c.relrowsecurity));
select fbtest.check('F3 every security-definer function pins its search_path',
  not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
               where n.nspname in ('public', 'private') and p.prosecdef
                 and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c
                                  where c like 'search_path=%')));
select fbtest.check('F4 anon holds no privilege on any of these tables',
  not exists (select 1 from information_schema.role_table_grants
               where grantee = 'anon' and table_schema in ('public', 'private')
                 and table_name in ('classrooms', 'students', 'notes', 'stories', 'consent',
                                    'audit_log', 'student_sessions', 'classroom_devices')));
select fbtest.check('F5 the usage report is for the project owner, not the API',
  not has_table_privilege('anon', 'public.usage_by_zip', 'select')
  and not has_table_privilege('authenticated', 'public.usage_by_zip', 'select'));
select fbtest.check('F6 no API role holds a privilege on any sequence',
  not exists (select 1 from pg_class c join pg_namespace n on n.oid = c.relnamespace
               where n.nspname = 'public' and c.relkind = 'S'
                 and (has_sequence_privilege('anon', c.oid, 'usage, select, update')
                      or has_sequence_privilege('authenticated', c.oid, 'usage, select, update'))));

-- ---------------------------------------------------------------------------
-- D: deletion takes everything it should and nothing it should not
-- ---------------------------------------------------------------------------
set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select fbtest.check('D1 a child can delete their own record with their own token',
  public.bs_self_delete('stu_bo_0002', current_setting('fbtest.t2')));
reset role;
select fbtest.check('D2 ...and every session for it goes too',
  not exists (select 1 from private.student_sessions where student_id = 'stu_bo_0002'));

set local role authenticated;
select set_config('request.jwt.claims',
  '{"sub":"aaaaaaaa-0000-4000-8000-00000000000a","role":"authenticated"}', true);
select fbtest.check('D3 a teacher can delete their own account', public.bs_delete_my_account());
reset role;
select fbtest.check('D4 ...which takes every classroom, child, note, consent row, device and session',
  not exists (select 1 from public.classrooms where code = 'TESTAA')
  and not exists (select 1 from public.students where classroom_code = 'TESTAA')
  and not exists (select 1 from public.notes where classroom_code = 'TESTAA')
  and not exists (select 1 from public.consent where classroom_code = 'TESTAA')
  and not exists (select 1 from private.classroom_devices where classroom_code = 'TESTAA')
  and not exists (select 1 from private.student_sessions where student_id = 'stu_ava_0001')
  and not exists (select 1 from auth.users where id = 'aaaaaaaa-0000-4000-8000-00000000000a'));
select fbtest.check('D5 ...and nothing that belongs to another teacher',
  exists (select 1 from public.classrooms where code = 'TESTBB')
  and exists (select 1 from public.students where id = 'stu_cy_0003'));

do $$ begin
  raise notice 'ALL % SECURITY TESTS PASSED', current_setting('fbtest.passed');
end $$;

rollback;
