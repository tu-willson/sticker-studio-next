-- Sticker Studio Next: publish an immutable, shareable template from a
-- collected Creator Reel.  The original character photograph is never copied
-- or referenced here; only the saved reel snapshot, its 4x2 preview, and an
-- optional style-reference image are eligible for publication.

create table public.shared_templates (
  id uuid primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  source_reel_id uuid not null references public.creator_reels(id) on delete restrict,
  title text not null check (char_length(title) between 3 and 80),
  description text not null default '' check (char_length(description) <= 300),
  apply_cost integer not null check (apply_cost between 1 and 20),
  status text not null default 'uploading'
    check (status in ('uploading', 'published', 'withdrawn')),
  snapshot jsonb not null default '{}'::jsonb
    check (jsonb_typeof(snapshot) = 'object'),
  preview_path text not null,
  style_reference_path text,
  created_at timestamptz not null default now(),
  published_at timestamptz,
  updated_at timestamptz not null default now(),
  check ((status = 'published' and published_at is not null) or status <> 'published')
);

create unique index shared_templates_one_live_source_reel
  on public.shared_templates (owner_id, source_reel_id)
  where status in ('uploading', 'published');
create index shared_templates_published_created_idx
  on public.shared_templates (created_at desc)
  where status = 'published';

create trigger shared_templates_set_updated_at
before update on public.shared_templates
for each row execute function public.set_updated_at();

alter table public.shared_templates enable row level security;
revoke all on public.shared_templates from anon, authenticated;
grant select on public.shared_templates to authenticated;

create policy "shared_templates_select_published_or_own"
on public.shared_templates for select to authenticated
using (status = 'published' or owner_id = (select auth.uid()));

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'shared-template-assets', 'shared-template-assets', false, 25000000,
  array['image/webp']
)
on conflict (id) do update
set public = false,
    file_size_limit = excluded.file_size_limit,
    allowed_mime_types = excluded.allowed_mime_types;

create or replace function public.shared_template_asset_is_readable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1 from public.shared_templates t
    where (t.owner_id = auth.uid() or t.status = 'published')
      and (t.preview_path = p_name or t.style_reference_path = p_name)
  );
$$;

create or replace function public.shared_template_asset_is_uploadable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1 from public.shared_templates t
    where t.owner_id = auth.uid()
      and t.status = 'uploading'
      and (t.preview_path = p_name or t.style_reference_path = p_name)
  );
$$;

drop policy if exists "shared_template_assets_select" on storage.objects;
create policy "shared_template_assets_select"
on storage.objects for select to authenticated
using (
  bucket_id = 'shared-template-assets'
  and public.shared_template_asset_is_readable(name)
);

drop policy if exists "shared_template_assets_insert" on storage.objects;
create policy "shared_template_assets_insert"
on storage.objects for insert to authenticated
with check (
  bucket_id = 'shared-template-assets'
  and public.shared_template_asset_is_uploadable(name)
);

drop policy if exists "shared_template_assets_delete" on storage.objects;
create policy "shared_template_assets_delete"
on storage.objects for delete to authenticated
using (
  bucket_id = 'shared-template-assets'
  and public.shared_template_asset_is_uploadable(name)
);

create or replace function public.reserve_shared_template(
  p_template_id uuid,
  p_source_reel_id uuid,
  p_title text,
  p_description text,
  p_apply_cost integer
)
returns table (
  template_id uuid,
  preview_path text,
  style_reference_path text,
  publish_template_limit integer,
  published_template_count integer
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_entitlement record;
  v_limit integer := 0;
  v_count integer := 0;
  v_reel public.creator_reels%rowtype;
  v_title text := trim(coalesce(p_title, ''));
  v_description text := trim(coalesce(p_description, ''));
  v_prefix text;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_template_id is null or p_source_reel_id is null
     or char_length(v_title) not between 3 and 80
     or char_length(v_description) > 300
     or p_apply_cost not between 1 and 20 then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;

  select * into v_entitlement from public.get_my_effective_entitlement();
  if found and v_entitlement.entitlement_kind = 'subscription' then
    case v_entitlement.product_code
      when 'basic_monthly' then v_limit := 10;
      when 'standard_monthly' then v_limit := 15;
      when 'advanced_monthly' then v_limit := 20;
    end case;
  end if;
  if v_limit = 0 then
    raise exception 'shared_templates_subscription_required' using errcode = '42501';
  end if;

  perform pg_advisory_xact_lock(hashtextextended(v_user::text, 47));
  select * into v_reel
  from public.creator_reels r
  where r.id = p_source_reel_id and r.owner_id = v_user and r.status = 'collected'
  for update;
  if not found then
    raise exception 'creator_reel_not_collected' using errcode = 'P0002';
  end if;

  delete from public.shared_templates t
  where t.owner_id = v_user and t.status = 'uploading'
    and t.created_at < now() - interval '30 minutes';

  if exists (
    select 1 from public.shared_templates t
    where t.owner_id = v_user and t.source_reel_id = p_source_reel_id
      and t.status in ('uploading', 'published')
  ) then
    raise exception 'shared_template_already_exists' using errcode = '23505';
  end if;

  select count(*)::integer into v_count
  from public.shared_templates t
  where t.owner_id = v_user and t.status in ('uploading', 'published');
  if v_count >= v_limit then
    raise exception 'shared_template_publish_limit' using errcode = '23514';
  end if;

  v_prefix := v_user::text || '/' || p_template_id::text || '/';
  insert into public.shared_templates (
    id, owner_id, source_reel_id, title, description, apply_cost, status,
    snapshot, preview_path, style_reference_path
  ) values (
    p_template_id, v_user, v_reel.id, v_title, v_description, p_apply_cost,
    'uploading', v_reel.snapshot, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null
      then v_prefix || 'style-reference.webp' else null end
  );

  return query select p_template_id, v_prefix || 'preview.webp',
    case when v_reel.style_reference_path is not null
      then v_prefix || 'style-reference.webp' else null end,
    v_limit, v_count + 1;
end;
$$;

create or replace function public.complete_shared_template_upload(
  p_template_id uuid,
  p_preview_bytes bigint,
  p_style_reference_bytes bigint default 0
)
returns boolean
language plpgsql
security definer
set search_path = public, storage
as $$
declare
  v_user uuid := auth.uid();
  v_template public.shared_templates%rowtype;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  if p_preview_bytes not between 1 and 15000000
     or p_style_reference_bytes not between 0 and 10000000 then
    raise exception 'invalid_shared_template_asset_size' using errcode = '22023';
  end if;
  select * into v_template from public.shared_templates t
  where t.id = p_template_id and t.owner_id = v_user and t.status = 'uploading'
  for update;
  if not found then
    raise exception 'shared_template_upload_slot_not_found' using errcode = 'P0002';
  end if;
  if not exists (
       select 1 from storage.objects o
       where o.bucket_id = 'shared-template-assets' and o.name = v_template.preview_path
     ) or (p_style_reference_bytes > 0 and not exists (
       select 1 from storage.objects o
       where o.bucket_id = 'shared-template-assets' and o.name = v_template.style_reference_path
     )) then
    raise exception 'shared_template_assets_missing' using errcode = 'P0002';
  end if;

  update public.shared_templates t
  set status = 'published', published_at = now()
  where t.id = v_template.id and t.owner_id = v_user;
  return true;
end;
$$;

create or replace function public.cancel_shared_template_upload(p_template_id uuid)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;
  delete from public.shared_templates t
  where t.id = p_template_id and t.owner_id = v_user and t.status = 'uploading';
  return found;
end;
$$;

revoke all on function public.shared_template_asset_is_readable(text) from public, anon;
revoke all on function public.shared_template_asset_is_uploadable(text) from public, anon;
revoke all on function public.reserve_shared_template(uuid, uuid, text, text, integer) from public, anon;
revoke all on function public.complete_shared_template_upload(uuid, bigint, bigint) from public, anon;
revoke all on function public.cancel_shared_template_upload(uuid) from public, anon;
grant execute on function public.shared_template_asset_is_readable(text) to authenticated;
grant execute on function public.shared_template_asset_is_uploadable(text) to authenticated;
grant execute on function public.reserve_shared_template(uuid, uuid, text, text, integer) to authenticated;
grant execute on function public.complete_shared_template_upload(uuid, bigint, bigint) to authenticated;
grant execute on function public.cancel_shared_template_upload(uuid) to authenticated;

notify pgrst, 'reload schema';
