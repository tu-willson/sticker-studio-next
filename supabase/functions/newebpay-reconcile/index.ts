import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { newebpayCheckValue } from "../_shared/newebpay.ts";

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
const HASH_KEY = (Deno.env.get("NEWEBPAY_HASH_KEY") || "").trim();
const HASH_IV = (Deno.env.get("NEWEBPAY_HASH_IV") || "").trim();
const QUERY_URL = "https://core.newebpay.com/API/QueryTradeInfo";

function result(body: Record<string, string>, status = 200): Response {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store" } });
}

function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
}

Deno.serve(async (request) => {
  if (request.method !== "GET") return result({ status: "method_not_allowed" }, 405);
  if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !MERCHANT_ID || !HASH_KEY || !HASH_IV) {
    return result({ status: "not_configured" }, 503);
  }
  const checkoutId = new URL(request.url).searchParams.get("checkout") || "";
  if (!isUuid(checkoutId)) return result({ status: "invalid" }, 400);

  try {
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    const { data: checkout } = await admin
      .from("newebpay_checkout_sessions")
      .select("order_id")
      .eq("id", checkoutId)
      .maybeSingle();
    if (!checkout?.order_id) return result({ status: "not_found" }, 404);

    const { data: order } = await admin
      .from("commerce_orders")
      .select("id, status, payment_provider, provider_order_reference, amount_twd")
      .eq("id", checkout.order_id)
      .maybeSingle();
    if (!order || order.payment_provider !== "newebpay_mpg") return result({ status: "not_found" }, 404);
    if (order.status === "paid") return result({ status: "paid" });
    if (order.status !== "pending" || !order.provider_order_reference) return result({ status: order.status || "unavailable" });

    const timestamp = Math.floor(Date.now() / 1000);
    const form = new URLSearchParams({
      MerchantID: MERCHANT_ID,
      Version: "1.3",
      RespondType: "JSON",
      CheckValue: await newebpayCheckValue(MERCHANT_ID, order.provider_order_reference, order.amount_twd, HASH_KEY, HASH_IV),
      TimeStamp: String(timestamp),
      MerchantOrderNo: order.provider_order_reference,
      Amt: String(order.amount_twd),
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
      && String(details?.MerchantOrderNo || "") === order.provider_order_reference
      && Number(details?.Amt) === order.amount_twd
      && String(details?.TradeStatus || "") === "1";
    if (!paid) {
      // Log only provider state codes; never log payment payloads, card data, or secrets.
      console.warn(
        "NEWEBPAY_RECONCILE_UNPAID",
        `http=${providerResponse.status} status=${String(providerJson?.Status || "").slice(0, 24)} trade_status=${String(details?.TradeStatus || "").slice(0, 8)}`,
      );
      return result({ status: "pending" });
    }

    const { error } = await admin.rpc("finalize_newebpay_initial_payment", {
      p_provider_order_reference: order.provider_order_reference,
      p_provider_transaction_reference: String(details?.TradeNo || "") || null,
      p_provider_period_reference: null,
      p_paid_amount: order.amount_twd,
      p_payment_status: "SUCCESS",
      p_payment_result: {
        source: "provider_query_reconciliation",
        payment_type: String(details?.PaymentType || ""),
        pay_time: String(details?.PayTime || ""),
        trade_status: String(details?.TradeStatus || ""),
      },
    });
    if (error) {
      console.error("NEWEBPAY_RECONCILE_FINALIZE_FAILED", error.code || "unknown");
      return result({ status: "retry" }, 502);
    }
    return result({ status: "paid" });
  } catch (error) {
    console.error("NEWEBPAY_RECONCILE_FAILED", error instanceof Error ? error.name : "unknown");
    return result({ status: "retry" }, 502);
  }
});
