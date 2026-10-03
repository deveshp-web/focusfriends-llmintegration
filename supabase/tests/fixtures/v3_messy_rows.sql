-- =============================================================================
--  FIXTURE: rows v3 accepted that the current rules do not
--  supabase/tests/fixtures/v3_messy_rows.sql      (used only by run_local.py)
-- =============================================================================
--  Applied after v3_data.sql. Each row is something a v3 database could really
--  hold. The upgrade must keep every one of them, say which rule each breaks,
--  and still finish - one odd row must not block securing the rest.

begin;

-- A teacher who deleted their account: v3 set their rooms' teacher to NULL
-- and left the children's records inside.
insert into auth.users (id, email) values ('33333333-0000-4000-8000-000000000003', 'v3-gone@example.invalid');
insert into classrooms (code, teacher_id, name, teacher_name, name_mode)
values ('V3ORPH', '33333333-0000-4000-8000-000000000003', 'Left behind', 'Gone', null);
delete from auth.users where id = '33333333-0000-4000-8000-000000000003';

-- A child whose record no save path would produce today.
insert into students (id, classroom_code, name, avatar, stars, badges, emotion_log, task_log)
values ('stu_v3_odd5', 'V3ORPH', 'A name far longer than the thirty characters allowed', '🦊',
        -2, null, 'null', '{"task": "Reading", "status": "done"}');

-- A note filed under one room about a child in another (v3 checked only the room).
insert into notes (classroom_code, student_id, text)
values ('V3OTHR', 'stu_v3_ava1', 'Filed under the wrong room.');

-- Consent with no student named, and consent for a child since deleted.
insert into consent (classroom_code, student_id, student_name, method, detail) values
  ('V3ROOM', null, null, 'school_authorized', 'no student named'),
  ('V3ROOM', 'stu_v3_gone', 'Ghost', 'school_authorized', 'student since deleted');

-- Access-log entries with no room, and for a room since deleted.
insert into audit_log (classroom_code, actor, action, detail) values
  (null, null, 'viewed_roster', 'no room'),
  ('GONE00', 'teacher:someone', 'deleted_student', 'room since deleted');

commit;
