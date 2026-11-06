import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { constantTimeEqual, decryptNewebpayMpg, newebpayCheckValue, newebpayTradeSha, parseNewebpayMpgValues, requestFields } from "../_shared/newebpay.ts";

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
const QUERY_URL = "https://core.newebpay.com/API/QueryTradeInfo";

function response(body: string, status = 200): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
}

type PendingOrder = {
  provider_order_reference: string;
  amount_twd: number;
};

async function reconcileVerifiedRecentOrders(
  admin: ReturnType<typeof createClient>,
): Promise<number> {
  // A valid Notify request can be retried by NewebPay, but has no readable
  // order reference when its TradeInfo cannot be decrypted.  Query only a
  // small, recent pending window and issue rights solely after NewebPay's
  // signed QueryTradeInfo response confirms the exact order and amount.
  const since = new Date(Date.now() - 30 * 60 * 1000).toISOString();
  const { data: orders, error } = await admin
    .from("commerce_orders")
    .select("provider_order_reference, amount_twd")
    .eq("payment_provider", "newebpay_mpg")
    .eq("status", "pending")
    .gte("created_at", since)
    .not("provider_order_reference", "is", null)
    .order("created_at", { ascending: false })
    .limit(8);
  if (error || !orders?.length) return 0;

  let finalized = 0;
  for (const order of orders as PendingOrder[]) {
    const reference = String(order.provider_order_reference || "");
    const amount = Number(order.amount_twd);
    if (!reference || !Number.isSafeInteger(amount) || amount < 1) continue;
    const form = new URLSearchParams({
      MerchantID: MERCHANT_ID,
      Version: "1.3",
      RespondType: "JSON",
      CheckValue: await newebpayCheckValue(MERCHANT_ID, reference, amount, HASH_KEY, HASH_IV),
      TimeStamp: String(Math.floor(Date.now() / 1000)),
      MerchantOrderNo: reference,
      Amt: String(amount),
    });
    const providerResponse = await fetch(QUERY_URL, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json" },
      body: form.toString(),
    });
    const providerJson = await providerResponse.json().catch(() => null) as Record<string, unknown> | null;
    const details = providerJson?.Result as Record<string, unknown> | undefined;
    const paid = providerResponse.ok && providerJson?.Status === "SUCCESS"
      && String(details?.MerchantID || "").trim() === MERCHANT_ID
      && String(details?.MerchantOrderNo || "") === reference
      && Number(details?.Amt) === amount
      && String(details?.TradeStatus || "") === "1";
    if (!paid) continue;
    const { error: finalizeError } = await admin.rpc("finalize_newebpay_initial_payment", {
      p_provider_order_reference: reference,
      p_provider_transaction_reference: String(details?.TradeNo || "") || null,
      p_provider_period_reference: null,
      p_paid_amount: amount,
      p_payment_status: "SUCCESS",
      p_payment_result: {
        source: "notify_query_fallback",
        payment_type: String(details?.PaymentType || ""),
        pay_time: String(details?.PayTime || ""),
        trade_status: String(details?.TradeStatus || ""),
      },
    });
    if (!finalizeError) finalized += 1;
  }
  return finalized;
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
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    let decoded: Record<string, string>;
    try {
      decoded = parseNewebpayMpgValues(await decryptNewebpayMpg(
        tradeInfo, HASH_KEY, HASH_IV, fields.EncryptType || "",
      ));
    } catch (decryptError) {
      const reconciled = await reconcileVerifiedRecentOrders(admin);
      console.warn(
        "NEWEBPAY_MPG_DECRYPT_FALLBACK",
        `encrypt_type=${fields.EncryptType || "0"} bytes=${tradeInfo.length / 2} reconciled=${reconciled}`,
      );
      if (reconciled > 0) return response("SUCCESS");
      throw decryptError;
    }
    if (decoded.MerchantID !== MERCHANT_ID) return response("INVALID_CALLBACK", 400);
    const reference = decoded.MerchantOrderNo || "";
    const amount = Number.parseInt(decoded.Amt || "", 10);
    if (!reference || !Number.isSafeInteger(amount)) return response("INVALID_PAYMENT", 400);
    const result = {
      status: decoded.Status || fields.Status || "", merchant_order_no: reference,
      trade_no: decoded.TradeNo || "", payment_type: decoded.PaymentType || "",
      pay_time: decoded.PayTime || "", respond_code: decoded.RespondCode || "",
    };
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
