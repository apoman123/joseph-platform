-- TradeX web beta: run once in Supabase -> SQL Editor. One table; every user sees and changes only their own rows.
create table if not exists public.tradex_docs (
  user_id    uuid   not null default auth.uid() references auth.users on delete cascade,
  kind       text   not null check (kind in ('layouts', 'templates')),
  id         text   not null check (id ~ '^[0-9a-zA-Z_-]{1,40}$'),
  name       text   not null default 'Untitled',
  saved_at   bigint not null default 0,
  summary    text   not null default '',
  doc        jsonb  not null check (pg_column_size(doc) < 2100000),
  primary key (user_id, kind, id)
);
alter table public.tradex_docs enable row level security;
drop policy if exists "own rows" on public.tradex_docs;
create policy "own rows" on public.tradex_docs for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);
