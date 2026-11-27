-- TradeX web: account profiles, plans, owner grants and self-service account deletion (2026-11-25).
-- Run once in the Supabase SQL editor (after supabase_setup.sql). Safe to run again.
-- Who is the owner is NOT set here: run, as the database admin,
--   update public.tradex_profiles set role = 'owner', plan = 'lifetime' where user_id = '<owner user id>';

-- One row per account, filled from the sign-in provider (GitHub / Google).
create table if not exists public.tradex_profiles (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  email       text,
  name        text,
  login       text,
  avatar_url  text,
  role        text not null default 'user' check (role in ('user', 'owner')),
  plan        text not null default 'free' check (plan in ('free', 'pro', 'lifetime')),
  plan_note   text check (char_length(plan_note) <= 120),
  granted_by  uuid,
  plan_updated_at timestamptz,
  created_at  timestamptz not null default now(),
  last_seen   timestamptz
);
-- Plans given by email to people who have not signed in yet (applied when they join).
create table if not exists public.tradex_grants (
  email      text primary key check (email = lower(email)),
  plan       text not null check (plan in ('free', 'pro', 'lifetime')),
  plan_note  text check (char_length(plan_note) <= 120),
  granted_by uuid,
  created_at timestamptz not null default now()
);
alter table public.tradex_profiles enable row level security;
alter table public.tradex_grants enable row level security;

create or replace function public.tradex_is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.tradex_profiles where user_id = auth.uid() and role = 'owner');
$$;

-- Read: your own row; the owner reads every row. No direct writes: changes go through the functions below.
drop policy if exists "read own or owner" on public.tradex_profiles;
create policy "read own or owner" on public.tradex_profiles for select to authenticated
  using (user_id = auth.uid() or public.tradex_is_owner());
drop policy if exists "owner reads grants" on public.tradex_grants;
create policy "owner reads grants" on public.tradex_grants for select to authenticated using (public.tradex_is_owner());

-- New account -> profile row (+ any plan granted to its email before it joined).
create or replace function public.tradex_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare m jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb); g public.tradex_grants;
begin
  select * into g from public.tradex_grants where email = lower(new.email);
  insert into public.tradex_profiles (user_id, email, name, login, avatar_url, plan, plan_note, granted_by, plan_updated_at)
  values (new.id, new.email, coalesce(m->>'full_name', m->>'name', m->>'user_name'), m->>'user_name', m->>'avatar_url',
          coalesce(g.plan, 'free'), g.plan_note, g.granted_by, case when g.plan is null then null else now() end)
  on conflict (user_id) do nothing;
  if g.email is not null then delete from public.tradex_grants where email = g.email; end if;
  return new;
end $$;
drop trigger if exists tradex_on_new_user on auth.users;
create trigger tradex_on_new_user after insert on auth.users for each row execute function public.tradex_new_user();

-- Existing accounts get their row now.
insert into public.tradex_profiles (user_id, email, name, login, avatar_url)
select u.id, u.email, coalesce(u.raw_user_meta_data->>'full_name', u.raw_user_meta_data->>'name', u.raw_user_meta_data->>'user_name'),
       u.raw_user_meta_data->>'user_name', u.raw_user_meta_data->>'avatar_url'
from auth.users u on conflict (user_id) do nothing;

-- Called by the page after sign-in: refresh name/photo from the provider, stamp last_seen, return my row.
create or replace function public.tradex_me() returns public.tradex_profiles
language plpgsql security definer set search_path = public as $$
declare u auth.users; m jsonb; r public.tradex_profiles;
begin
  select * into u from auth.users where id = auth.uid();
  if u.id is null then raise exception 'not signed in'; end if;
  m := coalesce(u.raw_user_meta_data, '{}'::jsonb);
  insert into public.tradex_profiles (user_id, email, name, login, avatar_url) values
    (u.id, u.email, coalesce(m->>'full_name', m->>'name', m->>'user_name'), m->>'user_name', m->>'avatar_url')
  on conflict (user_id) do update set email = excluded.email, name = excluded.name, login = excluded.login,
    avatar_url = excluded.avatar_url, last_seen = now()
  returning * into r;
  return r;
end $$;

-- Owner only: give a plan to a person by email (signed up or not yet).
create or replace function public.tradex_grant(target_email text, new_plan text, note text default null) returns text
language plpgsql security definer set search_path = public as $$
declare e text := lower(trim(target_email)); n int;
begin
  if not public.tradex_is_owner() then raise exception 'only the owner can give plans'; end if;
  if new_plan not in ('free', 'pro', 'lifetime') then raise exception 'unknown plan'; end if;
  if e !~ '^[^@\s]+@[^@\s]+\.[^@\s]+$' then raise exception 'not an email address'; end if;
  update public.tradex_profiles set plan = new_plan, plan_note = left(note, 120), granted_by = auth.uid(), plan_updated_at = now()
    where lower(email) = e and role <> 'owner';
  get diagnostics n = row_count;
  if n > 0 then return 'updated'; end if;
  if exists (select 1 from public.tradex_profiles where lower(email) = e and role = 'owner') then return 'owner'; end if;
  insert into public.tradex_grants (email, plan, plan_note, granted_by) values (e, new_plan, left(note, 120), auth.uid())
    on conflict (email) do update set plan = excluded.plan, plan_note = excluded.plan_note, granted_by = excluded.granted_by, created_at = now();
  return 'waiting';   -- applied when that email signs in for the first time
end $$;

-- Owner only: cancel a waiting grant.
create or replace function public.tradex_ungrant(target_email text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.tradex_is_owner() then raise exception 'only the owner can change plans'; end if;
  delete from public.tradex_grants where email = lower(trim(target_email));
end $$;

-- Anyone signed in: delete MY account and everything saved with it (layouts go with it: foreign key cascade).
-- The owner account cannot be deleted from the page.
create or replace function public.tradex_delete_me(confirm text) returns void
language plpgsql security definer set search_path = public, auth as $$
begin
  if auth.uid() is null then raise exception 'not signed in'; end if;
  if confirm is distinct from 'DELETE' then raise exception 'type DELETE to confirm'; end if;
  if public.tradex_is_owner() then raise exception 'the owner account cannot be deleted here'; end if;
  delete from auth.users where id = auth.uid();
end $$;

revoke all on function public.tradex_is_owner(), public.tradex_new_user(), public.tradex_me(),
  public.tradex_grant(text, text, text), public.tradex_ungrant(text), public.tradex_delete_me(text) from public, anon;
grant execute on function public.tradex_is_owner(), public.tradex_me(), public.tradex_grant(text, text, text),
  public.tradex_ungrant(text), public.tradex_delete_me(text) to authenticated;

-- Free-limit watcher (2026-11-25): one usage row per day, written by a daily Cloudflare cron through a harmless public
-- function (it only records today's numbers, one row per day, returns nothing). Calling it also keeps the free project
-- from pausing after a week without activity. The owner sees the rows in Account settings > Owner.
create table if not exists public.tradex_usage (
  day        date primary key,
  db_bytes   bigint not null,
  accounts   int not null,
  active_30d int not null,
  layouts    int not null,
  templates  int not null,
  at         timestamptz not null default now()
);
alter table public.tradex_usage enable row level security;
drop policy if exists "owner reads usage" on public.tradex_usage;
create policy "owner reads usage" on public.tradex_usage for select to authenticated using (public.tradex_is_owner());

create or replace function public.tradex_usage_tick() returns void
language sql security definer set search_path = public, auth as $$
  insert into public.tradex_usage (day, db_bytes, accounts, active_30d, layouts, templates)
  select current_date, pg_database_size(current_database()),
         (select count(*) from auth.users),
         (select count(*) from auth.users where last_sign_in_at > now() - interval '30 days'),
         (select count(*) from public.tradex_docs where kind = 'layouts'),
         (select count(*) from public.tradex_docs where kind = 'templates')
  on conflict (day) do update set db_bytes = excluded.db_bytes, accounts = excluded.accounts, active_30d = excluded.active_30d,
    layouts = excluded.layouts, templates = excluded.templates, at = now();
$$;
revoke all on function public.tradex_usage_tick() from public;
grant execute on function public.tradex_usage_tick() to anon, authenticated;

-- Saved-item cap per account (2026-11-25), protects the free 500 MB database: Free 30 layouts / 50 templates,
-- Pro and Lifetime 200 each, owner unlimited. Only NEW items count (saving over an existing one is always allowed).
create or replace function public.tradex_docs_cap() returns trigger
language plpgsql security definer set search_path = public as $$
declare pr public.tradex_profiles; lim int; n int;
begin
  select * into pr from public.tradex_profiles where user_id = new.user_id;
  if pr.role = 'owner' then return new; end if;
  -- upsert over an item that already exists (autosave) is not a new item
  if exists (select 1 from public.tradex_docs where user_id = new.user_id and kind = new.kind and id = new.id) then return new; end if;
  lim := case when coalesce(pr.plan, 'free') in ('pro', 'lifetime') then 200 when new.kind = 'templates' then 50 else 30 end;
  select count(*) into n from public.tradex_docs where user_id = new.user_id and kind = new.kind;
  if n >= lim then raise exception 'limit reached: % saved % on your plan; delete one to save a new one', lim, new.kind; end if;
  return new;
end $$;
drop trigger if exists tradex_docs_cap on public.tradex_docs;
create trigger tradex_docs_cap before insert on public.tradex_docs for each row execute function public.tradex_docs_cap();
