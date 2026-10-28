import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { constantTimeEqual, decryptPayuniInfo, payuniHashInfo, payuniResultValues, requestFields } from "../_shared/payuni.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const HASH_KEY = Deno.env.get("PAYUNI_HASH_KEY") || "";
const HASH_IV = Deno.env.get("PAYUNI_HASH_IV") || "";
const SITE_URL = (Deno.env.get("PAYUNI_SITE_URL") || "https://sticker-studio-next.streamlit.app").replace(/\/$/, "");

function redirect(path: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${SITE_URL}/${path}` } });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return redirect("?payment=invalid");
  try {
    const fields = await requestFields(request);
    const encryptedInfo = fields.EncryptInfo || "";
    const hashInfo = fields.HashInfo || "";
    if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !HASH_KEY || !HASH_IV || !encryptedInfo || !hashInfo) return redirect("?payment=invalid");
    const expectedHash = await payuniHashInfo(encryptedInfo, HASH_KEY, HASH_IV);
    if (!constantTimeEqual(expectedHash, hashInfo.toUpperCase())) return redirect("?payment=invalid");
    const decoded = payuniResultValues(await decryptPayuniInfo(encryptedInfo, HASH_KEY, HASH_IV));
    const reference = decoded.MerTradeNo || decoded.MerchantOrderNo || "";
    if (!reference) return redirect("?payment=invalid");
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    const { data } = await admin.from("commerce_orders").select("id").eq("provider_order_reference", reference).maybeSingle();
    if (!data?.id) return redirect("?payment=invalid");
    return redirect(`?payment_order=${encodeURIComponent(data.id)}`);
  } catch {
    return redirect("?payment=invalid");
  }
});
