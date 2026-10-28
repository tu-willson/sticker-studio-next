import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { encryptPayuniInfo, htmlEscape, payuniHashInfo } from "../_shared/payuni.ts";

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") || "";
const SERVICE_ROLE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || "";
const MERCHANT_ID = Deno.env.get("PAYUNI_MERCHANT_ID") || "";
const HASH_KEY = Deno.env.get("PAYUNI_HASH_KEY") || "";
const HASH_IV = Deno.env.get("PAYUNI_HASH_IV") || "";
const SITE_URL = (Deno.env.get("PAYUNI_SITE_URL") || "https://sticker-studio-next.streamlit.app").replace(/\/$/, "");
const SANDBOX_URL = "https://sandbox-api.payuni.com.tw/api/upp";

function problem(message: string, status = 400): Response {
  return new Response(`<!doctype html><meta charset="utf-8"><title>付款暫時無法開始</title><h2>付款暫時無法開始</h2><p>${htmlEscape(message)}</p>`, {
    status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

Deno.serve(async (request) => {
  if (request.method !== "GET") return problem("不支援的付款請求。", 405);
  if (!SUPABASE_URL || !SERVICE_ROLE_KEY || !MERCHANT_ID || !HASH_KEY || !HASH_IV) {
    return problem("PAYUNi 測試環境尚未完成設定。", 503);
  }
  const checkoutId = new URL(request.url).searchParams.get("checkout") || "";
  if (!/^[0-9a-f-]{36}$/i.test(checkoutId)) return problem("付款連結無效。", 404);

  const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
  const { data: session, error } = await admin
    .from("payuni_checkout_sessions")
    .select("id, user_id, expires_at, provider, commerce_orders!inner(id, status, amount_twd, provider_order_reference, product_snapshot)")
    .eq("id", checkoutId)
    .maybeSingle();
  if (error || !session) return problem("付款連結不存在或已失效。", 404);
  const order = session.commerce_orders as unknown as Record<string, unknown>;
  if (session.provider !== "payuni_sandbox" || session.expires_at <= new Date().toISOString() || order.status !== "pending") {
    return problem("此測試訂單已失效或已完成。", 410);
  }
  const { data: profile } = await admin
    .from("profiles")
    .select("email")
    .eq("id", session.user_id)
    .maybeSingle();
  const product = (order.product_snapshot || {}) as Record<string, unknown>;
  const encryptedInfo = await encryptPayuniInfo({
    MerID: MERCHANT_ID,
    MerTradeNo: String(order.provider_order_reference),
    TradeAmt: Number(order.amount_twd),
    ExpireDate: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString().slice(0, 10),
    ProdDesc: String(product.display_name || "LINE 貼圖創作室方案"),
    UsrMail: String(profile?.email || ""),
    ReturnURL: `${SUPABASE_URL}/functions/v1/payuni-return`,
    NotifyURL: `${SUPABASE_URL}/functions/v1/payuni-notify`,
    Lang: "zh-tw",
    Timestamp: Math.floor(Date.now() / 1000),
    Credit: 1,
  }, HASH_KEY, HASH_IV);
  const hashInfo = await payuniHashInfo(encryptedInfo, HASH_KEY, HASH_IV);
  const html = `<!doctype html><html lang="zh-Hant"><meta charset="utf-8"><title>前往 PAYUNi 測試付款</title><body><p>正在前往安全測試付款頁面…</p><form id="payuni" action="${SANDBOX_URL}" method="post"><input type="hidden" name="MerID" value="${htmlEscape(MERCHANT_ID)}"><input type="hidden" name="Version" value="1.0"><input type="hidden" name="EncryptInfo" value="${htmlEscape(encryptedInfo)}"><input type="hidden" name="HashInfo" value="${htmlEscape(hashInfo)}"><noscript><button type="submit">前往 PAYUNi 測試付款頁</button></noscript></form><script>document.getElementById('payuni').submit();</script></body></html>`;
  return new Response(html, { headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } });
});
