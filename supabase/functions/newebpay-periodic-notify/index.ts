import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { decryptNewebpayCbc, parseNewebpayValues, requestFields } from "../_shared/newebpay.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
function supabaseAdminKey(): string {
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}") as Record<string, unknown>;
    if (typeof keys.default === "string" && keys.default) return keys.default;
  } catch { /* fall through */ }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
}
const SERVICE_ROLE_KEY = supabaseAdminKey();
const MERCHANT_ID = (Deno.env.get("NEWEBPAY_MERCHANT_ID") || "").trim();
const HASH_KEY = Deno.env.get("NEWEBPAY_HASH_KEY") || "";
const HASH_IV = Deno.env.get("NEWEBPAY_HASH_IV") || "";

function response(body: string, status = 200): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return response("METHOD_NOT_ALLOWED", 405);
  try {
    if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !MERCHANT_ID || !HASH_KEY || !HASH_IV) return response("NOT_CONFIGURED", 503);
    const fields = await requestFields(request);
    const encryptedPeriod = fields.Period || "";
    if (!encryptedPeriod) return response("INVALID_CALLBACK", 400);
    // The periodic API signs the payload by encrypting it with the merchant's
    // AES-CBC key/IV; there is no separate TradeSha field for this endpoint.
    const decoded = parseNewebpayValues(await decryptNewebpayCbc(encryptedPeriod, HASH_KEY, HASH_IV));
    if (decoded.MerchantID !== MERCHANT_ID) return response("INVALID_CALLBACK", 400);
    const merchantOrder = decoded.MerchantOrderNo || "";
    const periodNo = decoded.PeriodNo || "";
    const renewalOrder = decoded.OrderNo || "";
    const amount = Number.parseInt(decoded.AuthAmt || decoded.PeriodAmt || "", 10);
    if (!merchantOrder || !periodNo || !Number.isSafeInteger(amount)) return response("INVALID_PAYMENT", 400);
    const payload = {
      status: decoded.Status || "", merchant_order_no: merchantOrder, order_no: renewalOrder,
      period_no: periodNo, trade_no: decoded.TradeNo || "", auth_amt: amount,
      authorized_times: decoded.AlreadyTimes || decoded.AuthTimes || "1", next_auth_date: decoded.NextAuthDate || "",
      respond_code: decoded.RespondCode || "", auth_date: decoded.AuthDate || decoded.AuthTime || "",
    };
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    const isRenewal = Boolean(renewalOrder && renewalOrder !== merchantOrder);
    const { error } = isRenewal
      ? await admin.rpc("record_newebpay_periodic_renewal", {
        p_provider_period_reference: periodNo, p_provider_order_reference: renewalOrder,
        p_provider_transaction_reference: decoded.TradeNo || null, p_paid_amount: amount,
        p_payment_status: decoded.Status || "", p_payment_result: payload,
      })
      : await admin.rpc("finalize_newebpay_initial_payment", {
        p_provider_order_reference: merchantOrder, p_provider_transaction_reference: decoded.TradeNo || null,
        p_provider_period_reference: periodNo, p_paid_amount: amount,
        p_payment_status: decoded.Status || "", p_payment_result: payload,
      });
    if (error) {
      console.error("NEWEBPAY_PERIODIC_FINALIZE_FAILED", error.code || "unknown");
      return response("ORDER_PROCESSING_FAILED", 400);
    }
    return response("SUCCESS");
  } catch (error) {
    console.error("NEWEBPAY_PERIODIC_CALLBACK_FAILED", error instanceof Error ? error.message : "unknown");
    return response("INVALID_CALLBACK", 400);
  }
});
