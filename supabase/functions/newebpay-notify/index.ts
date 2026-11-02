import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { constantTimeEqual, decryptNewebpayCbc, newebpayTradeSha, parseNewebpayMpgValues, requestFields } from "../_shared/newebpay.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
function supabaseAdminKey(): string {
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}") as Record<string, unknown>;
    if (typeof keys.default === "string" && keys.default) return keys.default;
  } catch { /* fall through */ }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
}
const SERVICE_ROLE_KEY = supabaseAdminKey();
const MERCHANT_ID = Deno.env.get("NEWEBPAY_MERCHANT_ID") || "";
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
    const tradeInfo = fields.TradeInfo || "";
    const tradeSha = fields.TradeSha || "";
    if (!tradeInfo || !tradeSha) return response("INVALID_CALLBACK", 400);
    const expected = await newebpayTradeSha(tradeInfo, HASH_KEY, HASH_IV);
    if (!constantTimeEqual(expected, tradeSha.toUpperCase())) return response("INVALID_SIGNATURE", 400);
    const decoded = parseNewebpayMpgValues(await decryptNewebpayCbc(tradeInfo, HASH_KEY, HASH_IV));
    if (decoded.MerchantID !== MERCHANT_ID) return response("INVALID_CALLBACK", 400);
    const reference = decoded.MerchantOrderNo || "";
    const amount = Number.parseInt(decoded.Amt || "", 10);
    if (!reference || !Number.isSafeInteger(amount)) return response("INVALID_PAYMENT", 400);
    const result = {
      status: decoded.Status || fields.Status || "", merchant_order_no: reference,
      trade_no: decoded.TradeNo || "", payment_type: decoded.PaymentType || "",
      pay_time: decoded.PayTime || "", respond_code: decoded.RespondCode || "",
    };
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    const { error } = await admin.rpc("finalize_newebpay_initial_payment", {
      p_provider_order_reference: reference, p_provider_transaction_reference: decoded.TradeNo || null,
      p_provider_period_reference: null, p_paid_amount: amount,
      p_payment_status: decoded.Status || fields.Status || "", p_payment_result: result,
    });
    if (error) {
      console.error("NEWEBPAY_MPG_FINALIZE_FAILED", error.code || "unknown");
      return response("ORDER_PROCESSING_FAILED", 400);
    }
    return response("SUCCESS");
  } catch (error) {
    console.error("NEWEBPAY_MPG_CALLBACK_FAILED", error instanceof Error ? error.message : "unknown");
    return response("INVALID_CALLBACK", 400);
  }
});
