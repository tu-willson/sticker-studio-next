-- A purchaser's unlocked template remains available for free reapply even
-- after the publisher deletes its withdrawn source reel.  It remains hidden
-- from everyone else and from the publisher's marketplace management shelf.

create or replace function public.shared_template_asset_is_readable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and exists (
    select 1 from public.shared_templates t
    where (t.preview_path = p_name or t.style_reference_path = p_name)
      and (
        t.owner_id = auth.uid()
        or (t.status = 'published' and t.preview_path = p_name)
        or exists (
          select 1 from public.shared_template_redemptions r
          where r.template_id = t.id and r.consumer_id = auth.uid()
        )
      )
  );
$$;

create or replace function public.list_shared_template_marketplace(
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
  v_tags text[] := array(select distinct trim(value) from unnest(coalesce(p_tags, array[]::text[])) value order by trim(value));
  v_search text := trim(coalesce(p_search, ''));
  v_sort text := case when p_sort in ('newest', 'popular', 'low_cost') then p_sort else 'newest' end;
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
      t.owner_id = v_user as is_owner,
      (t.owner_id = v_user or exists (
        select 1 from public.shared_template_redemptions r
        where r.template_id = t.id and r.consumer_id = v_user
      )) as is_unlocked,
      count(r.id) filter (where r.consumer_id <> t.owner_id) as unlock_count
    from public.shared_templates t
    left join public.shared_template_redemptions r on r.template_id = t.id
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
      and (cardinality(v_tags) = 0 or t.tags && v_tags)
      and (v_search = '' or t.title ilike '%' || v_search || '%' or t.description ilike '%' || v_search || '%')
    group by t.id
  )
  select * from market_templates
  order by
    case when v_sort = 'popular' then market_templates.unlock_count else 0 end desc,
    case when v_sort = 'low_cost' then market_templates.apply_cost else 0 end asc,
    case when v_sort = 'newest' then market_templates.published_at end desc,
    market_templates.template_id desc
  limit greatest(1, least(coalesce(p_limit, 24), 48))
  offset greatest(0, coalesce(p_offset, 0));
end;
$$;

revoke all on function public.shared_template_asset_is_readable(text) from public, anon;
revoke all on function public.list_shared_template_marketplace(integer, integer, text[], text, text) from public, anon;
grant execute on function public.shared_template_asset_is_readable(text) to authenticated;
grant execute on function public.list_shared_template_marketplace(integer, integer, text[], text, text) to authenticated;

notify pgrst, 'reload schema';
