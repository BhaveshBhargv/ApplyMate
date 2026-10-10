-- ApplyMate database schema.
-- Run ONCE in Supabase -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run: every statement is idempotent.
--
-- Design notes
--   * Everything personal (resume, cover letter, job snapshot, notes, labels) is
--     encrypted by the app BEFORE it reaches the database, in the `data_enc`
--     columns. Anyone with database access sees ciphertext only.
--   * Row Level Security is on for every table: a logged-in user can only touch
--     rows where user_id = their own id, even if the app code had a bug.
--   * The app only ever uses the publishable key + the user's own login token.
--     The service_role key is never needed.
--   * Limits (rows per user, blob size, AI calls per day) are enforced HERE, so
--     they can't be bypassed from the client. They are sized for the Supabase
--     free tier (500 MB) -- change the numbers below if you upgrade.

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------

create table if not exists public.profiles (
    user_id                 uuid primary key references auth.users (id) on delete cascade,
    wrapped_dek             text not null,          -- the user's data key, encrypted with the app master key
    onboarding_completed_at timestamptz,            -- null until the first-login walkthrough has been shown
    created_at              timestamptz not null default now()
);

create table if not exists public.resumes (
    id             uuid primary key,
    user_id        uuid not null references auth.users (id) on delete cascade,
    data_enc       text not null check (length(data_enc) <= 131072),   -- label + full resume, encrypted
    schema_version int  not null default 1,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists resumes_user_idx on public.resumes (user_id, updated_at desc);

-- Which of the user's résumés is their primary one (loaded first on login). Added after the
-- first release, so this is an ALTER: re-running the whole file upgrades an existing database.
alter table public.resumes add column if not exists is_primary boolean not null default false;

create table if not exists public.cover_letters (
    id         uuid primary key,
    user_id    uuid not null references auth.users (id) on delete cascade,
    resume_id  uuid references public.resumes (id) on delete set null,  -- which resume it was written from
    data_enc   text not null check (length(data_enc) <= 65536),         -- title, company, role, body, encrypted
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists cover_letters_user_idx on public.cover_letters (user_id, updated_at desc);

create table if not exists public.saved_jobs (
    id         uuid primary key,
    user_id    uuid not null references auth.users (id) on delete cascade,
    url_hash   text not null,                      -- keyed hash of the job URL, only used to stop duplicates
    source     text not null default '',           -- e.g. Adzuna / Remotive (a public job-board name)
    status     text not null default 'saved'
               check (status in ('saved', 'applied', 'interviewing', 'offer', 'rejected')),
    data_enc   text not null check (length(data_enc) <= 32768),         -- job snapshot + your notes, encrypted
    saved_at   timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (user_id, url_hash)
);
create index if not exists saved_jobs_user_idx on public.saved_jobs (user_id, saved_at desc);

-- Per-user daily AI-call counter (protects the shared OpenRouter free quota).
create table if not exists public.ai_usage (
    user_id uuid not null references auth.users (id) on delete cascade,
    day     date not null default ((now() at time zone 'utc')::date),
    calls   int  not null default 0,
    primary key (user_id, day)
);

-- Application log. Never contains personal data (the app only logs event names,
-- counts and error types). user_id is cleared, not deleted, when a user is
-- deleted, so the audit trail survives without identifying anyone.
create table if not exists public.app_logs (
    id         bigint generated always as identity primary key,
    ts         timestamptz not null default now(),
    level      text not null default 'info' check (level in ('debug', 'info', 'warning', 'error')),
    category   text not null default 'app',        -- app | auth | data | privacy | ai | error
    event      text not null,
    user_id    uuid references auth.users (id) on delete set null,
    session_id text,
    details    jsonb not null default '{}'::jsonb
);
create index if not exists app_logs_ts_idx   on public.app_logs (ts desc);
create index if not exists app_logs_user_idx on public.app_logs (user_id, ts desc);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------

alter table public.profiles      enable row level security;
alter table public.resumes       enable row level security;
alter table public.cover_letters enable row level security;
alter table public.saved_jobs    enable row level security;
alter table public.ai_usage      enable row level security;
alter table public.app_logs      enable row level security;

drop policy if exists "own profile" on public.profiles;
create policy "own profile" on public.profiles for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own resumes" on public.resumes;
create policy "own resumes" on public.resumes for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own cover letters" on public.cover_letters;
create policy "own cover letters" on public.cover_letters for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

drop policy if exists "own saved jobs" on public.saved_jobs;
create policy "own saved jobs" on public.saved_jobs for all to authenticated
    using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));

-- ai_usage: read-only for the owner (writes only through consume_ai_call()).
drop policy if exists "read own ai usage" on public.ai_usage;
create policy "read own ai usage" on public.ai_usage for select to authenticated
    using (user_id = (select auth.uid()));

-- app_logs: NO policies = nobody can read or write it directly through the API.
-- Writes go through log_event(); you read it in the Supabase dashboard.
revoke all on public.app_logs from anon, authenticated;
revoke all on public.ai_usage from anon;
revoke insert, update, delete on public.ai_usage from authenticated;

-- ---------------------------------------------------------------------------
-- Triggers: updated_at + per-user limits (free-tier sizing)
-- ---------------------------------------------------------------------------

-- Bumps updated_at only when the stored content changes (not, e.g., when a résumé is
-- merely marked primary), so "most recently edited" stays meaningful.
create or replace function public.set_updated_at() returns trigger
language plpgsql as $$
begin
    if new.data_enc is distinct from old.data_enc then
        new.updated_at := now();
    end if;
    return new;
end $$;

drop trigger if exists resumes_touch on public.resumes;
create trigger resumes_touch before update on public.resumes
    for each row execute function public.set_updated_at();
drop trigger if exists cover_letters_touch on public.cover_letters;
create trigger cover_letters_touch before update on public.cover_letters
    for each row execute function public.set_updated_at();
drop trigger if exists saved_jobs_touch on public.saved_jobs;
create trigger saved_jobs_touch before update on public.saved_jobs
    for each row execute function public.set_updated_at();

-- Rejects an insert once the user already owns `max_rows` rows in the table.
-- The error text "limit_reached:<table>" is what the app looks for.
create or replace function public.enforce_row_limit() returns trigger
language plpgsql as $$
declare
    max_rows int;
    n        int;
begin
    max_rows := tg_argv[0]::int;
    execute format('select count(*) from public.%I where user_id = $1', tg_table_name)
        into n using new.user_id;
    if n >= max_rows then
        raise exception 'limit_reached:%', tg_table_name using errcode = 'P0001';
    end if;
    return new;
end $$;

drop trigger if exists resumes_limit on public.resumes;
create trigger resumes_limit before insert on public.resumes
    for each row execute function public.enforce_row_limit(5);        -- 5 resumes per user
drop trigger if exists cover_letters_limit on public.cover_letters;
create trigger cover_letters_limit before insert on public.cover_letters
    for each row execute function public.enforce_row_limit(10);       -- 10 cover letters per user
drop trigger if exists saved_jobs_limit on public.saved_jobs;
create trigger saved_jobs_limit before insert on public.saved_jobs
    for each row execute function public.enforce_row_limit(50);       -- 50 saved jobs per user

-- ---------------------------------------------------------------------------
-- Functions the app calls (SECURITY DEFINER, locked to a fixed search_path)
-- ---------------------------------------------------------------------------

-- Daily AI-call limit per user. Change the 15 here to raise/lower it.
create or replace function public.ai_daily_limit() returns int
language sql immutable as $$ select 15 $$;

-- Counts one AI call for the caller. Returns {allowed, used, limit}.
create or replace function public.consume_ai_call() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    uid   uuid := auth.uid();
    today date := (now() at time zone 'utc')::date;
    lim   int  := public.ai_daily_limit();
    n     int;
begin
    if uid is null then
        raise exception 'not authenticated';
    end if;
    insert into public.ai_usage as u (user_id, day, calls)
    values (uid, today, 1)
    on conflict (user_id, day) do update set calls = u.calls + 1 where u.calls < lim
    returning u.calls into n;
    if n is null then      -- already at the limit: the conflict update was skipped
        select calls into n from public.ai_usage where user_id = uid and day = today;
        return jsonb_build_object('allowed', false, 'used', coalesce(n, lim), 'limit', lim);
    end if;
    return jsonb_build_object('allowed', true, 'used', n, 'limit', lim);
end $$;

-- How many AI calls the caller has used today (does not consume one).
create or replace function public.ai_quota() returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
    n   int;
begin
    if uid is null then
        raise exception 'not authenticated';
    end if;
    select calls into n from public.ai_usage
     where user_id = uid and day = (now() at time zone 'utc')::date;
    return jsonb_build_object('used', coalesce(n, 0), 'limit', public.ai_daily_limit());
end $$;

-- Application logging. Logged-in users log as themselves (user_id is forced to
-- their own id). Logged-out callers (failed logins) may only log 'auth' events
-- and are capped at 200 rows/hour so the table can't be flooded.
create or replace function public.log_event(
    p_level text, p_category text, p_event text,
    p_details jsonb default '{}'::jsonb, p_session text default null
) returns void
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
begin
    if uid is null then
        p_category := 'auth';
        if (select count(*) from public.app_logs
             where user_id is null and category = 'auth' and ts > now() - interval '1 hour') >= 200 then
            return;
        end if;
    end if;
    insert into public.app_logs (level, category, event, user_id, session_id, details)
    values (
        case when p_level in ('debug', 'info', 'warning', 'error') then p_level else 'info' end,
        left(coalesce(p_category, 'app'), 32),
        left(coalesce(p_event, 'unknown'), 64),
        uid,
        left(p_session, 64),
        case when p_details is null then '{}'::jsonb
             when pg_column_size(p_details) > 2048 then jsonb_build_object('truncated', true)
             else p_details end
    );
end $$;

-- Deletes the caller's account and, by cascade, every row they own: profile
-- (including their encryption key), resumes, cover letters, saved jobs, AI usage.
-- The log row it writes has no user_id and no personal data.
create or replace function public.delete_my_account() returns void
language plpgsql security definer set search_path = '' as $$
declare
    uid uuid := auth.uid();
begin
    if uid is null then
        raise exception 'not authenticated';
    end if;
    insert into public.app_logs (level, category, event, user_id, details)
    values ('info', 'privacy', 'account_deleted', null, '{}'::jsonb);
    delete from auth.users where id = uid;
end $$;

-- Marks one résumé as the caller's primary and clears the flag on all the others, atomically.
-- SECURITY INVOKER: row level security still limits it to the caller's own rows.
create or replace function public.set_primary_resume(p_id uuid) returns void
language sql security invoker set search_path = '' as $$
    update public.resumes
       set is_primary = (id = p_id)
     where user_id = (select auth.uid())
       and (is_primary or id = p_id);
$$;

-- Who may call what.
revoke execute on function public.consume_ai_call()    from public, anon, authenticated;
revoke execute on function public.ai_quota()           from public, anon, authenticated;
revoke execute on function public.delete_my_account()  from public, anon, authenticated;
revoke execute on function public.log_event(text, text, text, jsonb, text) from public, anon, authenticated;
revoke execute on function public.set_primary_resume(uuid) from public, anon, authenticated;
grant  execute on function public.set_primary_resume(uuid) to authenticated;
grant  execute on function public.consume_ai_call()    to authenticated;
grant  execute on function public.ai_quota()           to authenticated;
grant  execute on function public.delete_my_account()  to authenticated;
grant  execute on function public.log_event(text, text, text, jsonb, text) to anon, authenticated;

-- ---------------------------------------------------------------------------
-- Optional: keep logs for 90 days only (needs the pg_cron extension, which you
-- enable under Database -> Extensions). Uncomment to use.
-- ---------------------------------------------------------------------------
-- select cron.schedule('purge-app-logs', '15 3 * * *',
--     $$ delete from public.app_logs where ts < now() - interval '90 days' $$);
