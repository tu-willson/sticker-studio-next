# Sticker Studio Next

獨立於 `LINE_Sticker_Web_V10_TEST` 的下一代貼圖創作專案。

目前為可部署預覽版：所有圖片生成均使用訪客在當前瀏覽器 Session 輸入的 OpenAI API Key；不會讀取或呼叫舊版網站的 Supabase、每日 10 次額度或伺服器端 OpenAI Key。

## 會員 Beta 基礎（尚未上線）

[`supabase/migrations/20261015_000001_member_credit_beta.sql`](supabase/migrations/20261015_000001_member_credit_beta.sql) 是**新 Supabase 專案專用**的第一份 migration，不會連線或改動舊版資料庫。

它建立：

- Google／Supabase Auth 使用者對應的會員 profile；
- 新帳號建立時僅一次的 `+2` 體驗點數；
- 不可直接由前端寫入的點數帳本；
- 每次生成的保留、成功確認、失敗退款 RPC；
- 僅能讀取自己資料的 RLS 規則。

自備 OpenAI API Key 模式會持續保留；它不消耗會員點數，也不會把 Key 寫入資料庫。

## 上線順序

1. 建立新的 Supabase 專案並在 SQL Editor 執行 migration。
2. 在 Supabase Auth 啟用 Google Provider，並建立 Google OAuth client。
3. 將新專案的 URL 與 anon key 存入 Streamlit secrets（絕不提交 `secrets.toml`）。
4. 接上登入、點數餘額與生成前保留／失敗退款，再部署為 Beta。

舊版 `LINE_Sticker_Web_V10_TEST`、舊 Supabase 專案與其每日 10 次設定不屬於上述流程。

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```
