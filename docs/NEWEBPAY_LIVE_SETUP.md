# 藍新金流正式串接部署清單

這份設定對應正式商店，採 **預設關閉** 的上線方式：程式與回呼可先部署，但不會顯示付款入口或建立任何實際付款，直到最後一項開關由管理者手動啟用。

## 已固定的付款規則

- 7 次生成包使用 MPG 單次交易。
- 訂閱方案使用信用卡定期定額：第一次立即扣完整金額，後續固定每 30 天扣款一次。
- 非 CAU 狀態下，定期定額會以 `99` 期建立；這是藍新 API 的兩位數期數上限，日後若藍新確認已啟用 CAU，才可將 `NEWEBPAY_CAU_ENABLED` 切為 `true` 使用無限期 `NE`。
- Apple Pay、Google Pay 的商店資格雖已啟用，程式預設仍帶 `0`，付款頁只開放信用卡。要另行開放時才設定 `NEWEBPAY_WALLETS_ENABLED=true`。
- 所有點數與會員權益只接受 `NotifyURL` 的加密背景通知；`ReturnURL` 僅將使用者導回方案頁，永不發放權益。

## 部署順序

1. 在 Supabase SQL Editor 執行：

   `supabase/migrations/20261102_000068_newebpay_live_checkout_foundation.sql`

2. 部署五個 Edge Functions：

   ```text
   newebpay-checkout
   newebpay-notify
   newebpay-return
   newebpay-periodic-notify
   newebpay-periodic-return
   ```

3. 在 Supabase Edge Function Secrets 新增下列值。金鑰只能貼到 Secrets，不可放進 Streamlit secrets、Git 或聊天訊息：

   ```text
   NEWEBPAY_MERCHANT_ID=<藍新商店代號>
   NEWEBPAY_HASH_KEY=<藍新 Hash Key>
   NEWEBPAY_HASH_IV=<藍新 Hash IV>
   NEWEBPAY_SITE_URL=https://sticker-studio-next.streamlit.app
   NEWEBPAY_LIVE_ENABLED=false
   NEWEBPAY_WALLETS_ENABLED=false
   NEWEBPAY_CAU_ENABLED=false
   ```

4. 到藍新後台的「API 應用 URL」設定以下正式 HTTPS 網址；請勿將 NotifyURL 與 ReturnURL 設成同一網址：

   ```text
   MPG NotifyURL: https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/newebpay-notify
   MPG ReturnURL: https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/newebpay-return
   定期定額 NotifyURL: https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/newebpay-periodic-notify
   定期定額 ReturnURL: https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/newebpay-periodic-return
   ```

5. 完成程式部署與管理者真人小額測試後，才將 Supabase 的 `NEWEBPAY_LIVE_ENABLED` 改為 `true`；並在 Streamlit Cloud 的 app secrets 加入同名值：

   ```toml
   NEWEBPAY_LIVE_ENABLED = "true"
   ```

   兩個開關都為 `true` 才會顯示前台付款入口。若任一方維持 `false`，使用者看不到付款按鈕，且 Edge Function 也拒絕產生付款表單。

## 測試時必查

- 7 次包成功、回傳頁重整、Notify 重送：同一筆訂單只能增加一次 7 次額度。
- 訂閱首次付款：立即取得新方案完整額度，並從成功時刻起算 30 天；原方案未用完的額度保留。
- 訂閱續扣 Notify：每個 `OrderNo`、`TradeNo` 只能延長一次週期與新增一次額度。
- 付款失敗：不得增加額度；既有訂閱標記為待處理，不能把失敗當作成功。
- Apple Pay／Google Pay：本階段付款頁不應出現，直到另行開啟錢包開關。
