-- Use a SQL function here so result-column names cannot collide with
-- PL/pgSQL RETURN TABLE variables at runtime.

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
  is_favorited boolean,
  is_owner boolean,
  is_unlocked boolean,
  unlock_count bigint
)
language sql
security definer
set search_path = public
as $$
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
        where favorite.template_id = t.id and favorite.user_id = auth.uid()
      ) as is_favorited,
      (
        select favorite.created_at from public.shared_template_favorites favorite
        where favorite.template_id = t.id and favorite.user_id = auth.uid()
      ) as favorited_at,
      t.owner_id = auth.uid() as is_owner,
      (t.owner_id = auth.uid() or exists (
        select 1 from public.shared_template_redemptions redemption
        where redemption.template_id = t.id and redemption.consumer_id = auth.uid()
      )) as is_unlocked,
      count(redemption.id) filter (where redemption.consumer_id <> t.owner_id) as unlock_count
    from public.shared_templates t
    left join public.shared_template_redemptions redemption on redemption.template_id = t.id
    where auth.uid() is not null
      and (
        t.status = 'published'
        or (
          t.status in ('withdrawn', 'deleted')
          and exists (
            select 1 from public.shared_template_redemptions unlocked
            where unlocked.template_id = t.id and unlocked.consumer_id = auth.uid()
          )
        )
      )
      and not exists (
        select 1 from public.shared_template_reports report
        where report.template_id = t.id and report.reporter_id = auth.uid()
      )
      and (
        cardinality(array(
          select distinct trim(value)
          from unnest(coalesce(p_tags, array[]::text[])) value
          order by trim(value)
        )) = 0
        or t.tags && array(
          select distinct trim(value)
          from unnest(coalesce(p_tags, array[]::text[])) value
          order by trim(value)
        )
      )
      and (
        trim(coalesce(p_search, '')) = ''
        or t.title ilike '%' || trim(coalesce(p_search, '')) || '%'
        or t.description ilike '%' || trim(coalesce(p_search, '')) || '%'
      )
    group by t.id
  )
  select
    market_templates.template_id,
    market_templates.title,
    market_templates.description,
    market_templates.apply_cost,
    market_templates.tags,
    market_templates.preview_path,
    market_templates.has_style_reference,
    market_templates.published_at,
    market_templates.is_favorited,
    market_templates.is_owner,
    market_templates.is_unlocked,
    market_templates.unlock_count
  from market_templates
  where (case
    when p_sort in ('newest', 'popular', 'low_cost', 'favorites') then p_sort
    else 'newest'
  end) <> 'favorites' or market_templates.is_favorited
  order by
    case when p_sort = 'favorites' then market_templates.favorited_at end desc,
    case when p_sort = 'popular' then market_templates.unlock_count else 0 end desc,
    case when p_sort = 'low_cost' then market_templates.apply_cost else 0 end asc,
    case when p_sort = 'newest' then market_templates.published_at end desc,
    market_templates.template_id desc
  limit greatest(1, least(coalesce(p_limit, 24), 48))
  offset greatest(0, coalesce(p_offset, 0));
$$;

revoke all on function public.list_shared_template_marketplace(integer, integer, text[], text, text) from public, anon;
grant execute on function public.list_shared_template_marketplace(integer, integer, text[], text, text) to authenticated;

notify pgrst, 'reload schema';
