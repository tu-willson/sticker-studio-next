import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { constantTimeEqual, decryptNewebpayMpg, newebpayTradeSha, parseNewebpayMpgValues, requestFields, safeProviderText } from "../_shared/newebpay.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
function supabaseAdminKey(): string {
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}") as Record<string, unknown>;
    if (typeof keys.default === "string" && keys.default) return keys.default;
  } catch { /* fall through */ }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
}
const SERVICE_ROLE_KEY = supabaseAdminKey();
const HASH_KEY = Deno.env.get("NEWEBPAY_HASH_KEY") || "";
const HASH_IV = Deno.env.get("NEWEBPAY_HASH_IV") || "";
const SITE_URL = (Deno.env.get("NEWEBPAY_SITE_URL") || "https://sticker-studio-next.streamlit.app").replace(/\/$/, "");

function redirect(path: string): Response {
  return new Response(null, { status: 303, headers: { Location: `${SITE_URL}/${path}` } });
}

// Keep browser-visible diagnostics intentionally coarse: they help support
// investigate a failed redirect without exposing payment payloads or secrets.
function invalidReturn(reason: string): Response {
  console.warn("NEWEBPAY_RETURN_INVALID", reason);
  return redirect(`?payment=invalid&reason=${encodeURIComponent(reason)}`);
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return invalidReturn("method");
  try {
    const fields = await requestFields(request);
    const tradeInfo = fields.TradeInfo || "";
    const tradeSha = fields.TradeSha || "";
    if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !HASH_KEY || !HASH_IV) return invalidReturn("configuration");
    if (!tradeInfo || !tradeSha) return invalidReturn("payload");
    if (!constantTimeEqual(await newebpayTradeSha(tradeInfo, HASH_KEY, HASH_IV), tradeSha.toUpperCase())) return invalidReturn("signature");

    let decoded: Record<string, string>;
    try {
      decoded = parseNewebpayMpgValues(await decryptNewebpayMpg(
        tradeInfo, HASH_KEY, HASH_IV, fields.EncryptType || "",
      ));
    } catch {
      return invalidReturn("decrypt");
    }
    const reference = decoded.MerchantOrderNo || "";
    if (!reference) return invalidReturn("reference");
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    const { data } = await admin.from("commerce_orders").select("id").eq("provider_order_reference", reference).maybeSingle();
    if (!data?.id) return invalidReturn("order");
    const result = new URLSearchParams({ payment_order: String(data.id) });
    const status = safeProviderText(decoded.Status || fields.Status, 60);
    const message = safeProviderText(decoded.Message, 180);
    if (status) result.set("payment_result", status);
    if (message) result.set("payment_message", message);
    // NotifyURL, not this browser redirect, is the only entitlement path.
    return redirect(`?${result.toString()}`);
  } catch (error) {
    console.error("NEWEBPAY_RETURN_UNEXPECTED", error instanceof Error ? error.name : "unknown");
    return invalidReturn("unexpected");
  }
});
