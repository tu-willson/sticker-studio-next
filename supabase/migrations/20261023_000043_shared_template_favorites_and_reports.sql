-- Marketplace members can keep a private favorite list and report an item.
-- A report immediately hides the item only for that reporter; three reports
-- from different members place a compact record in the future admin queue.

create table if not exists public.shared_template_favorites (
  template_id uuid not null references public.shared_templates(id) on delete cascade,
  user_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (template_id, user_id)
);

create index if not exists shared_template_favorites_user_created_idx
  on public.shared_template_favorites (user_id, created_at desc);

create table if not exists public.shared_template_reports (
  template_id uuid not null references public.shared_templates(id) on delete cascade,
  reporter_id uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (template_id, reporter_id)
);

create index if not exists shared_template_reports_template_created_idx
  on public.shared_template_reports (template_id, created_at desc);

create table if not exists public.shared_template_moderation_queue (
  template_id uuid primary key references public.shared_templates(id) on delete cascade,
  report_count integer not null check (report_count >= 3),
  review_status text not null default 'pending_review'
    check (review_status in ('pending_review', 'reviewed', 'dismissed')),
  first_reported_at timestamptz not null default now(),
  last_reported_at timestamptz not null default now()
);

alter table public.shared_template_favorites enable row level security;
alter table public.shared_template_reports enable row level security;
alter table public.shared_template_moderation_queue enable row level security;
revoke all on public.shared_template_favorites from anon, authenticated;
revoke all on public.shared_template_reports from anon, authenticated;
revoke all on public.shared_template_moderation_queue from anon, authenticated;

create or replace function public.toggle_shared_template_favorite(p_template_id uuid)
returns table (is_favorited boolean)
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
  if not exists (
    select 1 from public.shared_templates t
    where t.id = p_template_id and t.status = 'published'
  ) then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;

  delete from public.shared_template_favorites f
  where f.template_id = p_template_id and f.user_id = v_user;
  if found then
    return query select false;
    return;
  end if;

  insert into public.shared_template_favorites (template_id, user_id)
  values (p_template_id, v_user);
  return query select true;
end;
$$;

create or replace function public.report_shared_template(p_template_id uuid)
returns table (
  report_count integer,
  queued_for_review boolean
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_owner uuid;
  v_count integer := 0;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  select t.owner_id into v_owner
  from public.shared_templates t
  where t.id = p_template_id and t.status = 'published';
  if v_owner is null then
    raise exception 'invalid_shared_template' using errcode = '22023';
  end if;
  if v_owner = v_user then
    raise exception 'cannot_report_own_template' using errcode = '42501';
  end if;

  insert into public.shared_template_reports (template_id, reporter_id)
  values (p_template_id, v_user)
  on conflict (template_id, reporter_id) do nothing;

  select count(*)::integer into v_count
  from public.shared_template_reports r
  where r.template_id = p_template_id;

  if v_count >= 3 then
    insert into public.shared_template_moderation_queue (
      template_id, report_count, review_status, first_reported_at, last_reported_at
    )
    values (p_template_id, v_count, 'pending_review', now(), now())
    on conflict (template_id) do update
    set report_count = excluded.report_count,
        review_status = case
          when public.shared_template_moderation_queue.review_status = 'dismissed'
            then 'pending_review'
          else public.shared_template_moderation_queue.review_status
        end,
        last_reported_at = now();
  end if;

  return query select v_count, v_count >= 3;
end;
$$;

drop function if exists public.list_shared_template_marketplace(integer, integer, text[], text, text);

create function public.list_shared_template_marketplace(
  p_limit integer default 24,
  p_offset integer default 0,
  p_tags text[] default null,
  p_search text default null,
  p_sort text default 'newest'
)
returns table (
  template_id uuid,
  title text,
  description text,
  apply_cost integer,
  tags text[],
  preview_path text,
  has_style_reference boolean,
  published_at timestamptz,
  is_favorited boolean,
  is_owner boolean,
  is_unlocked boolean,
  unlock_count bigint
)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_user uuid := auth.uid();
  v_tags text[] := array(
    select distinct trim(value)
    from unnest(coalesce(p_tags, array[]::text[])) value
    order by trim(value)
  );
  v_search text := trim(coalesce(p_search, ''));
  v_sort text := case
    when p_sort in ('newest', 'popular', 'low_cost', 'favorites') then p_sort
    else 'newest'
  end;
begin
  if v_user is null then
    raise exception 'not_authenticated' using errcode = '28000';
  end if;

  return query
  with market_templates as (
    select
      t.id as template_id,
      t.title,
      t.description,
      t.apply_cost,
      t.tags,
      t.preview_path,
      t.style_reference_path is not null as has_style_reference,
      t.published_at,
      exists (
        select 1 from public.shared_template_favorites favorite
        where favorite.template_id = t.id and favorite.user_id = v_user
      ) as is_favorited,
      (
        select favorite.created_at from public.shared_template_favorites favorite
        where favorite.template_id = t.id and favorite.user_id = v_user
      ) as favorited_at,
      t.owner_id = v_user as is_owner,
      (t.owner_id = v_user or exists (
        select 1 from public.shared_template_redemptions redemption
        where redemption.template_id = t.id and redemption.consumer_id = v_user
      )) as is_unlocked,
      count(redemption.id) filter (where redemption.consumer_id <> t.owner_id) as unlock_count
    from public.shared_templates t
    left join public.shared_template_redemptions redemption on redemption.template_id = t.id
    where (
        t.status = 'published'
        or (
          t.status in ('withdrawn', 'deleted')
          and exists (
            select 1 from public.shared_template_redemptions unlocked
            where unlocked.template_id = t.id and unlocked.consumer_id = v_user
          )
        )
      )
      and not exists (
        select 1 from public.shared_template_reports report
        where report.template_id = t.id and report.reporter_id = v_user
      )
      and (cardinality(v_tags) = 0 or t.tags && v_tags)
      and (v_search = '' or t.title ilike '%' || v_search || '%' or t.description ilike '%' || v_search || '%')
    group by t.id
  )
  select
    market_templates.template_id, market_templates.title,
    market_templates.description, market_templates.apply_cost,
    market_templates.tags, market_templates.preview_path,
    market_templates.has_style_reference, market_templates.published_at,
    market_templates.is_favorited, market_templates.is_owner,
    market_templates.is_unlocked, market_templates.unlock_count
  from market_templates
  where v_sort <> 'favorites' or market_templates.is_favorited
  order by
    case when v_sort = 'favorites' then market_templates.favorited_at end desc,
    case when v_sort = 'popular' then market_templates.unlock_count else 0 end desc,
    case when v_sort = 'low_cost' then market_templates.apply_cost else 0 end asc,
    case when v_sort = 'newest' then market_templates.published_at end desc,
    market_templates.template_id desc
  limit greatest(1, least(coalesce(p_limit, 24), 48))
  offset greatest(0, coalesce(p_offset, 0));
end;
$$;

revoke all on function public.toggle_shared_template_favorite(uuid) from public, anon;
revoke all on function public.report_shared_template(uuid) from public, anon;
revoke all on function public.list_shared_template_marketplace(integer, integer, text[], text, text) from public, anon;
grant execute on function public.toggle_shared_template_favorite(uuid) to authenticated;
grant execute on function public.report_shared_template(uuid) to authenticated;
grant execute on function public.list_shared_template_marketplace(integer, integer, text[], text, text) to authenticated;

notify pgrst, 'reload schema';
