-- =============================================================================
--  FIXTURE: the v3 schema, exactly as the app shipped it (dated 2026-08-05).
--  supabase/tests/fixtures/schema_v3.sql      (used only by run_local.py)
-- =============================================================================
--  Everything below this header is the earlier focus-bridge-schema-secure.sql,
--  byte for byte apart from line endings. run_local.py builds a database with
--  it, fills that with data, and then upgrades it with the current schema, to
--  prove the upgrade keeps every record and ends where a fresh install does.
--  Do not edit it to make a test pass: it stands for databases already live.

-- ============================================================
--  Focus Bridge — SECURE Schema (v3, compliance foundation)
--  Run in Supabase SQL Editor.  SAFE TO RE-RUN.
--
--  WHAT THIS FIXES vs the earlier schema:
--   • The earlier schema let ANYONE with the anon key read
--     every student's record (policies were USING (true)).
--     That is disqualifying for real student data.
--   • Here, the tables are LOCKED. Direct anonymous access is
--     denied. Teachers reach their own data only after a real
--     login (Supabase Auth). Students reach ONLY their own row,
--     and only through narrow SECURITY DEFINER functions that
--     expose whitelisted fields and require a per-student token.
--
--  IMPORTANT: this is the technical layer only. It does NOT by
--  itself make you COPPA/FERPA compliant. See COMPLIANCE_SETUP.md.
-- ============================================================

-- ---------- TABLES ----------

CREATE TABLE IF NOT EXISTS classrooms (
  code           VARCHAR(6)  PRIMARY KEY,
  teacher_id     UUID        REFERENCES auth.users(id) ON DELETE SET NULL,  -- owner (v3)
  name           TEXT        NOT NULL,
  teacher_name   TEXT        NOT NULL,
  pin            VARCHAR(4),                                                -- legacy/demo only
  token_reward   TEXT        DEFAULT 'Special Reward!',
  token_goal     INTEGER     DEFAULT 5,
  schedule_json  JSONB,
  name_mode      TEXT        DEFAULT 'full',                                -- full | initial | id  (data minimization)
  school_name    TEXT,                                                      -- teacher-provided, for usage statistics
  zip            VARCHAR(10),                                               -- teacher/school ZIP, for regional statistics
  roster_pick    BOOLEAN     DEFAULT false,                                 -- teacher opt-in: let students tap their name to sign in
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS students (
  id             TEXT        PRIMARY KEY,
  classroom_code VARCHAR(6)  NOT NULL REFERENCES classrooms(code) ON DELETE CASCADE,
  access_token   UUID        DEFAULT gen_random_uuid(),                     -- per-student capability (v3)
  name           TEXT        NOT NULL,
  avatar         TEXT        NOT NULL DEFAULT '🦊',
  stars          INTEGER     DEFAULT 0,
  coins          INTEGER     DEFAULT 0,
  tokens         INTEGER     DEFAULT 0,
  badges         TEXT[]      DEFAULT '{}',
  emotion_log    JSONB       DEFAULT '[]',
  task_log       JSONB       DEFAULT '[]',
  schedule_state JSONB       DEFAULT '[]',
  aac_count      INTEGER     DEFAULT 0,
  emo_count      INTEGER     DEFAULT 0,
  sentence_count INTEGER     DEFAULT 0,
  stories_read   INTEGER     DEFAULT 0,
  calm_done      TEXT[]      DEFAULT '{}',
  streak         INTEGER     DEFAULT 0,
  last_active    DATE,
  joined_at      TIMESTAMPTZ DEFAULT NOW(),
  last_seen      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS stories (
  id             TEXT        PRIMARY KEY,
  classroom_code VARCHAR(6)  NOT NULL REFERENCES classrooms(code) ON DELETE CASCADE,
  title          TEXT        NOT NULL,
  emoji          TEXT        DEFAULT '📖',
  pages          JSONB       DEFAULT '[]',
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notes (
  id             UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  classroom_code VARCHAR(6)  NOT NULL,
  student_id     TEXT        NOT NULL REFERENCES students(id) ON DELETE CASCADE,
  text           TEXT        NOT NULL,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Consent records (COPPA / FERPA evidence trail)
CREATE TABLE IF NOT EXISTS consent (
  id             UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
  classroom_code VARCHAR(6)  NOT NULL,
  student_id     TEXT,
  student_name   TEXT,
  method         TEXT        NOT NULL,        -- e.g. 'school_authorized', 'parent_verified'
  detail         TEXT,                        -- who consented / reference
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- Audit log (who touched what)
CREATE TABLE IF NOT EXISTS audit_log (
  id             BIGSERIAL   PRIMARY KEY,
  classroom_code VARCHAR(6),
  actor          TEXT,                        -- 'teacher:<uid>' | 'student:<id>'
  action         TEXT        NOT NULL,        -- viewed_roster, exported_student, deleted_student, ...
  detail         TEXT,
  created_at     TIMESTAMPTZ DEFAULT NOW()
);

-- ---------- MIGRATIONS (no-ops on fresh installs; upgrade older DBs) ----------
ALTER TABLE classrooms ADD COLUMN IF NOT EXISTS teacher_id UUID REFERENCES auth.users(id) ON DELETE SET NULL;
ALTER TABLE classrooms ADD COLUMN IF NOT EXISTS schedule_json JSONB;
ALTER TABLE classrooms ADD COLUMN IF NOT EXISTS name_mode TEXT DEFAULT 'full';
ALTER TABLE classrooms ADD COLUMN IF NOT EXISTS school_name TEXT;
ALTER TABLE classrooms ADD COLUMN IF NOT EXISTS zip VARCHAR(10);
ALTER TABLE classrooms ADD COLUMN IF NOT EXISTS roster_pick BOOLEAN DEFAULT false;
ALTER TABLE classrooms ALTER COLUMN pin DROP NOT NULL;
ALTER TABLE students   ADD COLUMN IF NOT EXISTS access_token UUID DEFAULT gen_random_uuid();
ALTER TABLE students   ADD COLUMN IF NOT EXISTS streak INTEGER DEFAULT 0;
ALTER TABLE students   ADD COLUMN IF NOT EXISTS last_active DATE;

-- ============================================================
--  ROW LEVEL SECURITY — lock everything down
--  Direct table access is allowed ONLY to the authenticated
--  teacher who owns the classroom. Anonymous clients get nothing
--  on the tables directly; they must use the functions below.
-- ============================================================

ALTER TABLE classrooms ENABLE ROW LEVEL SECURITY;
ALTER TABLE students   ENABLE ROW LEVEL SECURITY;
ALTER TABLE stories    ENABLE ROW LEVEL SECURITY;
ALTER TABLE notes      ENABLE ROW LEVEL SECURITY;
ALTER TABLE consent    ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_log  ENABLE ROW LEVEL SECURITY;

-- ============================================================
--  Drop every existing bs_* function first.
--  CREATE OR REPLACE cannot change a function's return type, so when a
--  function gains a column (as bs_get_classroom did when roster_pick was
--  added) a plain re-run fails with "cannot change return type of existing
--  function" and the script only half-applies. Dropping first makes this
--  file safely re-runnable on any older database.
--  CASCADE also removes policies that depend on bs_owns; they are recreated
--  below, so this is safe.
-- ============================================================
DO $drop_fns$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT p.oid::regprocedure AS sig
    FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname LIKE 'bs\_%'
  LOOP
    EXECUTE 'DROP FUNCTION IF EXISTS ' || r.sig || ' CASCADE';
  END LOOP;
END
$drop_fns$;

-- ============================================================
--  CRITICAL: remove EVERY pre-existing policy on these tables first.
--  PostgreSQL combines policies with OR, so a single leftover
--  permissive policy (e.g. "Enable all access for all users USING (true)"
--  from an early schema version, or one added via the Supabase UI)
--  silently defeats all of the restrictive policies below and lets
--  every teacher read every classroom. Dropping by name is not enough,
--  because we cannot know what earlier policies were called.
-- ============================================================
DO $drop_all$
DECLARE r RECORD;
BEGIN
  FOR r IN
    SELECT policyname, tablename FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename IN ('classrooms','students','stories','notes','consent','audit_log')
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', r.policyname, r.tablename);
  END LOOP;
END
$drop_all$;

-- helper: does the current authed user own this classroom?
CREATE OR REPLACE FUNCTION bs_owns(p_code VARCHAR)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
  SELECT EXISTS (SELECT 1 FROM classrooms c WHERE c.code = p_code AND c.teacher_id = auth.uid());
$$;

-- classrooms: owner-only (insert must set yourself as owner)
DROP POLICY IF EXISTS c_sel ON classrooms;
DROP POLICY IF EXISTS c_ins ON classrooms;
DROP POLICY IF EXISTS c_upd ON classrooms;
DROP POLICY IF EXISTS c_del ON classrooms;
CREATE POLICY c_sel ON classrooms FOR SELECT USING (teacher_id = auth.uid());
CREATE POLICY c_ins ON classrooms FOR INSERT WITH CHECK (teacher_id = auth.uid());
CREATE POLICY c_upd ON classrooms FOR UPDATE USING (teacher_id = auth.uid());
CREATE POLICY c_del ON classrooms FOR DELETE USING (teacher_id = auth.uid());

-- students/stories/notes/consent/audit: visible/editable only to owning teacher
DROP POLICY IF EXISTS s_all ON students;
CREATE POLICY s_all ON students FOR ALL USING (bs_owns(classroom_code)) WITH CHECK (bs_owns(classroom_code));
DROP POLICY IF EXISTS st_all ON stories;
CREATE POLICY st_all ON stories FOR ALL USING (bs_owns(classroom_code)) WITH CHECK (bs_owns(classroom_code));
DROP POLICY IF EXISTS n_all ON notes;
CREATE POLICY n_all ON notes FOR ALL USING (bs_owns(classroom_code)) WITH CHECK (bs_owns(classroom_code));
DROP POLICY IF EXISTS cn_all ON consent;
CREATE POLICY cn_all ON consent FOR ALL USING (bs_owns(classroom_code)) WITH CHECK (bs_owns(classroom_code));
DROP POLICY IF EXISTS al_all ON audit_log;
CREATE POLICY al_all ON audit_log FOR ALL USING (bs_owns(classroom_code)) WITH CHECK (bs_owns(classroom_code));

-- ============================================================
--  STUDENT-SIDE FUNCTIONS (SECURITY DEFINER)
--  These run with elevated rights but expose ONLY whitelisted
--  fields and enforce their own scoping. They are the ONLY way
--  an anonymous (student) client can touch data.
-- ============================================================

-- Look up minimal, safe classroom info to join (no PIN, no teacher_id)
CREATE OR REPLACE FUNCTION bs_get_classroom(p_code VARCHAR)
RETURNS TABLE(name TEXT, token_reward TEXT, token_goal INT, schedule_json JSONB, name_mode TEXT, roster_pick BOOLEAN)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT name, token_reward, token_goal, schedule_json, name_mode, COALESCE(roster_pick,false)
  FROM classrooms WHERE code = upper(p_code);
$$;

-- Sign-in list: first name + avatar ONLY, never access tokens, and ONLY when the
-- teacher has switched on roster_pick for that classroom. Lets a young child tap
-- their own name instead of typing it.
CREATE OR REPLACE FUNCTION bs_list_students(p_code VARCHAR)
RETURNS TABLE(id TEXT, name TEXT, avatar TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT s.id, s.name, s.avatar FROM students s
  JOIN classrooms c ON c.code = s.classroom_code
  WHERE s.classroom_code = upper(p_code)
    AND COALESCE(c.roster_pick,false) = true
  ORDER BY s.name;
$$;

-- Stories are readable by students in the class
CREATE OR REPLACE FUNCTION bs_get_stories(p_code VARCHAR)
RETURNS TABLE(id TEXT, title TEXT, emoji TEXT, pages JSONB, created_at TIMESTAMPTZ)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT id, title, emoji, pages, created_at FROM stories
  WHERE classroom_code = upper(p_code) ORDER BY created_at ASC;
$$;

-- Join (or resume). Resuming by name ALSO requires the matching avatar, so a
-- name alone is not enough to open another student's profile. Returns the
-- student's id, capability token, and saved data.
CREATE OR REPLACE FUNCTION bs_join_student(
  p_code VARCHAR, p_id TEXT, p_name TEXT, p_avatar TEXT, p_schedule JSONB
) RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r students; existing students;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM classrooms WHERE code = upper(p_code)) THEN
    RETURN jsonb_build_object('error','no_classroom');
  END IF;
  -- name + avatar must both match to resume an existing profile
  SELECT * INTO existing FROM students
    WHERE classroom_code = upper(p_code) AND lower(name) = lower(p_name) AND avatar = p_avatar LIMIT 1;
  IF existing.id IS NOT NULL THEN
    UPDATE students SET last_seen = NOW()
      WHERE id = existing.id RETURNING * INTO r;
  ELSE
    INSERT INTO students(id, classroom_code, name, avatar, schedule_state, last_seen)
      VALUES (p_id, upper(p_code), p_name, p_avatar, COALESCE(p_schedule,'[]'::jsonb), NOW())
      RETURNING * INTO r;
  END IF;
  RETURN jsonb_build_object(
    'id', r.id, 'token', r.access_token,
    'data', jsonb_build_object(
      'name',r.name,'avatar',r.avatar,'stars',r.stars,'coins',r.coins,'tokens',r.tokens,
      'badges',r.badges,'emotionLog',r.emotion_log,'taskLog',r.task_log,'schedule',r.schedule_state,
      'aacCount',r.aac_count,'emoCount',r.emo_count,'sentenceCount',r.sentence_count,
      'storiesRead',r.stories_read,'calmDone',r.calm_done,'streak',r.streak,'lastActive',r.last_active
    )
  );
END; $$;

-- Token-authenticated load (same-device return): only the holder of the
-- per-student token can read the record. No name lookup involved.
CREATE OR REPLACE FUNCTION bs_get_student(p_id TEXT, p_token UUID)
RETURNS JSONB
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE r students;
BEGIN
  SELECT * INTO r FROM students WHERE id = p_id AND access_token = p_token;
  IF r.id IS NULL THEN RETURN NULL; END IF;
  UPDATE students SET last_seen = NOW() WHERE id = r.id;
  RETURN jsonb_build_object(
    'name',r.name,'avatar',r.avatar,'stars',r.stars,'coins',r.coins,'tokens',r.tokens,
    'badges',r.badges,'emotionLog',r.emotion_log,'taskLog',r.task_log,'schedule',r.schedule_state,
    'aacCount',r.aac_count,'emoCount',r.emo_count,'sentenceCount',r.sentence_count,
    'storiesRead',r.stories_read,'calmDone',r.calm_done,'streak',r.streak,'lastActive',r.last_active
  );
END; $$;

-- Save progress — only with a matching capability token
CREATE OR REPLACE FUNCTION bs_save_student(p_id TEXT, p_token UUID, p_payload JSONB)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  UPDATE students SET
    stars=COALESCE((p_payload->>'stars')::int,stars),
    coins=COALESCE((p_payload->>'coins')::int,coins),
    tokens=COALESCE((p_payload->>'tokens')::int,tokens),
    streak=COALESCE((p_payload->>'streak')::int,streak),
    last_active=COALESCE((p_payload->>'lastActive')::date,last_active),
    badges=COALESCE(ARRAY(SELECT jsonb_array_elements_text(p_payload->'badges')),badges),
    calm_done=COALESCE(ARRAY(SELECT jsonb_array_elements_text(p_payload->'calmDone')),calm_done),
    emotion_log=COALESCE(p_payload->'emotionLog',emotion_log),
    task_log=COALESCE(p_payload->'taskLog',task_log),
    schedule_state=COALESCE(p_payload->'schedule',schedule_state),
    aac_count=COALESCE((p_payload->>'aacCount')::int,aac_count),
    emo_count=COALESCE((p_payload->>'emoCount')::int,emo_count),
    sentence_count=COALESCE((p_payload->>'sentenceCount')::int,sentence_count),
    stories_read=COALESCE((p_payload->>'storiesRead')::int,stories_read),
    last_seen=NOW()
  WHERE id = p_id AND access_token = p_token;
  RETURN FOUND;
END; $$;

-- Record a consent event (called at join time)
CREATE OR REPLACE FUNCTION bs_record_consent(
  p_code VARCHAR, p_student_id TEXT, p_name TEXT, p_method TEXT, p_detail TEXT
) RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
BEGIN
  INSERT INTO consent(classroom_code, student_id, student_name, method, detail)
    VALUES (upper(p_code), p_student_id, p_name, p_method, p_detail);
  INSERT INTO audit_log(classroom_code, actor, action, detail)
    VALUES (upper(p_code), 'student:'||COALESCE(p_student_id,'?'), 'consent_recorded', p_method);
  RETURN TRUE;
END; $$;

-- A student can export or delete THEIR OWN data with their token (data-subject rights)
CREATE OR REPLACE FUNCTION bs_self_delete(p_id TEXT, p_token UUID)
RETURNS BOOLEAN
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
DECLARE c VARCHAR;
BEGIN
  SELECT classroom_code INTO c FROM students WHERE id=p_id AND access_token=p_token;
  IF c IS NULL THEN RETURN FALSE; END IF;
  DELETE FROM students WHERE id=p_id AND access_token=p_token;
  INSERT INTO audit_log(classroom_code, actor, action, detail)
    VALUES (c, 'student:'||p_id, 'self_deleted', 'data subject erasure');
  RETURN TRUE;
END; $$;

-- expose functions to anonymous + authenticated callers
GRANT EXECUTE ON FUNCTION bs_get_classroom(VARCHAR)               TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_list_students(VARCHAR)               TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_get_stories(VARCHAR)                 TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_join_student(VARCHAR,TEXT,TEXT,TEXT,JSONB) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_get_student(TEXT,UUID)              TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_save_student(TEXT,UUID,JSONB)        TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_record_consent(VARCHAR,TEXT,TEXT,TEXT,TEXT) TO anon, authenticated;
GRANT EXECUTE ON FUNCTION bs_self_delete(TEXT,UUID)              TO anon, authenticated;

-- ============================================================
--  PRIVILEGES — defense in depth (verified against PostgreSQL 16)
--  Students never touch tables directly; they only call the
--  SECURITY DEFINER functions above. So the anonymous role is
--  given NO table privileges at all. Even if an RLS policy were
--  ever misconfigured, anon still could not read a single row.
--  Teachers (authenticated) get table access, scoped by RLS to
--  the classrooms they own.
-- ============================================================
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon;
GRANT  ALL ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT  ALL ON ALL SEQUENCES IN SCHEMA public TO authenticated;

-- ---------- INDEXES ----------
CREATE INDEX IF NOT EXISTS idx_students_classroom ON students(classroom_code);

-- ---------- AGGREGATE STATS (for the operator's own reporting) ----------
-- Rolls classrooms up by ZIP so regional usage can be reported without reading
-- individual classrooms or any student data. Query in the Supabase SQL Editor.
CREATE OR REPLACE VIEW usage_by_zip AS
  SELECT zip,
         COUNT(*)                    AS classrooms,
         COUNT(DISTINCT school_name) AS schools,
         MIN(created_at)::date       AS first_signup
  FROM classrooms
  WHERE zip IS NOT NULL AND zip <> ''
  GROUP BY zip
  ORDER BY classrooms DESC;
CREATE INDEX IF NOT EXISTS idx_classrooms_teacher ON classrooms(teacher_id);
CREATE INDEX IF NOT EXISTS idx_stories_classroom  ON stories(classroom_code);
CREATE INDEX IF NOT EXISTS idx_notes_lookup       ON notes(classroom_code, student_id);
CREATE INDEX IF NOT EXISTS idx_consent_classroom  ON consent(classroom_code);
CREATE INDEX IF NOT EXISTS idx_audit_classroom    ON audit_log(classroom_code);

-- ============================================================
--  SELF-AUDIT — run this after the script to confirm isolation.
--  Every row must show ONLY the policies listed below. If you see
--  any policy whose condition is `true`, you have a leak: some other
--  script or the Supabase UI added it after this file ran.
--
--    SELECT tablename, policyname, qual
--    FROM pg_policies WHERE schemaname='public' ORDER BY 1,2;
--
--  Expected: classrooms -> c_sel/c_ins/c_upd/c_del (teacher_id = auth.uid())
--            students/stories/notes/consent/audit_log -> *_all (bs_owns(...))
--  Also expected: anon holds ZERO table privileges:
--    SELECT count(*) FROM information_schema.role_table_grants
--    WHERE grantee='anon' AND table_schema='public';   -- must be 0
-- ============================================================

-- ============================================================
--  DONE. Next: in Authentication > Providers, ensure Email is on.
--  For pilots you may disable "Confirm email" to simplify signup.
--  See COMPLIANCE_SETUP.md for the full checklist.
-- ============================================================
