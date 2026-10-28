# PAYUNi 沙盒付款設定

此專案的 PAYUNi 整合預設為**沙盒測試**。正式收款不可直接開啟；須另行完成 PAYUNi 正式審核與定期扣款申請。

## 1. Supabase Edge Function secrets

在 Supabase 專案設定下列 secrets；不要將值寫進 Git、Streamlit 程式碼或聊天室。

```text
PAYUNI_MERCHANT_ID=<PAYUNi 商店代號>
PAYUNI_HASH_KEY=<PAYUNi Hash Key>
PAYUNI_HASH_IV=<PAYUNi IV Key>
PAYUNI_SITE_URL=https://sticker-studio-next.streamlit.app
```

Edge Functions 會使用 Supabase 已提供的 `SUPABASE_URL` 與 `SUPABASE_SERVICE_ROLE_KEY`。三個 PAYUNi Functions 均設為不驗證 JWT：結帳頁以一次性 UUID checkout link 授權；Notify／Return 則只接受正確 `HashInfo` 的 PAYUNi 加密回呼。

## 2. Streamlit secret

在 Streamlit Cloud 的 secrets 加入：

```toml
PAYUNI_SANDBOX_ENABLED = "true"
```

未設定或非 `true` 時，方案頁只會顯示尚未啟用，無法建立任何付款訂單。

## 3. 部署與回呼網址

部署以下 Edge Functions：

```text
payuni-checkout
payuni-notify
payuni-return
```

程式每筆訂單會自動帶入：

```text
NotifyURL=https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/payuni-notify
ReturnURL=https://iffmdhpqbezdrunwoxyh.supabase.co/functions/v1/payuni-return
```

PAYUNi NotifyURL 的有效簽章才會發放權益。ReturnURL 只讓使用者回到網站查看訂單狀態，不能發放點數。

## 4. 本輪沙盒範圍

- 信用卡一次性付款：可測試基本／標準／進階方案的一個月權益，以及 7 次生成包 30 天權益。
- 訂單金額、PAYUNi 簽章、Merchant Order No、回呼與資料庫授權都會驗證並可防重複回呼。
- 自動續訂尚未啟用。PAYUNi 的定期扣款／信用卡約定為另一項申請與 API 流程，待核准後再串接。
