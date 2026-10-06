-- Atlas: одна память на все устройства.
-- Выполнить один раз: Supabase → SQL Editor → New query → вставить этот текст → Run.

-- Все строки памяти всех устройств в одной таблице: какая таблица (tbl), содержимое (data),
-- когда изменено на устройстве (updated_at) и когда попало в облако (synced_at).
create table if not exists public.atlas_rows (
  uid        text primary key,
  tbl        text not null,
  data       jsonb not null default '{}'::jsonb,
  updated_at double precision not null,
  deleted    boolean not null default false,
  device     text,
  synced_at  timestamptz not null default clock_timestamp()
);
create index if not exists atlas_rows_synced_at on public.atlas_rows (synced_at);

-- Закрыть таблицу для всех, кроме секретного ключа service_role (он хранится только на компьютере
-- и в облачном мозге Atlas, никогда на телефоне). Политик нет — значит, обычным ключом не прочитать.
alter table public.atlas_rows enable row level security;

-- Приём изменений: если строку уже меняли на другом устройстве позже — свежее не перетирается.
create or replace function public.atlas_push(rows jsonb)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare n integer;
begin
  insert into atlas_rows (uid, tbl, data, updated_at, deleted, device, synced_at)
  select r->>'uid', r->>'tbl', coalesce(r->'data', '{}'::jsonb), (r->>'updated_at')::double precision,
         coalesce((r->>'deleted')::boolean, false), r->>'device', clock_timestamp()
  from jsonb_array_elements(rows) as r
  on conflict (uid) do update
    set tbl = excluded.tbl, data = excluded.data, updated_at = excluded.updated_at,
        deleted = excluded.deleted, device = excluded.device, synced_at = clock_timestamp()
    where excluded.updated_at > atlas_rows.updated_at;
  get diagnostics n = row_count;
  return n;
end
$$;

revoke all on function public.atlas_push(jsonb) from public, anon, authenticated;
grant execute on function public.atlas_push(jsonb) to service_role;
