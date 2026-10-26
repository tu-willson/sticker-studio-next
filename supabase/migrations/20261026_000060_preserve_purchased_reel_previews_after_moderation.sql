-- A purchaser's private library must remain visually complete when its source
-- reel is withdrawn or moderation-blocked.  The earlier moderation override
-- accidentally scoped redemption access to style-reference assets only, which
-- made preview thumbnails unreadable until the publisher listed the reel again.
--
-- This does not make withdrawn previews public: the redemption check is still
-- evaluated against auth.uid() for the exact purchased reel.

create or replace function public.shared_template_asset_is_readable(p_name text)
returns boolean
language sql
stable
security definer
set search_path = public, storage
as $$
  select auth.uid() is not null and (
    public.is_beta_admin()
    or exists (
      select 1
      from public.shared_templates t
      where (t.preview_path = p_name or t.style_reference_path = p_name)
        and (
          t.owner_id = auth.uid()
          -- Only a currently public reel exposes its preview to everyone.
          or (t.status = 'published' and t.preview_path = p_name)
          -- Purchasers may keep using their private snapshot (including its
          -- preview and optional style reference) after a withdrawal, deletion
          -- or an administrator moderation block.
          or exists (
            select 1
            from public.shared_template_redemptions r
            where r.template_id = t.id
              and r.consumer_id = auth.uid()
          )
        )
    )
  );
$$;

revoke all on function public.shared_template_asset_is_readable(text) from public, anon;
grant execute on function public.shared_template_asset_is_readable(text) to authenticated;

notify pgrst, 'reload schema';
