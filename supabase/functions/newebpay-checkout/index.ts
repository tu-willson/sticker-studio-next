import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { encryptNewebpayCbc, newebpayTradeSha } from "../_shared/newebpay.ts";

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
const LIVE_ENABLED = /^(1|true|yes|on)$/i.test(Deno.env.get("NEWEBPAY_LIVE_ENABLED") || "");
const WALLETS_ENABLED = /^(1|true|yes|on)$/i.test(Deno.env.get("NEWEBPAY_WALLETS_ENABLED") || "");
const CAU_ENABLED = /^(1|true|yes|on)$/i.test(Deno.env.get("NEWEBPAY_CAU_ENABLED") || "");
const SITE_URL = (Deno.env.get("NEWEBPAY_SITE_URL") || "https://sticker-studio-next.streamlit.app").replace(/\/$/, "");
const MPG_URL = "https://core.newebpay.com/MPG/mpg_gateway";
const PERIOD_URL = "https://core.newebpay.com/MPG/period";

function problem(message: string, status = 400): Response {
  return Response.json({ error: message }, { status, headers: { "Cache-Control": "no-store" } });
}

function productDescription(value: unknown): string {
  return String(value || "LINE 貼圖創作室方案")
    .replace(/[^\p{Script=Han}A-Za-z0-9 _]/gu, " ").replace(/\s+/g, " ").trim().slice(0, 50)
    || "LINE 貼圖創作室方案";
}

Deno.serve(async (request) => {
  if (request.method !== "GET") return problem("不支援的付款請求。", 405);
  // This gate remains off until the production callbacks and a live test are
  // confirmed. It prevents any accidental real charge merely from deployment.
  if (!LIVE_ENABLED || !SUPABASE_URL || !SERVICE_ROLE_KEY || !MERCHANT_ID || !HASH_KEY || !HASH_IV) {
    return problem("藍新正式付款尚未開放。", 503);
  }
  const checkoutId = new URL(request.url).searchParams.get("checkout") || "";
  if (!/^[0-9a-f-]{36}$/i.test(checkoutId)) return problem("付款連結無效。", 404);
  const admin = createClient(SUPABASE_URL, SERVICE_ROLE_KEY, { auth: { persistSession: false } });
  const { data: session, error } = await admin
    .from("newebpay_checkout_sessions")
    .select("id, user_id, expires_at, payment_flow, commerce_orders!inner(id, status, amount_twd, provider_order_reference, product_snapshot)")
    .eq("id", checkoutId).maybeSingle();
  if (error || !session) return problem("付款連結不存在或已失效。", 404);
  const order = session.commerce_orders as unknown as Record<string, unknown>;
  if (session.expires_at <= new Date().toISOString() || order.status !== "pending") {
    return problem("此訂單已失效或已完成。", 410);
  }
  const { data: profile } = await admin.from("profiles").select("email").eq("id", session.user_id).maybeSingle();
  if (!String(profile?.email || "").trim()) return problem("會員沒有可用的付款通知信箱。", 409);
  const product = (order.product_snapshot || {}) as Record<string, unknown>;
  const now = Math.floor(Date.now() / 1000);
  const isPeriodic = session.payment_flow === "periodic";
  if (isPeriodic) {
    const postData = await encryptNewebpayCbc({
      RespondType: "JSON", TimeStamp: now, Version: "1.5", LangType: "zh-Tw",
      MerOrderNo: String(order.provider_order_reference), ProdDesc: productDescription(product.display_name),
      PeriodAmt: Number(order.amount_twd), PeriodType: "D", PeriodPoint: "30",
      PeriodStartType: "2", PeriodTimes: CAU_ENABLED ? "NE" : "99",
      ReturnURL: `${SUPABASE_URL}/functions/v1/newebpay-periodic-return`,
      NotifyURL: `${SUPABASE_URL}/functions/v1/newebpay-periodic-notify`,
      BackURL: `${SITE_URL}/?payment=cancelled`, PayerEmail: String(profile?.email || ""),
      EmailModify: "0", PaymentInfo: "N", OrderInfo: "N",
    }, HASH_KEY, HASH_IV);
    return Response.json({ action: PERIOD_URL, fields: { MerchantID_: MERCHANT_ID, PostData_: postData } }, {
      headers: { "Cache-Control": "no-store" },
    });
  }
  const tradeInfo = await encryptNewebpayCbc({
    MerchantID: MERCHANT_ID, RespondType: "JSON", TimeStamp: now, Version: "2.3", P3D: "1", LangType: "zh-tw",
    MerchantOrderNo: String(order.provider_order_reference), Amt: Number(order.amount_twd),
    ItemDesc: productDescription(product.display_name), TradeLimit: "900",
    ReturnURL: `${SUPABASE_URL}/functions/v1/newebpay-return`,
    NotifyURL: `${SUPABASE_URL}/functions/v1/newebpay-notify`,
    ClientBackURL: `${SITE_URL}/?payment=cancelled`, Email: String(profile?.email || ""),
    CREDIT: "1", APPLEPAY: WALLETS_ENABLED ? "1" : "0", ANDROIDPAY: WALLETS_ENABLED ? "1" : "0", SAMSUNGPAY: "0",
  }, HASH_KEY, HASH_IV);
  return Response.json({ action: MPG_URL, fields: {
    MerchantID: MERCHANT_ID, TradeInfo: tradeInfo,
    TradeSha: await newebpayTradeSha(tradeInfo, HASH_KEY, HASH_IV), Version: "2.3", EncryptType: "0",
  } }, { headers: { "Cache-Control": "no-store" } });
});
