-- Include each publisher's own preview object in the private management RPC.

drop function if exists public.get_my_shared_template_management();

create function public.get_my_shared_template_management()
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
  reward_credits bigint
)
language sql
stable
security definer
set search_path = public
as $$
  select
    t.id,
    t.source_reel_id,
    t.title,
    t.description,
    t.apply_cost,
    t.status,
    t.preview_path,
    t.published_at,
    t.updated_at,
    count(r.id) filter (where r.consumer_id <> t.owner_id),
    coalesce(sum(r.charged_cost) filter (where r.consumer_id <> t.owner_id), 0)
  from public.shared_templates t
  left join public.shared_template_redemptions r on r.template_id = t.id
  where t.owner_id = auth.uid()
  group by t.id
  order by case when t.status = 'published' then 0 else 1 end,
    t.updated_at desc;
$$;

revoke all on function public.get_my_shared_template_management() from public, anon;
grant execute on function public.get_my_shared_template_management() to authenticated;

notify pgrst, 'reload schema';
