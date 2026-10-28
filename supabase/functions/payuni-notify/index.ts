import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { constantTimeEqual, decryptPayuniInfo, payuniHashInfo, payuniResultValues, requestFields } from "../_shared/payuni.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
function supabaseAdminKey(): string {
  try {
    const keys = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}") as Record<string, unknown>;
    if (typeof keys.default === "string" && keys.default) return keys.default;
  } catch {
    // Fall through to the legacy key for projects that have not migrated yet.
  }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
}

const SERVICE_ROLE_KEY = supabaseAdminKey();
const MERCHANT_ID = Deno.env.get("PAYUNI_MERCHANT_ID") || "";
const HASH_KEY = Deno.env.get("PAYUNI_HASH_KEY") || "";
const HASH_IV = Deno.env.get("PAYUNI_HASH_IV") || "";

function response(body: string, status = 200): Response {
  return new Response(body, { status, headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" } });
}

Deno.serve(async (request) => {
  if (request.method !== "POST") return response("METHOD_NOT_ALLOWED", 405);
  try {
    if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !MERCHANT_ID || !HASH_KEY || !HASH_IV) return response("NOT_CONFIGURED", 503);
    const fields = await requestFields(request);
    const encryptedInfo = fields.EncryptInfo || "";
    const hashInfo = fields.HashInfo || "";
    if (!encryptedInfo || !hashInfo) return response("INVALID_CALLBACK", 400);
    const expectedHash = await payuniHashInfo(encryptedInfo, HASH_KEY, HASH_IV);
    if (!constantTimeEqual(expectedHash, hashInfo.toUpperCase())) return response("INVALID_SIGNATURE", 400);
    const decoded = payuniResultValues(await decryptPayuniInfo(encryptedInfo, HASH_KEY, HASH_IV));
    // PAYUNi's outer callback fields are not stable between UPP variants.
    // Validate the signed, decrypted merchant id instead of rejecting a valid
    // callback because of an optional outer field.
    if (decoded.MerID && decoded.MerID !== MERCHANT_ID) return response("INVALID_CALLBACK", 400);
    const reference = decoded.MerTradeNo || decoded.MerchantOrderNo || "";
    const amount = Number.parseInt(decoded.TradeAmt || decoded.Amt || "", 10);
    if (!reference || !Number.isSafeInteger(amount)) return response("INVALID_PAYMENT", 400);
    const safeResult = {
      status: decoded.Status || fields.Status || "",
      trade_no: decoded.TradeNo || "",
      payment_type: decoded.PaymentType || "",
      pay_time: decoded.PayTime || decoded.PaymentTime || "",
      merchant_order_no: reference,
    };
    const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
    const { error } = await admin.rpc("finalize_payuni_payment", {
      p_provider_order_reference: reference,
      p_provider_transaction_reference: decoded.TradeNo || null,
      p_paid_amount: amount,
      p_payment_status: decoded.Status || fields.Status || "",
      p_payment_result: safeResult,
    });
    if (error) {
      console.error("PAYUNI_FINALIZE_FAILED", error.code || "unknown");
      return response("ORDER_PROCESSING_FAILED", 400);
    }
    console.log("PAYUNI_PAYMENT_FINALIZED", String(decoded.Status || fields.Status || ""), reference.slice(0, 8));
    return response("SUCCESS");
  } catch (error) {
    console.error("PAYUNI_CALLBACK_FAILED", error instanceof Error ? error.message : "unknown");
    return response("INVALID_CALLBACK", 400);
  }
});
