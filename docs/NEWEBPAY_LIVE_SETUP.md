# 藍新金流正式串接部署清單

這份設定對應正式商店，採 **預設關閉** 的上線方式：程式與回呼可先部署，但不會顯示付款入口或建立任何實際付款，直到最後一項開關由管理者手動啟用。

## 已固定的付款規則

- 基本、標準、進階創作包與 7 次生成包，全部使用 MPG **單次交易**。
- 每筆付款成功後各自建立一份 30 天權益與額度；後續加購不會延長或覆蓋舊批次，系統在付費額度中優先使用最早到期的一筆。
- 不建立信用卡定期定額、不會自動續訂，也不使用藍新的定期定額回呼。
- Apple Pay、Google Pay 的商店資格雖已啟用，程式預設仍帶 `0`，付款頁只開放信用卡。要另行開放時才設定 `NEWEBPAY_WALLETS_ENABLED=true`。
- 所有點數與會員權益只接受 `NotifyURL` 的加密背景通知；`ReturnURL` 僅將使用者導回方案頁，永不發放權益。

## 部署順序

1. 在 Supabase SQL Editor 執行：

   依序執行：

   ```text
   supabase/migrations/20261102_000068_newebpay_live_checkout_foundation.sql
   supabase/migrations/20261102_000069_one_time_creator_packs.sql
   ```

2. 部署五個 Edge Functions。建議使用本專案新增的 GitHub Actions 工作流程，避免在 Dashboard 逐一貼上有共用模組的五份程式：

   - 在 Supabase 帳號的 Access Tokens 建立一組僅供部署使用的 Token。
   - 到 GitHub repository 的 **Settings → Secrets and variables → Actions**，新增名稱為 `SUPABASE_ACCESS_TOKEN` 的 Repository secret，值為剛建立的 Token。
   - 到 **Actions → Deploy NewebPay Edge Functions → Run workflow** 手動執行。
   - 此 workflow 只有手動觸發，不會因日後 push 自動部署。

   執行後會部署以下五個 Functions：

   ```text
   newebpay-checkout
   newebpay-notify
   newebpay-return
   newebpay-periodic-notify（僅保留舊資料相容，不供新訂單使用）
   newebpay-periodic-return（僅保留舊資料相容，不供新訂單使用）
   ```

3. 在 Supabase Edge Function Secrets 新增下列值。金鑰只能貼到 Secrets，不可放進 Streamlit secrets、Git 或聊天訊息：

   ```text
   NEWEBPAY_MERCHANT_ID=<藍新商店代號>
   NEWEBPAY_HASH_KEY=<藍新 Hash Key>
   NEWEBPAY_HASH_IV=<藍新 Hash IV>
   NEWEBPAY_SITE_URL=https://sticker-studio-next.streamlit.app
   NEWEBPAY_LIVE_ENABLED=false
   NEWEBPAY_WALLETS_ENABLED=false
   ```

4. 到藍新後台的「API 應用 URL」設定以下正式 HTTPS 網址；請勿將 NotifyURL 與 ReturnURL 設成同一網址：

   ```text
   MPG NotifyURL: https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/newebpay-notify
   MPG ReturnURL: https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/newebpay-return
   ```

5. 完成程式部署與管理者真人小額測試後，才將 Supabase 的 `NEWEBPAY_LIVE_ENABLED` 改為 `true`；並在 Streamlit Cloud 的 app secrets 加入同名值：

   ```toml
   NEWEBPAY_LIVE_ENABLED = "true"
   ```

   兩個開關都為 `true` 才會顯示前台付款入口。若任一方維持 `false`，使用者看不到付款按鈕，且 Edge Function 也拒絕產生付款表單。

## 測試時必查

- 每一款創作包成功、回傳頁重整、Notify 重送：同一筆訂單只能發放一次對應額度。
- 同一創作包重複購買：每筆各自新增完整額度與 30 天效期，舊批次到期日不得被延長。
- 基本包尚餘額度後再買標準包：兩筆明細都要存在；標準權益立即生效，舊基本額度仍依原到期日使用。
- 標準包到期：若基本包仍有效，權益回落基本；若沒有有效創作包，回落免費權益，既有作品不刪除且市集只能下架既有內容。
- 付款失敗：不得增加額度，不能把失敗當作成功。
- Apple Pay／Google Pay：本階段付款頁不應出現，直到另行開啟錢包開關。
