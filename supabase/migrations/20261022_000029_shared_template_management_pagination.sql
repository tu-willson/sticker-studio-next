-- Page the creator management shelf so a larger published-template library
-- stays compact without exposing templates owned by other members.

create or replace function public.list_my_shared_template_management(
  p_limit integer default 4,
  p_offset integer default 0
)
returns table (
  template_id uuid,
  source_reel_id uuid,
  title text,
  description text,
  apply_cost integer,
  status text,
  preview_path text,
  published_at timestamptz,
  updated_at timestamptz,
  unlock_count bigint,
  reward_credits bigint,
  total_count bigint
)
language sql
stable
security definer
set search_path = public
as $$
  with managed_templates as (
    select
      t.id as template_id,
      t.source_reel_id,
      t.title,
      t.description,
      t.apply_cost,
      t.status,
      t.preview_path,
      t.published_at,
      t.updated_at,
      count(r.id) filter (where r.consumer_id <> t.owner_id) as unlock_count,
      coalesce(sum(r.charged_cost) filter (where r.consumer_id <> t.owner_id), 0) as reward_credits
    from public.shared_templates t
    left join public.shared_template_redemptions r on r.template_id = t.id
    where t.owner_id = auth.uid()
    group by t.id
  )
  select managed_templates.*, count(*) over () as total_count
  from managed_templates
  order by case when status = 'published' then 0 else 1 end, updated_at desc
  limit greatest(1, least(coalesce(p_limit, 4), 12))
  offset greatest(0, coalesce(p_offset, 0));
$$;

revoke all on function public.list_my_shared_template_management(integer, integer) from public, anon;
grant execute on function public.list_my_shared_template_management(integer, integer) to authenticated;

notify pgrst, 'reload schema';
