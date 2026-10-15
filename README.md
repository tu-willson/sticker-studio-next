# Sticker Studio Next

獨立於 `LINE_Sticker_Web_V10_TEST` 的下一代貼圖創作專案。

目前為可部署預覽版：所有圖片生成均使用訪客在當前瀏覽器 Session 輸入的 OpenAI API Key；不會讀取或呼叫舊版網站的 Supabase、每日 10 次額度或伺服器端 OpenAI Key。

## 會員 Beta

這套流程僅供**新的** Supabase 專案使用，絕不連線或改動舊版資料庫。依序執行：

1. [`20261015_000001_member_credit_beta.sql`](supabase/migrations/20261015_000001_member_credit_beta.sql)
2. [`20261016_000002_oauth_login_state.sql`](supabase/migrations/20261016_000002_oauth_login_state.sql)
3. [`20261016_000003_refund_stale_reservations.sql`](supabase/migrations/20261016_000003_refund_stale_reservations.sql)

它提供：

- Google／Supabase Auth 使用者對應的會員 profile；
- 新帳號建立時僅一次的 `+2` 體驗點數；
- 不可直接由前端寫入的點數帳本；
- 每次生成的保留、成功確認、失敗退款 RPC；
- 僅能讀取自己資料的 RLS 規則。
- 一次性、10 分鐘有效的 Google PKCE 登入狀態；
- 若瀏覽器或服務中斷，15 分鐘後於會員下次生成前自動退回未完成的保留點數。

設定 `SUPABASE_URL` 與 `SUPABASE_ANON_KEY` 後，網站會顯示 Google 登入與會員點數。只有再設定伺服器端 `OPENAI_API_KEY` 時，會員模式才會允許站方付費生成；在此之前自備 Key 模式仍可完整使用。自備 OpenAI API Key 不消耗會員點數，也不會寫入資料庫。

## 上線順序

1. 建立新的 Supabase 專案並依序在 SQL Editor 執行三份 migration。
2. 在 Supabase Auth 啟用 Google Provider，並建立 Google OAuth client。
3. 將新專案的 URL 與 anon key 存入 Streamlit secrets（絕不提交 `secrets.toml`）。
4. 部署後測試 Google 登入、首次帳號 +2 點與 RLS；確認無誤後才加入站方的 `OPENAI_API_KEY` 開啟付費生成。

舊版 `LINE_Sticker_Web_V10_TEST`、舊 Supabase 專案與其每日 10 次設定不屬於上述流程。

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```
