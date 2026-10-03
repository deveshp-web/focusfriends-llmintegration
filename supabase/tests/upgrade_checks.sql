-- =============================================================================
--  After the upgrade: was anything lost, and is the old way in closed?
--  supabase/tests/upgrade_checks.sql      (used only by run_local.py)
-- =============================================================================
--  Run against a v3 database after the current schema has been applied to it,
--  comparing it with the copy upgrade_snapshot.sql took just before. Like
--  security_tests.sql, it stops at the first failure with "FAIL <id>", and
--  rolls everything back.

begin;

create schema fbup;

create function fbup.check(p_label text, p_ok boolean, p_detail text default '')
returns void language plpgsql as $$
begin
  if p_ok is not true then
    raise exception 'FAIL  %  %', p_label, coalesce(p_detail, '');
  end if;
  perform set_config('fbup.passed',
    (coalesce(nullif(current_setting('fbup.passed', true), ''), '0')::integer + 1)::text, true);
  raise notice 'pass  %', p_label;
end $$;

create function fbup.refused(p_label text, p_sql text, p_expect text)
returns void language plpgsql as $$
begin
  begin
    execute p_sql;
  exception when others then
    if sqlerrm ilike '%' || p_expect || '%' then
      perform fbup.check(p_label, true);
      return;
    end if;
    raise exception 'FAIL  %  refused, but with "%" rather than "%"', p_label, sqlerrm, p_expect;
  end;
  raise exception 'FAIL  %  it was allowed: %', p_label, p_sql;
end $$;

grant usage on schema fbup to anon, authenticated;
grant execute on all functions in schema fbup to anon, authenticated;
select set_config('fbup.passed', '0', true);

-- ---------------------------------------------------------------------------
-- Nothing lost
-- ---------------------------------------------------------------------------
with now_counts (tbl, n) as (
  select 'classrooms', count(*) from public.classrooms
  union all select 'students',  count(*) from public.students
  union all select 'notes',     count(*) from public.notes
  union all select 'stories',   count(*) from public.stories
  union all select 'consent',   count(*) from public.consent
  union all select 'audit_log', count(*) from public.audit_log)
select fbup.check('U1 every classroom, child, note, story, consent row and log entry is kept',
  not exists (select 1 from now_counts c join fbsnap.counts s using (tbl) where c.n <> s.n),
  (select string_agg(format('%s %s -> %s', s.tbl, s.n, c.n), ', ')
     from now_counts c join fbsnap.counts s using (tbl) where c.n <> s.n));

select fbup.check('U2 every check-in and task entry is kept exactly',
  not exists (select 1 from fbsnap.students s join public.students p using (id)
               where (jsonb_typeof(s.emotion_log) = 'array' and p.emotion_log <> s.emotion_log)
                  or (jsonb_typeof(s.task_log) = 'array' and p.task_log <> s.task_log)));
select fbup.check('U3 a log that was not a list is kept, wrapped in one',
  not exists (select 1 from fbsnap.students s join public.students p using (id)
               where (jsonb_typeof(s.emotion_log) not in ('array', 'null')
                      and p.emotion_log <> jsonb_build_array(s.emotion_log))
                  or (jsonb_typeof(s.task_log) not in ('array', 'null')
                      and p.task_log <> jsonb_build_array(s.task_log))));
select fbup.check('U4 badges are kept, now as a JSON list',
  not exists (select 1 from fbsnap.students s join public.students p using (id)
               where s.badges is not null and p.badges <> s.badges));
select fbup.check('U5 the last day each child was active is kept',
  not exists (select 1 from fbsnap.students s join public.students p using (id)
               where s.last_active is not null and p.last_active is distinct from s.last_active));
select fbup.check('U6 rooms set to initials now show ids; every other room keeps its setting',
  not exists (select 1 from fbsnap.classrooms s join public.classrooms p using (code)
               where p.name_mode <> case when s.name_mode = 'initial' then 'id'
                                         when s.name_mode is null then 'full'
                                         else s.name_mode end));
select fbup.check('U7 consent recorded under v3 is marked as unverified',
  not exists (select 1 from fbsnap.consent s join public.consent p using (id)
               where p.recorded_via <> 'legacy_unverified'));

-- ---------------------------------------------------------------------------
-- The old way in is closed
-- ---------------------------------------------------------------------------
select fbup.check('U8 the v3 tokens are gone from the table every teacher reads',
  not exists (select 1 from information_schema.columns
               where table_schema = 'public' and table_name = 'students'
                 and column_name ilike '%token%' and column_name <> 'tokens'));
select fbup.check('U9 only the current functions exist - no v3 signature survives',
  (select array_agg(p.oid::regprocedure::text order by p.oid::regprocedure::text)
     from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname like 'bs\_%')
  = array['bs_delete_my_account()', 'bs_enroll_device(text,text)', 'bs_get_classroom(text)',
          'bs_get_stories(text)', 'bs_get_student(text,text)',
          'bs_join_student(text,text,text,text,jsonb,text,text)', 'bs_list_students(text,text)',
          'bs_record_consent(text,text,text,text,text,text)', 'bs_revoke_devices(text)',
          'bs_save_student(text,text,jsonb)', 'bs_self_delete(text,text)'],
  (select string_agg(p.oid::regprocedure::text, ', ')
     from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname like 'bs\_%'));
select fbup.check('U10 only the current policies exist - no v3 policy survives',
  (select array_agg(policyname::text order by policyname) from pg_policies where schemaname = 'public')
  = array['audit_insert', 'audit_select', 'classrooms_delete', 'classrooms_insert',
          'classrooms_select', 'classrooms_update', 'consent_select', 'notes_delete',
          'notes_insert', 'notes_select', 'stories_delete', 'stories_insert', 'stories_select',
          'students_delete', 'students_select']);
select fbup.check('U11 the usage report still works for the project owner, and only for them',
  (select coalesce(sum(classrooms), 0) from public.usage_by_zip)
    = (select count(*) from public.classrooms where zip is not null and zip <> '')
  and not has_table_privilege('anon', 'public.usage_by_zip', 'select')
  and not has_table_privilege('authenticated', 'public.usage_by_zip', 'select'));

-- A v3 teacher and one of their children, picked from the copy.
select set_config('fbup.teacher', s.teacher_id::text, true), set_config('fbup.code', s.code, true)
  from fbsnap.classrooms s
 where exists (select 1 from auth.users u where u.id = s.teacher_id)
   and exists (select 1 from fbsnap.students st
                where st.classroom_code = s.code and char_length(st.name) between 1 and 30)
 order by (select count(*) from fbsnap.students st where st.classroom_code = s.code) desc, s.code
 limit 1;
select set_config('fbup.kid', st.id, true), set_config('fbup.kid_name', st.name, true),
       set_config('fbup.kid_avatar', st.avatar, true), set_config('fbup.kid_token', st.v3_token, true),
       set_config('fbup.kid_checkins', jsonb_array_length(st.emotion_log)::text, true)
  from fbsnap.students st
 where st.classroom_code = current_setting('fbup.code')
   and char_length(st.name) between 1 and 30 and jsonb_typeof(st.emotion_log) = 'array'
 order by jsonb_array_length(st.emotion_log) desc, st.id
 limit 1;
select set_config('fbup.teacher_kids', count(*)::text, true)
  from fbsnap.students st join fbsnap.classrooms c on c.code = st.classroom_code
 where c.teacher_id = current_setting('fbup.teacher')::uuid;
select set_config('fbup.last_audit', id::text, true) from fbsnap.last_audit;

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select fbup.refused('U12 a v3 child''s name and picture no longer open their record',
  format('select public.bs_join_student(%L, %L, %L, %L)', current_setting('fbup.code'),
         'stu_upgrade_probe', current_setting('fbup.kid_name'), current_setting('fbup.kid_avatar')),
  'already in this class');
select fbup.refused('U13 ...nor does their id, from a device with no sign-in',
  format('select public.bs_join_student(%L, %L, %L, %L)', current_setting('fbup.code'),
         current_setting('fbup.kid'), current_setting('fbup.kid_name'), current_setting('fbup.kid_avatar')),
  'ask your teacher');
select fbup.check('U14 ...nor their v3 token',
  public.bs_get_student(current_setting('fbup.kid'), current_setting('fbup.kid_token')) is null);
select fbup.refused('U15 an old app calling a v3 signature reaches nothing',
  $q$select public.bs_get_student('stu_v3_ava1', '00000000-0000-4000-8000-000000000000'::uuid)$q$,
  'does not exist');
reset role;

-- ---------------------------------------------------------------------------
-- ...and the new way back works for a v3 class
-- ---------------------------------------------------------------------------
set local role authenticated;
select set_config('request.jwt.claims',
  json_build_object('sub', current_setting('fbup.teacher'), 'role', 'authenticated')::text, true);
select fbup.check('U16 a v3 teacher still sees all of their own children, and only theirs',
  (select count(*) from public.students) = current_setting('fbup.teacher_kids')::bigint);
insert into public.audit_log (classroom_code, actor, action, detail)
values (current_setting('fbup.code'), 'x', 'viewed_roster', 'after the upgrade');
select fbup.check('U17 the access log numbers on from its last v3 entry',
  (select id from public.audit_log where detail = 'after the upgrade')
    > current_setting('fbup.last_audit')::bigint);
select set_config('fbup.device',
  public.bs_enroll_device(current_setting('fbup.code'), 'Upgrade probe') ->> 'device_secret', true);
reset role;

set local role anon;
select set_config('request.jwt.claims', '{"role":"anon"}', true);
select set_config('fbup.token',
  public.bs_join_student(current_setting('fbup.code'), current_setting('fbup.kid'),
                         current_setting('fbup.kid_name'), current_setting('fbup.kid_avatar'),
                         '[]'::jsonb, null, current_setting('fbup.device')) ->> 'token', true);
select fbup.check('U18 on a tablet the teacher trusts, the child is back in their own record',
  current_setting('fbup.token') ~ '^[0-9a-f]{64}$'
  and json_array_length(public.bs_get_student(current_setting('fbup.kid'), current_setting('fbup.token'))
                        -> 'emotionLog') = current_setting('fbup.kid_checkins')::integer);
select fbup.check('U19 ...and their next check-in saves on top of the old ones',
  public.bs_save_student(current_setting('fbup.kid'), current_setting('fbup.token'),
    jsonb_build_object('emotion_log', jsonb_build_array(jsonb_build_object(
      'emoji', '😊', 'label', 'Happy', 'time', '09:00 AM', 'iso', to_char(now(), 'YYYY-MM-DD')))))
  and json_array_length(public.bs_get_student(current_setting('fbup.kid'), current_setting('fbup.token'))
                        -> 'emotionLog') = current_setting('fbup.kid_checkins')::integer + 1);
reset role;

delete from auth.users where id = current_setting('fbup.teacher')::uuid;
select fbup.check('U20 deleting a v3 teacher''s account now takes their classrooms and children with it',
  not exists (select 1 from public.classrooms c join fbsnap.classrooms s using (code)
               where s.teacher_id = current_setting('fbup.teacher')::uuid)
  and not exists (select 1 from public.students p join fbsnap.students st using (id)
                   join fbsnap.classrooms s on s.code = st.classroom_code
                  where s.teacher_id = current_setting('fbup.teacher')::uuid));

do $$ begin
  raise notice 'ALL % UPGRADE CHECKS PASSED', current_setting('fbup.passed');
end $$;

rollback;
