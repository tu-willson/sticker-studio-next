# Sticker Studio Next

獨立於 `LINE_Sticker_Web_V10_TEST` 的下一代貼圖創作專案。

目前為可部署預覽版：所有圖片生成均使用訪客在當前瀏覽器 Session 輸入的 OpenAI API Key；不會讀取或呼叫舊版網站的 Supabase、每日 10 次額度或伺服器端 OpenAI Key。

後續規劃：Google 登入、會員免費 2 次體驗額度、訂閱方案與獨立 Supabase 專案。

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```
