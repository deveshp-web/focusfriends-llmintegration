-- =============================================================================
--  FIXTURE: a small classroom database as the v3 app left it
--  supabase/tests/fixtures/v3_data.sql      (used only by run_local.py)
-- =============================================================================
--  Applied on top of schema_v3.sql. Children join and save through the v3
--  functions themselves, so their tokens, logs and badges have exactly the
--  shapes a live v3 database holds. Every row here is valid under the new
--  rules too: run_local.py requires this database, once upgraded, to match a
--  fresh install exactly. v3_messy_rows.sql adds the rows that are not.
--  All names are invented.

begin;

insert into auth.users (id, email) values
  ('11111111-0000-4000-8000-000000000001', 'v3-teacher-one@example.invalid'),
  ('22222222-0000-4000-8000-000000000002', 'v3-teacher-two@example.invalid');

-- Classrooms, as the v3 teacher dashboard inserted them.
insert into classrooms (code, teacher_id, name, teacher_name, token_reward, token_goal,
                        schedule_json, name_mode, school_name, zip, roster_pick) values
  ('V3ROOM', '11111111-0000-4000-8000-000000000001', 'Bluebird Room', 'Ms Rivera',
   'Line leader', 6, '[{"id": 1, "label": "Reading"}, {"id": 2, "label": "Gym"}]',
   'full', 'Riverside Primary', '02139', true),
  ('V3INIT', '11111111-0000-4000-8000-000000000001', 'Maple Nest', 'Ms Rivera',
   'Sticker', 5, null, 'initial', 'Riverside Primary', '02139', false),
  ('V3OTHR', '22222222-0000-4000-8000-000000000002', 'Harbour Group', 'Mr Okafor',
   null, 4, '[]', 'id', 'Brookside Elementary', '10001', false);

-- Children join through the v3 function (which mints their access tokens)...
select bs_join_student('V3ROOM', 'stu_v3_ava1', 'Ava', '🐼', '[{"id": 1, "done": false}]');
select bs_join_student('V3ROOM', 'stu_v3_bo02', 'Bo',  '🦊', '[{"id": 1, "done": false}]');
select bs_join_student('V3INIT', 'stu_v3_cy03', 'Cy',  '🐸', '[]');
select bs_join_student('V3OTHR', 'stu_v3_di04', 'Di',  '🦁', '[]');

-- ...and save through it, with the v3 app's camelCase payloads.
select bs_save_student('stu_v3_ava1', (select access_token from students where id = 'stu_v3_ava1'), $j${
  "stars": 7, "coins": 3, "tokens": 2, "streak": 4, "lastActive": "2026-08-14",
  "badges": ["first_star", "feeling_star"], "calmDone": ["breathing", "squeeze"],
  "emotionLog": [
    {"emoji": "😊", "label": "Happy", "time": "09:05 AM", "date": "08/10/2026", "iso": "2026-08-10"},
    {"emoji": "😢", "label": "Sad",   "time": "09:10 AM", "date": "08/11/2026", "iso": "2026-08-11"},
    {"emoji": "😢", "label": "Sad",   "time": "10:30 AM", "date": "08/12/2026", "iso": "2026-08-12"},
    {"emoji": "😠", "label": "Angry", "time": "11:00 AM", "date": "08/13/2026", "iso": "2026-08-13"}],
  "taskLog": [
    {"emoji": "📚", "task": "Reading", "status": "done",    "time": "09:20 AM", "iso": "2026-08-12"},
    {"emoji": "🏃", "task": "Gym",     "status": "skipped", "time": "10:15 AM", "iso": "2026-08-13"}],
  "schedule": [{"id": 1, "uid": "a1", "done": true}, {"id": 2, "uid": "a2", "done": false}],
  "aacCount": 5, "emoCount": 4, "sentenceCount": 2, "storiesRead": 1}$j$::jsonb);
select bs_save_student('stu_v3_bo02', (select access_token from students where id = 'stu_v3_bo02'), $j${
  "stars": 2, "lastActive": "2026-08-13", "badges": [],
  "emotionLog": [{"emoji": "😌", "label": "Calm", "time": "13:05", "date": "2026-08-13", "iso": "2026-08-13"}],
  "taskLog": []}$j$::jsonb);
select bs_save_student('stu_v3_cy03', (select access_token from students where id = 'stu_v3_cy03'), $j${
  "stars": 1, "badges": ["first_star"],
  "emotionLog": [{"emoji": "😐", "label": "Okay", "time": "8:30", "date": "2026-08-12", "iso": "2026-08-12"}]}$j$::jsonb);

-- What the teacher dashboard wrote.
insert into notes (classroom_code, student_id, text) values
  ('V3ROOM', 'stu_v3_ava1', 'Struggled with the move to Gym; needed a warning first.'),
  ('V3ROOM', 'stu_v3_ava1', 'Calmer after breathing in the calm corner.'),
  ('V3OTHR', 'stu_v3_di04', 'New this week.');
insert into stories (id, classroom_code, title, emoji, pages) values
  ('story_v3_gym', 'V3ROOM', 'Going to Gym', '🏃',
   '[{"emoji": "🏃", "text": "Today we go to Gym."}, {"emoji": "😊", "text": "I can ask for a break."}]'),
  ('story_v3_bus', 'V3OTHR', 'The Bus Ride', null, '[]');

-- Consent, as v3 recorded it: through a function that took no secret.
select bs_record_consent('V3ROOM', 'stu_v3_ava1', 'Ava', 'school_authorized', 'Confirmed by supervising adult at join');
select bs_record_consent('V3OTHR', 'stu_v3_di04', 'Di', 'parent_verified', 'Signed form on file');

insert into audit_log (classroom_code, actor, action, detail) values
  ('V3ROOM', 'teacher:11111111-0000-4000-8000-000000000001', 'viewed_roster', ''),
  ('V3OTHR', 'teacher:22222222-0000-4000-8000-000000000002', 'exported_student', 'stu_v3_di04');

commit;
