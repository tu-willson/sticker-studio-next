-- Raise the published monthly image-generation allowance for the Advanced
-- subscription. Existing grants remain immutable purchase records; this value
-- is used for new test purchases, verified payments, and future renewals.

update public.commerce_products
set image_credits = 45
where code = 'advanced_monthly';

notify pgrst, 'reload schema';
