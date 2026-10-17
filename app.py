# V11｜STEP 02D-3｜公開版最後操作細節整理
import re
import zipfile
import streamlit as st
from openai import OpenAI
import base64
from io import BytesIO
from PIL import Image
import random
from pathlib import Path
import json
import hashlib
import html
import urllib.request
import urllib.error
import urllib.parse
import secrets
from concurrent.futures import ThreadPoolExecutor

# ------------------------------------------------------------
# V10 使用者資料本／獨立儲存區
# 不放進 app.py 內；只要這些 JSON 檔仍在專案中，更新 app.py 不會清空。
# 先讀取新的 v10_data/，若尚未建立則相容讀取舊版根目錄 JSON。
# ------------------------------------------------------------
import time
V10_DATA_DIR = Path(__file__).with_name("v10_data")
V10_DATA_DIR.mkdir(exist_ok=True)

V10_PRESET_FILE = V10_DATA_DIR / "V10_user_presets.json"
V10_CUSTOM_PHRASE_POOL_FILE = V10_DATA_DIR / "V10_custom_phrase_pool.json"

V10_LEGACY_PRESET_FILE = Path(__file__).with_name("V10_user_presets.json")
V10_LEGACY_PHRASE_POOL_FILE = Path(__file__).with_name("V10_custom_phrase_pool.json")

def _public02a_default_settings():
    return {
        "style_custom": [""] * 10,
        "style_custom_names": [f"使用者自定{i}" for i in range(1, 11)],
        "character_custom": [""],
        "character_enabled": [False],
        "custom_phrases": [],
    }


def _public02a_normalize_settings(data):
    base = _public02a_default_settings()
    if not isinstance(data, dict):
        return base

    for key in ("style_custom", "style_custom_names"):
        value = data.get(key)
        if isinstance(value, list):
            value = [str(x) if x is not None else "" for x in value[:10]]
            value += [""] * (10 - len(value))
            base[key] = value

    # 舊版曾有 3 組需求。載入舊資料時，優先保留「已啟用且有內容」的
    # 第一組；若都未啟用，則保留第一則有內容的需求，避免使用者資料消失。
    raw_custom = data.get("character_custom")
    raw_enabled = data.get("character_enabled")
    custom_values = (
        [str(x) if x is not None else "" for x in raw_custom]
        if isinstance(raw_custom, list) else []
    )
    enabled_values = (
        [bool(x) for x in raw_enabled]
        if isinstance(raw_enabled, list) else []
    )
    if custom_values or enabled_values:
        selected_index = next(
            (
                i for i, value in enumerate(custom_values)
                if value.strip() and i < len(enabled_values) and enabled_values[i]
            ),
            None,
        )
        if selected_index is None:
            selected_index = next(
                (i for i, value in enumerate(custom_values) if value.strip()),
                0,
            )
        base["character_custom"] = [
            custom_values[selected_index] if selected_index < len(custom_values) else ""
        ]
        base["character_enabled"] = [
            enabled_values[selected_index] if selected_index < len(enabled_values) else False
        ]

    value = data.get("custom_phrases")
    if isinstance(value, list):
        base["custom_phrases"] = list(dict.fromkeys(
            str(x).strip() for x in value if str(x).strip()
        ))

    return base


def _public02a_get_settings():
    """
    取得目前使用者的完整自定義設定。

    V12 FIX｜匯出空檔案修正

    自定義風格在 V12 已改由 public02a_style_bank 作為唯一可靠來源，
    因為舊的 v10_style_custom_* widget 會在切換頁面／條件渲染後被
    Streamlit 移除或重設。

    因此匯出時不能再直接讀 legacy widget keys，
    必須從 Style Bank 取得真正已儲存的風格資料。
    """

    # ------------------------------------------------------------
    # ① 自定義風格：Style Bank 是 V12 的 authoritative source
    # ------------------------------------------------------------
    bank = _public02a_get_style_bank()

    style_custom = [
        str(bank[str(i)].get("prompt", ""))
        for i in range(1, 11)
    ]

    style_custom_names = [
        str(bank[str(i)].get("name", "")).strip() or f"使用者自定{i}"
        for i in range(1, 11)
    ]

    # ------------------------------------------------------------
    # ② 人物／場景：優先讀目前畫面中的 widget，
    #    若 widget 已被 Streamlit 清除，則回退到 durable preset store
    # ------------------------------------------------------------
    preset_store = _public02a_get_preset_store()

    character_custom = []
    character_enabled = []

    for i in range(1, 2):
        custom_key = f"v10_character_custom_{i}"
        enabled_key = f"v10_character_enabled_{i}"

        if custom_key in st.session_state:
            character_custom.append(
                str(st.session_state.get(custom_key, ""))
            )
        else:
            character_custom.append(
                str(preset_store["character_custom"][i - 1])
            )

        if enabled_key in st.session_state:
            character_enabled.append(
                bool(st.session_state.get(enabled_key, False))
            )
        else:
            character_enabled.append(
                bool(preset_store["character_enabled"][i - 1])
            )

    # ------------------------------------------------------------
    # ③ 自定義語詞：使用目前 Session 的專屬語詞池
    # ------------------------------------------------------------
    custom_phrases = list(
        dict.fromkeys(
            str(x).strip()
            for x in st.session_state.get("public02a_custom_phrases", [])
            if str(x).strip()
        )
    )

    settings = {
        "style_custom": style_custom,
        "style_custom_names": style_custom_names,
        "character_custom": character_custom,
        "character_enabled": character_enabled,
        "custom_phrases": custom_phrases,
    }

    # 最後統一正規化，確保匯出的 JSON 格式固定且可再次匯入。
    return _public02a_normalize_settings(settings)


def _public02a_apply_settings(data):
    # Streamlit widget keys cannot be modified after those widgets are instantiated.
    # Store the imported data as a pending payload; the next script run applies it
    # before the widgets are created.
    data = _public02a_normalize_settings(data)
    st.session_state["public02a_pending_import"] = data
    return True


def _public02a_default_style_bank():
    return {
        str(i): {
            "name": f"使用者自定{i}",
            "prompt": "",
        }
        for i in range(1, 11)
    }


def _public02a_normalize_style_bank(data):
    base = _public02a_default_style_bank()
    if not isinstance(data, dict):
        return base
    for i in range(1, 11):
        row = data.get(str(i), data.get(i, {}))
        if isinstance(row, dict):
            name = str(row.get("name", "") or "").strip()
            prompt = str(row.get("prompt", "") or "").strip()
            if name:
                base[str(i)]["name"] = name
            if prompt:
                base[str(i)]["prompt"] = prompt
    return base


def _public02a_get_style_bank():
    bank = _public02a_normalize_style_bank(
        st.session_state.get("public02a_style_bank")
    )
    st.session_state["public02a_style_bank"] = bank
    return bank


def _public02a_set_style_bank(bank):
    st.session_state["public02a_style_bank"] = _public02a_normalize_style_bank(bank)


def _public02a_sync_style_bank_from_widgets():
    """V12 REFACTOR compatibility shim.

    The old 10-slot conditional widgets are no longer the source of truth.
    Custom styles are now managed through one fixed editor + Style Bank.
    Keeping this function non-destructive prevents stale/blank legacy widget
    keys from overwriting saved prompts during page navigation.
    """
    return _public02a_get_style_bank()


def _v12_load_style_editor_from_slot():
    """Load the selected Style Bank slot into the single fixed editor."""
    try:
        slot = int(st.session_state.get("v12_style_editor_slot", 1))
    except Exception:
        slot = 1
    slot = max(1, min(10, slot))

    bank = _public02a_get_style_bank()
    item = bank[str(slot)]

    st.session_state["v12_style_editor_name"] = str(item.get("name", "")).strip() or f"使用者自定{slot}"
    st.session_state["v12_style_editor_prompt"] = str(item.get("prompt", ""))


def _v12_save_style_editor():
    """Save only the single editor into the selected Style Bank slot."""
    try:
        slot = int(st.session_state.get("v12_style_editor_slot", 1))
    except Exception:
        slot = 1
    slot = max(1, min(10, slot))

    bank = _public02a_get_style_bank()
    name = str(st.session_state.get("v12_style_editor_name", "")).strip() or f"使用者自定{slot}"
    prompt = str(st.session_state.get("v12_style_editor_prompt", "")).strip()

    bank[str(slot)] = {
        "name": name,
        "prompt": prompt,
    }
    _public02a_set_style_bank(bank)

    # Keep the legacy combined structure synchronized, but derive it only
    # from the authoritative Style Bank.
    _save_v10_presets()
    return True


def _public02a_init():
    # V12 FIX4：非 Widget 的個人設定 store。
    # 公開版不能直接把所有訪客資料寫進共用 v10_data，
    # 因此同一位使用者的 Session 以 public02a_user_presets 作為可靠來源。
    st.session_state.setdefault(
        "public02a_user_presets",
        {
            "style_custom": [""] * 10,
            "style_custom_names": [f"使用者自定{i}" for i in range(1, 11)],
            "character_custom": [""],
            "character_enabled": [False],
        },
    )

    # 一次性把舊版 3 組人物／場景需求安全縮併為 1 組。
    # 優先保留已啟用且有內容的項目，避免改版後意外遺失使用者設定。
    if not st.session_state.get("public02a_character_single_migrated", False):
        legacy_store = dict(st.session_state.get("public02a_user_presets") or {})
        legacy_custom = list(legacy_store.get("character_custom") or [])
        legacy_enabled = list(legacy_store.get("character_enabled") or [])
        for _i in range(1, 4):
            _custom_key = f"v10_character_custom_{_i}"
            _enabled_key = f"v10_character_enabled_{_i}"
            if _custom_key in st.session_state:
                while len(legacy_custom) < _i:
                    legacy_custom.append("")
                legacy_custom[_i - 1] = str(st.session_state.get(_custom_key, "") or "")
            if _enabled_key in st.session_state:
                while len(legacy_enabled) < _i:
                    legacy_enabled.append(False)
                legacy_enabled[_i - 1] = bool(st.session_state.get(_enabled_key, False))

        migrated = _public02a_normalize_settings({
            "character_custom": legacy_custom,
            "character_enabled": legacy_enabled,
        })
        legacy_store["character_custom"] = list(migrated["character_custom"])
        legacy_store["character_enabled"] = list(migrated["character_enabled"])
        st.session_state["public02a_user_presets"] = legacy_store
        st.session_state["v10_character_custom_1"] = migrated["character_custom"][0]
        st.session_state["v10_character_enabled_1"] = migrated["character_enabled"][0]
        st.session_state["public02a_character_single_migrated"] = True

    # FIX11: custom style prompts have their own authoritative bank.
    if "public02a_style_bank" not in st.session_state:
        _seed = _public02a_normalize_settings(
            st.session_state.get("public02a_user_presets")
        )
        _public02a_set_style_bank({
            str(i): {
                "name": _seed["style_custom_names"][i - 1],
                "prompt": _seed["style_custom"][i - 1],
            }
            for i in range(1, 11)
        })

    # Sync any still-existing editor values BEFORE Streamlit can discard
    # conditional widget state at the end of a navigation rerun.
    _public02a_sync_style_bank_from_widgets()

    defaults = _public02a_normalize_settings(
        st.session_state.get("public02a_user_presets")
    )
    _style_bank_defaults = _public02a_get_style_bank()
    for _i in range(1, 11):
        defaults["style_custom_names"][_i - 1] = _style_bank_defaults[str(_i)]["name"]
        defaults["style_custom"][_i - 1] = _style_bank_defaults[str(_i)]["prompt"]

    # Apply an imported profile BEFORE the corresponding Streamlit widgets are instantiated.
    pending = st.session_state.pop("public02a_pending_import", None)
    if pending is not None:
        pending = _public02a_normalize_settings(pending)
        st.session_state["public02a_user_presets"] = {
            "style_custom": list(pending["style_custom"]),
            "style_custom_names": list(pending["style_custom_names"]),
            "character_custom": list(pending["character_custom"]),
            "character_enabled": list(pending["character_enabled"]),
        }
        defaults = pending
        for i in range(1, 11):
            st.session_state[f"v10_style_custom_{i}"] = pending["style_custom"][i - 1]
            st.session_state[f"v10_style_name_{i}"] = pending["style_custom_names"][i - 1]
        for i in range(1, 2):
            st.session_state[f"v10_character_custom_{i}"] = pending["character_custom"][i - 1]
            st.session_state[f"v10_character_enabled_{i}"] = pending["character_enabled"][i - 1]
        st.session_state["public02a_custom_phrases"] = list(pending["custom_phrases"])
        _public02a_set_style_bank({
            str(i): {
                "name": pending["style_custom_names"][i - 1],
                "prompt": pending["style_custom"][i - 1],
            }
            for i in range(1, 11)
        })

    # V12 FIX5｜關鍵修正：
    # Streamlit 切到「我的作品」後，創作頁的 widget key 可能被移除，
    # 但 public02a_initialized 仍然是 True。
    # FIX4 之前因此不會再次執行 setdefault，造成回到創作頁時風格變空白。
    #
    # 所以每一次 rerun 都檢查「缺少的 widget key」，並從非 Widget store
    # 重新補回；已有的 key 絕不覆蓋使用者正在編輯的內容。
    for i in range(1, 11):
        st.session_state.setdefault(
            f"v10_style_custom_{i}", defaults["style_custom"][i - 1]
        )
        st.session_state.setdefault(
            f"v10_style_name_{i}", defaults["style_custom_names"][i - 1]
        )

    for i in range(1, 2):
        st.session_state.setdefault(
            f"v10_character_custom_{i}", defaults["character_custom"][i - 1]
        )
        st.session_state.setdefault(
            f"v10_character_enabled_{i}", defaults["character_enabled"][i - 1]
        )

    st.session_state.setdefault("public02a_custom_phrases", [])
    st.session_state["public02a_initialized"] = True



_public02a_init()

# 每個 Streamlit Session 都有自己的語詞池。
# 不再讀寫共用的 v10_data/V10_custom_phrase_pool.json。
V10_CUSTOM_PHRASE_POOL = st.session_state["public02a_custom_phrases"]


def _load_v10_phrase_pool():
    # 公開版：只從目前使用者 Session 取得。
    return list(st.session_state.get("public02a_custom_phrases", []))


def _save_v10_phrase_pool(values):
    # 公開版：只更新目前使用者 Session，不寫入共用檔案。
    values = list(dict.fromkeys(
        str(x).strip() for x in values if str(x).strip()
    ))
    st.session_state["public02a_custom_phrases"] = values
    return True


def _public02a_get_preset_store():
    data = st.session_state.get("public02a_user_presets")
    return _public02a_normalize_settings(data)


def _public02a_set_preset_store(data):
    normalized = _public02a_normalize_settings(data)
    st.session_state["public02a_user_presets"] = {
        "style_custom": list(normalized["style_custom"]),
        "style_custom_names": list(normalized["style_custom_names"]),
        "character_custom": list(normalized["character_custom"]),
        "character_enabled": list(normalized["character_enabled"]),
    }


def _public02a_sync_style_slot(slot: int):
    """FIX10: non-destructive sync.

    A blank conditional Streamlit widget must never erase an already saved
    custom-style prompt. Only non-empty editor values are allowed to update
    the durable preset store.
    """
    try:
        slot = int(slot)
        if not 1 <= slot <= 10:
            return

        current = _public02a_get_preset_store()
        name_key = f"v10_style_name_{slot}"
        style_key = f"v10_style_custom_{slot}"

        if name_key in st.session_state:
            name = str(st.session_state[name_key]).strip()
            if name:
                current["style_custom_names"][slot - 1] = name

        if style_key in st.session_state:
            style = str(st.session_state[style_key]).strip()
            # CRITICAL: never let an empty/recreated conditional widget erase
            # a previously saved custom-style prompt.
            if style:
                current["style_custom"][slot - 1] = style

        _public02a_set_preset_store(current)
        st.session_state["public02a_last_preset_snapshot"] = _public02a_get_preset_store()
    except Exception:
        pass

def _load_v10_presets():
    return _public02a_get_preset_store()


def _save_v10_presets():
    try:
        # V12 REFACTOR｜Style Bank is authoritative.
        # Never read legacy conditional style widgets here.
        bank = _public02a_get_style_bank()

        snapshot = {
            "style_custom": [
                str(bank[str(i)]["prompt"])
                for i in range(1, 11)
            ],
            "style_custom_names": [
                str(bank[str(i)]["name"])
                for i in range(1, 11)
            ],
            "character_custom": [
                str(st.session_state.get(f"v10_character_custom_{i}", ""))
                for i in range(1, 2)
            ],
            "character_enabled": [
                bool(st.session_state.get(f"v10_character_enabled_{i}", False))
                for i in range(1, 2)
            ],
        }

        _public02a_set_preset_store(snapshot)
        st.session_state["public02a_last_preset_snapshot"] = _public02a_get_preset_store()
        return True
    except Exception:
        return False


# 舊版 v10_data/ 仍保留在專案中，作為備份資料，不由公開版讀寫。

import streamlit.components.v1 as components

def _v11_clear_generation_state() -> None:
    """Clear only transient generation UI/session flags after a terminal outcome."""
    for _key in (
        "v11_generation_pending",
        "v11_generation_in_progress",
    ):
        st.session_state.pop(_key, None)



def _v11_user_error_message(exc: Exception) -> str:
    """Return a simple Chinese message for end users; never expose traceback."""
    msg = str(exc or "").lower()

    if isinstance(exc, TimeoutError) or "timeout" in msg or "timed out" in msg:
        return "⏱️ 生成逾時，請重新操作。"

    if "api key" in msg or "authentication" in msg or "unauthorized" in msg or "401" in msg:
        return "🔑 API Key 無法使用，請確認後重新操作。"

    if "429" in msg or "rate limit" in msg or "quota" in msg:
        return "⚠️ AI 服務目前較忙，請稍後再試。"

    if "no api key" in msg or "api key" in msg:
        return "🔑 請先確認 API Key，再重新操作。"

    if "b64_json" in msg or "沒有取得有效圖片" in msg or "圖片資料不完整" in msg:
        return "🖼️ 這次沒有取得有效圖片，請重新操作。"

    if "image" in msg or "png" in msg or "pil" in msg or "base64" in msg:
        return "🖼️ 圖片處理失敗，請重新操作。"

    return "🔴 圖片生成失敗，請稍後重新操作。"


# ============================================================
# V10 STEP 10C
# 原生 HTML Canvas 滑鼠裁切版
#
# 核心：
# - 不使用 streamlit-drawable-canvas
# - 8 個裁切框同時顯示
# - 滑鼠拖曳框中央：移動
# - 拖曳四角：縮放
# - 拖曳四邊：單方向縮放
# - 顯示座標與原圖座標分離
# - 固定預覽上限，不跟 Streamlit 容器無限放大
# - Chrome 80/100/125/150% 仍以原始圖片座標計算
# - 01～08 不會送給 AI 當作圖片編號
# ============================================================

st.set_page_config(
    page_title="LINE 貼圖創作工作室｜V12",
    page_icon="🎨",
    layout="wide",
)

# ============================================================
# V10 STEP 20｜介面重新排版
# - 大標題置中、放大
# - 每個主要區域以彩虹色分隔
# - 一般控制項限制寬度，避免滿版橫跨
# - 主要內容維持中央操作區
# ============================================================
# ============================================================
# V11｜STEP 02C-1｜FIX4
# 主題顏色改由 .streamlit/config.toml 的 Streamlit 原生 Light/Dark theme 控制。
# Python 這裡不再強制覆蓋整個 App 的背景與文字顏色。


st.markdown("""
<style>
/* V12｜④ 文字來源雙入口：維持兩個清楚、可點選的大標題。 */
.st-key-v12_open_ai_copy_panel button,
.st-key-v12_open_random_panel button{
  min-height:58px !important;
  border-radius:12px !important;
  font-size:18px !important;
  font-weight:800 !important;
}
/* 畫風參考的自訂元素需明確儲存，以紅色按鈕提高辨識度。 */
.st-key-v12_style_reference_save_button button{
  background:#ff4f67 !important;
  border-color:#ff4f67 !important;
  color:#fff !important;
  font-weight:800 !important;
}
.st-key-v12_style_reference_save_button button:hover{
  background:#e53e57 !important;
  border-color:#e53e57 !important;
}
/* 人物照與畫風參考共用醒目的粉紅色原生上傳按鈕。 */
.st-key-v12_person_photo_upload [data-testid="stFileUploaderDropzone"] button,
.st-key-v12_style_reference_upload [data-testid="stFileUploaderDropzone"] button{
  position:relative !important;
  min-height:42px !important;
  padding:0 16px !important;
  color:transparent !important;
  background:#ff4f67 !important;
  border-color:#ff4f67 !important;
  border-radius:10px !important;
  font-weight:850 !important;
  box-shadow:0 5px 13px rgba(255,79,103,.24) !important;
}
.st-key-v12_person_photo_upload [data-testid="stFileUploaderDropzone"] button::after,
.st-key-v12_style_reference_upload [data-testid="stFileUploaderDropzone"] button::after{
  content:"上傳";
  position:absolute;
  inset:0;
  display:flex;
  align-items:center;
  justify-content:center;
  color:#fff;
  font-size:1rem;
  font-weight:850;
}
.st-key-v12_person_photo_upload [data-testid="stFileUploaderDropzone"] button:hover,
.st-key-v12_style_reference_upload [data-testid="stFileUploaderDropzone"] button:hover{
  background:#e53e57 !important;
  border-color:#e53e57 !important;
}
/* 登出屬於帳號狀態操作，使用紅色警示按鈕避免誤認為一般設定。 */
.st-key-beta_logout button{
  background:#e53935 !important;
  border-color:#e53935 !important;
  color:#fff !important;
  font-weight:800 !important;
}
.st-key-beta_logout button:hover{
  background:#c62828 !important;
  border-color:#c62828 !important;
}
/* 雲端保存是影響打包等待時間的重要選項，放大以提高可見度。 */
.st-key-v12_save_to_cloud_on_package [data-testid="stCheckbox"] label p{
  font-size:19px !important;
  font-weight:800 !important;
}
@media (max-width:640px){
  .st-key-v12_save_to_cloud_on_package [data-testid="stCheckbox"] label p{
    font-size:17px !important;
  }
}
:root{
  --v10-max: 1180px;
  --v10-control: 820px;
}
/* 櫻花富士創作工作室：背景只營造氛圍，不覆蓋任何操作元件。 */
[data-testid="stAppViewContainer"]{
  position:relative;
  isolation:isolate;
  background-color:#151d2b;
  background-image:
    linear-gradient(180deg, rgba(10,16,28,.30), rgba(13,19,31,.56)),
    url("https://raw.githubusercontent.com/tu-willson/sticker-studio-next/main/static/backgrounds/sakura-fuji-dusk-v1.png");
  background-position:center center;
  background-size:cover;
  background-attachment:fixed;
}
[data-testid="stAppViewContainer"]::before,
[data-testid="stAppViewContainer"]::after{
  content:"";
  position:fixed;
  z-index:0;
  inset:-22vh -8vw;
  pointer-events:none;
  background-repeat:repeat-y;
  will-change:transform;
}
[data-testid="stAppViewContainer"]::before{
  opacity:.72;
  background-size:100% 920px;
  background-image:
    radial-gradient(ellipse 9px 15px at 6% 7%, rgba(255,151,181,.75) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 13px at 17% 24%, rgba(255,190,207,.80) 0 58%, transparent 62%),
    radial-gradient(ellipse 12px 8px at 29% 11%, rgba(250,161,188,.70) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 14px at 42% 36%, rgba(255,203,217,.88) 0 58%, transparent 62%),
    radial-gradient(ellipse 12px 8px at 56% 17%, rgba(252,148,181,.74) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 13px at 69% 42%, rgba(255,196,212,.82) 0 58%, transparent 62%),
    radial-gradient(ellipse 10px 15px at 81% 9%, rgba(248,161,188,.70) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 12px at 93% 31%, rgba(255,207,219,.86) 0 58%, transparent 62%);
  animation:v12-sakura-fall-a 35s linear infinite;
}
[data-testid="stAppViewContainer"]::after{
  opacity:.52;
  background-size:100% 1180px;
  background-image:
    radial-gradient(ellipse 8px 14px at 11% 44%, rgba(255,182,202,.86) 0 58%, transparent 62%),
    radial-gradient(ellipse 12px 8px at 24% 18%, rgba(246,137,172,.72) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 13px at 37% 55%, rgba(255,214,224,.92) 0 58%, transparent 62%),
    radial-gradient(ellipse 10px 15px at 51% 27%, rgba(249,169,193,.80) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 12px at 64% 63%, rgba(255,198,214,.88) 0 58%, transparent 62%),
    radial-gradient(ellipse 12px 8px at 76% 35%, rgba(244,145,176,.70) 0 58%, transparent 62%),
    radial-gradient(ellipse 8px 14px at 89% 57%, rgba(255,211,222,.88) 0 58%, transparent 62%);
  animation:v12-sakura-fall-b 49s linear infinite;
}
[data-testid="stAppViewContainer"] .main{
  position:relative;
  z-index:1;
  background-color:#151d2b !important;
  background-image:
    linear-gradient(180deg, rgba(10,16,28,.26), rgba(13,19,31,.50)),
    url("https://raw.githubusercontent.com/tu-willson/sticker-studio-next/main/static/backgrounds/sakura-fuji-dusk-v1.png") !important;
  background-position:center center;
  background-size:cover;
  background-attachment:fixed;
}
[data-testid="stAppViewContainer"] .block-container,
[data-testid="stHeader"]{
  position:relative;
  z-index:1;
}
@keyframes v12-sakura-fall-a{
  from{transform:translate3d(-1vw,-10vh,0) rotate(0deg)}
  to{transform:translate3d(3vw,112vh,0) rotate(18deg)}
}
@keyframes v12-sakura-fall-b{
  from{transform:translate3d(3vw,-18vh,0) rotate(12deg)}
  to{transform:translate3d(-2vw,108vh,0) rotate(-14deg)}
}
@media (prefers-reduced-motion:reduce){
  [data-testid="stAppViewContainer"]::before,
  [data-testid="stAppViewContainer"]::after{animation:none;}
}
@media (max-width:640px){
  [data-testid="stAppViewContainer"]{
    background-attachment:scroll;
    background-position:center top;
  }
  [data-testid="stAppViewContainer"] .main{
    background-attachment:scroll;
    background-position:center top;
  }
  [data-testid="stAppViewContainer"]::after{display:none;}
  [data-testid="stAppViewContainer"]::before{
    opacity:.35;
    animation-duration:44s;
  }
}
.block-container{
  max-width:1180px !important;
  padding-left:2rem !important;
  padding-right:2rem !important;
}



/* V11｜02D-3｜公開版最後操作細節整理 */
.v11-section-tip{
  width:min(900px,100%);
  margin:.35rem auto .85rem;
  padding:.6rem .85rem;
  border-radius:12px;
  border:1px solid rgba(120,120,120,.14);
  background:color-mix(in srgb,#64748b 7%, transparent);
  font-size:.96rem;
  line-height:1.55;
}
.v11-success-next{
  width:min(900px,100%);
  margin:.8rem auto 1rem;
  padding:.8rem 1rem;
  border-radius:14px;
  border:1px solid rgba(22,160,133,.25);
  background:color-mix(in srgb,#16a085 9%, transparent);
  text-align:center;
  font-weight:800;
}
/* V11｜02D-2｜生成後流程導引 */
.v11-postgen-guide{
  width:min(1080px,100%);
  margin:1rem auto 1.25rem;
  padding:1rem 1.1rem;
  border-radius:18px;
  border:1px solid rgba(120,120,120,.18);
  background:color-mix(in srgb,#16a085 8%, transparent);
}
.v11-postgen-title{
  text-align:center;
  font-size:clamp(1.15rem,2.2vw,1.55rem);
  font-weight:900;
  margin:.05rem 0 .8rem;
}
.v11-postgen-steps{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:.7rem;
}
.v11-postgen-step{
  padding:.75rem .85rem;
  border-radius:14px;
  border:1px solid rgba(120,120,120,.12);
  background:color-mix(in srgb,#ffffff 58%, transparent);
}
.v11-postgen-step strong{
  display:block;
  margin-bottom:.2rem;
  font-size:1.02rem;
}
.v11-postgen-step span{
  font-size:.96rem;
  line-height:1.5;
  opacity:.9;
}
@media (max-width:720px){
  .v11-postgen-steps{grid-template-columns:1fr;}
}
/* V11｜02D-1｜首次使用導引 */
.v11-onboarding{
  width:min(1080px,100%);
  margin:0 auto 1.2rem;
  padding:1rem 1.15rem 1.1rem;
  border-radius:18px;
  border:1px solid rgba(120,120,120,.18);
  background:linear-gradient(135deg,
    color-mix(in srgb,#7c5cff 10%, transparent),
    color-mix(in srgb,#4bb3fd 8%, transparent),
    color-mix(in srgb,#31c48d 7%, transparent));
}
.v11-onboarding-title{
  text-align:center;
  font-size:clamp(1.2rem,2.3vw,1.7rem);
  font-weight:900;
  margin:.1rem 0 .8rem;
}
.v11-onboarding-grid{
  display:grid;
  grid-template-columns:repeat(3,minmax(0,1fr));
  gap:.75rem;
}
.v11-onboarding-card{
  padding:.8rem .9rem;
  border-radius:14px;
  background:color-mix(in srgb,#ffffff 62%, transparent);
  border:1px solid rgba(120,120,120,.12);
}
.v11-onboarding-step{
  font-size:1.05rem;
  font-weight:900;
  margin-bottom:.25rem;
}
.v11-onboarding-text{
  font-size:.98rem;
  line-height:1.55;
  opacity:.9;
}
@media (max-width:720px){
  .v11-onboarding-grid{grid-template-columns:1fr;}
}
.v10-main-title{
  text-align:center;
  font-size:clamp(2rem,4vw,3.15rem);
  font-weight:900;
  letter-spacing:.04em;
  margin:1.0rem auto 1.8rem;
  white-space:nowrap;
}
@media (max-width:640px){
  .v10-main-title{
    font-size:clamp(1.7rem,8vw,2.2rem);
    letter-spacing:.015em;
  }
}
.v10-section{
  position:relative;
  width:min(1050px,100%);
  margin:2.25rem auto .7rem;
  padding:.8rem 1.05rem;
  display:flex;
  align-items:center;
  gap:.85rem;
  overflow:hidden;
  border:1px solid color-mix(in srgb,var(--accent) 48%, rgba(255,255,255,.10));
  border-left:5px solid var(--accent);
  border-radius:18px;
  background:linear-gradient(115deg,
    color-mix(in srgb,var(--accent) 19%, rgba(18,26,42,.91)),
    rgba(18,26,42,.78) 58%,
    color-mix(in srgb,var(--accent) 8%, rgba(18,26,42,.78)));
  box-shadow:0 12px 28px rgba(4,9,19,.16);
}
.v10-section::before{
  content:"";
  position:absolute;
  width:160px;
  height:160px;
  right:-72px;
  top:-92px;
  border-radius:50%;
  background:radial-gradient(circle,color-mix(in srgb,var(--accent) 34%, transparent),transparent 68%);
  pointer-events:none;
}
.v10-section::after{
  display:none;
}
.v10-section > *{position:relative;z-index:1;}
.v10-section-icon{
  flex:0 0 50px;
  width:50px;
  height:50px;
  display:grid;
  place-items:center;
  border-radius:16px;
  font-size:1.75rem;
  background:linear-gradient(145deg,color-mix(in srgb,var(--accent) 70%, #fff),var(--accent));
  box-shadow:0 7px 16px color-mix(in srgb,var(--accent) 32%, transparent),inset 0 1px 0 rgba(255,255,255,.42);
  filter:saturate(1.08);
}
.v10-section-copy{min-width:0;text-align:left;}
.v10-section-step{
  margin:0 0 .12rem;
  color:color-mix(in srgb,var(--accent) 72%, #fff);
  font-size:.72rem;
  font-weight:900;
  letter-spacing:.12em;
}
.v10-section-title{
  font-size:clamp(1.25rem,2.45vw,1.8rem);
  line-height:1.18;
  font-weight:900;
  letter-spacing:.015em;
}
.v12-journey{
  width:min(1080px,100%);
  margin:.35rem auto 1rem;
  padding:.85rem 1rem 1rem;
  border:1px solid rgba(180,197,224,.22);
  border-radius:18px;
  background:linear-gradient(135deg,rgba(17,27,45,.92),rgba(27,35,57,.79));
  box-shadow:0 12px 28px rgba(4,9,19,.16);
}
.v12-journey-heading{
  display:flex;
  align-items:center;
  justify-content:center;
  gap:.5rem;
  margin:0 0 .72rem;
  font-size:1.04rem;
  font-weight:900;
}
.v12-journey-icon{
  display:grid;
  place-items:center;
  width:27px;
  height:27px;
  border-radius:9px;
  background:linear-gradient(145deg,#ff7e94,#ff4f70);
}
.v12-journey-steps{
  display:grid;
  grid-template-columns:repeat(8,minmax(0,1fr));
  gap:.38rem;
}
.v12-journey-step{
  min-width:0;
  padding:.44rem .25rem .48rem;
  border:1px solid color-mix(in srgb,var(--journey-accent) 48%, rgba(255,255,255,.11));
  border-radius:11px;
  text-align:center;
  background:color-mix(in srgb,var(--journey-accent) 12%, rgba(255,255,255,.035));
}
.v12-journey-number{
  display:block;
  margin-bottom:.13rem;
  color:var(--journey-accent);
  font-size:.72rem;
  line-height:1;
  font-weight:950;
}
.v12-journey-label{
  display:block;
  overflow:hidden;
  color:rgba(244,247,252,.94);
  font-size:.73rem;
  font-weight:800;
  text-overflow:ellipsis;
  white-space:nowrap;
}
@media (max-width:720px){
  .v10-section{margin-top:1.7rem;padding:.72rem .8rem;gap:.65rem;border-radius:15px;}
  .v10-section-icon{flex-basis:42px;width:42px;height:42px;border-radius:13px;font-size:1.45rem;}
  .v10-section-title{font-size:1.18rem;}
  .v12-journey-steps{grid-template-columns:repeat(4,minmax(0,1fr));gap:.42rem;}
  .v12-journey-step{padding:.42rem .2rem;}
}
.v10-transparent-box{
  width:min(760px,100%);
  margin:0 auto 1rem;
  padding:.45rem .75rem;
  text-align:center;
}
/* V11｜02C-3D｜透明背景選項專用穩定 CSS scope */
.st-key-transparent_png_option{
  width:min(900px,100%);
  margin:.35rem auto 1.25rem;
  padding:.65rem 1rem;
  text-align:center;
}

.st-key-transparent_png_option [data-testid="stCheckbox"]{
  width:100% !important;
}

.st-key-transparent_png_option [data-testid="stCheckbox"] label{
  display:flex !important;
  align-items:center !important;
  justify-content:center !important;
  gap:1rem !important;
  width:100% !important;
  cursor:pointer !important;
}

.st-key-transparent_png_option [data-testid="stCheckbox"] label p{
  margin:0 !important;
  font-size:2rem !important;
  line-height:1.2 !important;
  font-weight:900 !important;
}

.st-key-transparent_png_option [data-testid="stCheckbox"] label span{
  font-size:2rem !important;
  font-weight:900 !important;
}

.st-key-transparent_png_option [data-testid="stCheckbox"] [role="checkbox"]{
  min-width:2.1rem !important;
  width:2.1rem !important;
  min-height:2.1rem !important;
  height:2.1rem !important;
  transform:scale(1.15);
  transform-origin:center;
}

.st-key-transparent_png_option [data-testid="stCheckbox"] input{
  width:2rem !important;
  height:2rem !important;
  cursor:pointer !important;
}

.v11-transparent-title{
  width:min(900px,100%);
  margin:1.25rem auto .55rem;
  padding:.7rem 1rem;
  border-left:8px solid #16a085;
  border-radius:10px;
  background:color-mix(in srgb,#16a085 10%, transparent);
  color:var(--text-color, inherit);
  font-size:2rem !important;
  line-height:1.2 !important;
  font-weight:900 !important;
  text-align:left;
}
.v10-transparent-box div[data-testid="stCheckbox"]{
  width:100% !important;
  text-align:center !important;
}
.v10-transparent-box label{
  font-size:2rem !important;
  font-weight:900 !important;
  line-height:1.25 !important;
  cursor:pointer !important;
}
.v10-transparent-box label p,
.v10-transparent-box label span{
  font-size:2rem !important;
  font-weight:900 !important;
}
.v10-transparent-box div[data-testid="stCheckbox"] input{
  width:2.25rem !important;
  height:2.25rem !important;
  transform:scale(1.35);
  transform-origin:center;
  cursor:pointer !important;
}
div[data-testid="stCheckbox"] label{
  font-size:1.12rem !important;
  font-weight:700 !important;
}
.v10-subsection{
  width:min(900px,100%);
  margin:1.25rem auto .75rem;
  padding:.65rem 1rem;
  border-left:6px solid var(--accent);
  border-radius:8px;
  background:color-mix(in srgb,var(--accent) 10%, transparent);
  font-size:1.28rem;
  font-weight:800;
  text-align:left;
}
.v12-mode-panel-heading{
  width:min(900px,100%);
  margin:1rem auto .5rem;
  padding:.5rem 1rem;
  border:2px solid var(--accent);
  border-radius:10px;
  background:color-mix(in srgb,var(--accent) 10%, transparent);
  font-size:1.22rem;
  line-height:1.35;
  font-weight:800;
  text-align:center;
}
.v10-note{
  width:min(900px,100%);
  margin:.5rem auto 1rem;
  text-align:center;
  opacity:.9;
}
.v10-control{
  width:min(var(--v10-control),100%);
  margin-left:auto !important;
  margin-right:auto !important;
}
.v10-control-wide{
  width:min(1050px,100%);
  margin-left:auto !important;
  margin-right:auto !important;
}

.v10-centered-image{
  display:flex;
  justify-content:center;
  width:100%;
}
.v10-centered-image img{
  margin-left:auto !important;
  margin-right:auto !important;
}
.v12-quick-nav{
  position:fixed;
  right:22px;
  bottom:calc(108px + env(safe-area-inset-bottom, 0px));
  z-index:9999;
  width:52px;
  height:52px;
}
.v12-quick-nav details{position:relative;}
.v12-quick-nav summary{
  list-style:none;
  width:52px;
  height:52px;
  border-radius:50%;
  display:flex;
  align-items:center;
  justify-content:center;
  text-decoration:none !important;
  font-size:26px;
  font-weight:900;
  color:#fff !important;
  background:#ff4f67;
  border:2px solid rgba(255,255,255,.78);
  box-shadow:0 8px 24px rgba(0,0,0,.35);
  transition:transform .18s ease, background .18s ease;
  cursor:pointer;
  user-select:none;
}
.v12-quick-nav summary::-webkit-details-marker{display:none;}
.v12-quick-nav summary::marker{content:"";}
.v12-quick-nav summary:hover{transform:translateY(-3px) scale(1.05);}
.v12-quick-nav details[open] summary{transform:rotate(45deg);background:#7c5cff;}
.v12-quick-nav-menu{
  position:absolute;
  right:2px;
  bottom:62px;
  width:48px;
  height:auto;
  margin:0;
  padding:0;
  display:flex;
  flex-direction:column-reverse;
  align-items:center;
  gap:6px;
}
.v12-quick-nav-link{
  position:relative;
  width:48px;
  height:48px;
  display:flex;
  flex-direction:column;
  align-items:center;
  justify-content:center;
  border-radius:50%;
  color:#fff !important;
  text-decoration:none !important;
  font-size:.72rem;
  font-weight:950;
  letter-spacing:.02em;
  background:linear-gradient(145deg,color-mix(in srgb,var(--nav-accent) 72%,#fff),var(--nav-accent));
  border:1px solid rgba(255,255,255,.72);
  box-shadow:0 7px 16px rgba(2,7,18,.34);
  pointer-events:auto;
  transition:transform .18s ease,filter .18s ease;
}
.v12-quick-nav-link:hover{color:#fff !important;transform:scale(1.12);filter:brightness(1.12);}
.v12-quick-nav-label,
.v12-quick-nav-number{display:block;line-height:1.02;text-align:center;}
.v12-quick-nav-label{font-size:.65rem;font-weight:950;}
.v12-quick-nav-number{margin-top:2px;font-size:.72rem;opacity:.94;}
html{scroll-behavior:smooth;}
[id^="v12-step-"]{scroll-margin-top:1rem;}
.v12-font-gallery-note{
  text-align:center;
  opacity:.9;
  margin:.4rem 0 .7rem;
}
.v12-settings-label{
  font-size:1.05rem;
  font-weight:800;
  margin:0 0 .55rem;
}
.v12-beta-login-title{
  margin:.65rem 0 .45rem;
  text-align:center;
  font-size:clamp(1.35rem,2.8vw,1.75rem);
  font-weight:900;
}
.v12-beta-login-copy{
  margin:.35rem auto .65rem;
  text-align:center;
  font-size:1.08rem;
  font-weight:700;
  line-height:1.55;
}
.v12-google-login-link,
.v12-google-login-link:visited{
  display:flex;
  align-items:center;
  justify-content:center;
  width:100%;
  min-height:52px;
  border:1px solid #ff4f67;
  border-radius:10px;
  background:#ff4f67;
  color:#fff !important;
  font-size:18px;
  font-weight:850;
  text-decoration:none !important;
  box-shadow:0 6px 14px rgba(255,79,103,.22);
}
.v12-google-login-link:hover{
  background:#e53e57;
  border-color:#e53e57;
  color:#fff !important;
}
.v12-pricing-hero{
  width:min(1000px,100%);
  margin:1rem auto 1.35rem;
  padding:1.35rem 1.5rem;
  text-align:center;
  border:1px solid rgba(255,128,154,.55);
  border-radius:22px;
  background:linear-gradient(135deg,rgba(255,79,103,.25),rgba(89,93,211,.19) 55%,rgba(21,34,57,.7));
  box-shadow:0 16px 34px rgba(2,7,18,.25);
}
.v12-pricing-kicker{
  color:#ffc0cc;
  font-size:.73rem;
  font-weight:950;
  letter-spacing:.16em;
}
.v12-pricing-hero h1{margin:.25rem 0 .42rem;color:#fff;font-size:clamp(1.8rem,4vw,2.6rem);}
.v12-pricing-hero p{margin:0;color:rgba(245,248,255,.88);font-weight:650;}
.v12-pricing-card{
  position:relative;
  min-height:258px;
  margin-bottom:.6rem;
  padding:1.1rem .9rem .92rem;
  overflow:hidden;
  border:1px solid rgba(177,196,233,.26);
  border-radius:17px;
  background:linear-gradient(155deg,rgba(37,50,78,.96),rgba(18,28,46,.94));
  box-shadow:0 8px 20px rgba(1,6,18,.22);
}
.v12-pricing-card-featured{border-color:rgba(255,176,79,.85);box-shadow:0 10px 26px rgba(255,143,55,.20);}
.v12-pricing-badge,.v12-pricing-current{
  display:inline-block;
  margin:0 0 .5rem;
  padding:.2rem .52rem;
  border-radius:999px;
  color:#201505;
  background:#ffbf58;
  font-size:.7rem;
  font-weight:950;
}
.v12-pricing-current{margin-left:.35rem;color:#fff;background:#39c98a;}
.v12-pricing-plan-name{color:#fff;font-size:1.25rem;font-weight:950;}
.v12-pricing-price{margin:.28rem 0 .75rem;color:#ff9cad;font-size:1.7rem;font-weight:950;line-height:1;}
.v12-pricing-price span{display:block;margin-top:.26rem;color:rgba(221,230,246,.78);font-size:.73rem;font-weight:750;}
.v12-pricing-benefit{margin:.4rem 0;color:rgba(244,247,253,.94);font-size:.87rem;font-weight:730;line-height:1.35;}
.v12-pricing-selection{
  width:min(720px,100%);
  margin:1.35rem auto;
  padding:1rem 1.15rem;
  border:1px solid rgba(255,161,179,.48);
  border-radius:16px;
  background:rgba(255,79,103,.11);
}
.v12-pricing-selection h3{text-align:center;margin:.1rem 0 .65rem;}
@media (max-width:900px){.v12-pricing-card{min-height:232px;padding:.9rem .72rem;}.v12-pricing-plan-name{font-size:1.08rem;}.v12-pricing-price{font-size:1.42rem;}.v12-pricing-benefit{font-size:.78rem;}}
@media (max-width:640px){.v12-pricing-hero{padding:1rem .8rem;border-radius:16px;}.v12-pricing-hero p{font-size:.9rem;}.v12-pricing-card{min-height:0;margin-bottom:.42rem;padding:.78rem .55rem;}.v12-pricing-plan-name{font-size:.95rem;}.v12-pricing-price{font-size:1.16rem;}.v12-pricing-benefit{font-size:.7rem;}.v12-pricing-badge,.v12-pricing-current{padding:.15rem .32rem;font-size:.57rem;}}

/* V12｜STEP 01B③｜125 種帶圖字型響應式圖卡牆 */
.v12-font-grid{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:14px;
  width:100%;
  margin:.7rem auto 1rem;
}
.v12-font-card{
  min-width:0;
  overflow:hidden;
  border:1px solid rgba(122,142,170,.28);
  border-radius:14px;
  background:rgba(255,255,255,.025);
  box-shadow:0 4px 14px rgba(0,0,0,.14);
}
.v12-font-card img{
  display:block;
  width:100%;
  aspect-ratio:1 / 1;
  object-fit:cover;
  background:#f7f7f7;
}
.v12-font-card-meta{
  display:flex;
  align-items:center;
  gap:.45rem;
  padding:.48rem .58rem .58rem;
  min-width:0;
}
.v12-font-card-no{
  flex:0 0 auto;
  font-weight:900;
  color:#a9c9ff;
  font-size:.92rem;
}
.v12-font-card-name{
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
  opacity:.88;
  font-size:.86rem;
}
@media (max-width:1000px){
  .v12-font-grid{
    grid-template-columns:repeat(3,minmax(0,1fr));
    gap:11px;
  }
}
@media (max-width:640px){
  .v12-font-grid{
    grid-template-columns:repeat(2,minmax(0,1fr));
    gap:9px;
    margin:.55rem auto .8rem;
  }
  .v12-font-card{
    border-radius:11px;
  }
  .v12-font-card-meta{
    gap:.35rem;
    padding:.4rem .45rem .48rem;
  }
  .v12-font-card-no{
    font-size:.82rem;
  }
  .v12-font-card-name{
    font-size:.78rem;
  }
}
@media (max-width:800px){
  /* 手機瀏覽器底部工具列較高，快捷鍵往上避開系統導覽列。 */
  .v12-quick-nav{
    width:46px;
    height:46px;
    right:14px;
    bottom:calc(116px + env(safe-area-inset-bottom, 0px));
  }
  .v12-quick-nav summary{
    width:46px;
    height:46px;
    font-size:23px;
  }
  .v12-quick-nav-menu{
    right:0;
    bottom:55px;
    transform:scale(.9);
    transform-origin:bottom right;
  }
}

/* V12｜STEP 01B③-5｜手機字型圖卡強制雙欄
   Streamlit 原生欄位在手機預設會折成單欄；這裡只針對字型總覽容器覆寫。 */
@media (max-width:640px){
  .st-key-v12_font_card_grid [data-testid="stHorizontalBlock"]{
    flex-direction:row !important;
    flex-wrap:wrap !important;
    gap:9px !important;
    align-items:flex-start !important;
  }
  .st-key-v12_font_card_grid [data-testid="stColumn"]{
    flex:0 0 calc(50% - 4.5px) !important;
    width:calc(50% - 4.5px) !important;
    min-width:0 !important;
  }
}

div[data-testid="stFileUploader"],
div[data-testid="stSelectbox"],
div[data-testid="stMultiSelect"],
div[data-testid="stTextInput"],
div[data-testid="stTextArea"],
div[data-testid="stNumberInput"]{
  width:min(var(--v10-control),100%) !important;
  margin-left:auto !important;
  margin-right:auto !important;
}
div[data-testid="stButton"]{
  width:min(520px,100%) !important;
  margin-left:auto !important;
  margin-right:auto !important;
}
.v10-small-button div[data-testid="stButton"]{
  width:min(360px,100%) !important;
  margin-left:auto !important;
  margin-right:auto !important;
}
div[data-testid="stCheckbox"],
div[data-testid="stRadio"]{
  width:min(var(--v10-control),100%) !important;
  margin-left:auto !important;
  margin-right:auto !important;
}
div[data-testid="stExpander"]{
  width:min(1000px,100%) !important;
  margin-left:auto !important;
  margin-right:auto !important;
}
@media (max-width:800px){
  .block-container{
    padding-left:.75rem !important;
    padding-right:.75rem !important;
  }
  .v10-section{margin-top:2rem;}
  .v10-section-title{font-size:1.55rem;}
}

/* V12 試作版｜局部文字上色：色卡使用實際背景色，不依賴 emoji 色字。 */
div[class*="st-key-v12_text_color_pick_"] button p{
  color:transparent !important;
  font-size:0 !important;
}
div[class*="st-key-v12_text_color_pick_"] button:hover{
  filter:brightness(.92) !important;
  transform:translateY(-1px);
}
div[class*="st-key-v12_text_color_pick_0_0_0"] button,
div[class*="st-key-v12_text_color_pick_0_1_0"] button,
div[class*="st-key-v12_text_color_pick_0_2_0"] button,
div[class*="st-key-v12_text_color_pick_1_0_0"] button,
div[class*="st-key-v12_text_color_pick_1_1_0"] button,
div[class*="st-key-v12_text_color_pick_1_2_0"] button,
div[class*="st-key-v12_text_color_pick_2_0_0"] button,
div[class*="st-key-v12_text_color_pick_2_1_0"] button,
div[class*="st-key-v12_text_color_pick_2_2_0"] button,
div[class*="st-key-v12_text_color_pick_3_0_0"] button,
div[class*="st-key-v12_text_color_pick_3_1_0"] button,
div[class*="st-key-v12_text_color_pick_3_2_0"] button,
div[class*="st-key-v12_text_color_pick_4_0_0"] button,
div[class*="st-key-v12_text_color_pick_4_1_0"] button,
div[class*="st-key-v12_text_color_pick_4_2_0"] button,
div[class*="st-key-v12_text_color_pick_5_0_0"] button,
div[class*="st-key-v12_text_color_pick_5_1_0"] button,
div[class*="st-key-v12_text_color_pick_5_2_0"] button,
div[class*="st-key-v12_text_color_pick_6_0_0"] button,
div[class*="st-key-v12_text_color_pick_6_1_0"] button,
div[class*="st-key-v12_text_color_pick_6_2_0"] button,
div[class*="st-key-v12_text_color_pick_7_0_0"] button,
div[class*="st-key-v12_text_color_pick_7_1_0"] button,
div[class*="st-key-v12_text_color_pick_7_2_0"] button{
  background:#FFFFFF !important;
  background-color:#FFFFFF !important;
  color:transparent !important;
  border:2px solid #666666 !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_1"] button,
div[class*="st-key-v12_text_color_pick_0_1_1"] button,
div[class*="st-key-v12_text_color_pick_0_2_1"] button,
div[class*="st-key-v12_text_color_pick_1_0_1"] button,
div[class*="st-key-v12_text_color_pick_1_1_1"] button,
div[class*="st-key-v12_text_color_pick_1_2_1"] button,
div[class*="st-key-v12_text_color_pick_2_0_1"] button,
div[class*="st-key-v12_text_color_pick_2_1_1"] button,
div[class*="st-key-v12_text_color_pick_2_2_1"] button,
div[class*="st-key-v12_text_color_pick_3_0_1"] button,
div[class*="st-key-v12_text_color_pick_3_1_1"] button,
div[class*="st-key-v12_text_color_pick_3_2_1"] button,
div[class*="st-key-v12_text_color_pick_4_0_1"] button,
div[class*="st-key-v12_text_color_pick_4_1_1"] button,
div[class*="st-key-v12_text_color_pick_4_2_1"] button,
div[class*="st-key-v12_text_color_pick_5_0_1"] button,
div[class*="st-key-v12_text_color_pick_5_1_1"] button,
div[class*="st-key-v12_text_color_pick_5_2_1"] button,
div[class*="st-key-v12_text_color_pick_6_0_1"] button,
div[class*="st-key-v12_text_color_pick_6_1_1"] button,
div[class*="st-key-v12_text_color_pick_6_2_1"] button,
div[class*="st-key-v12_text_color_pick_7_0_1"] button,
div[class*="st-key-v12_text_color_pick_7_1_1"] button,
div[class*="st-key-v12_text_color_pick_7_2_1"] button{
  background:#111111 !important;
  background-color:#111111 !important;
  color:transparent !important;
  border:2px solid #111111 !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_2"] button,
div[class*="st-key-v12_text_color_pick_0_1_2"] button,
div[class*="st-key-v12_text_color_pick_0_2_2"] button,
div[class*="st-key-v12_text_color_pick_1_0_2"] button,
div[class*="st-key-v12_text_color_pick_1_1_2"] button,
div[class*="st-key-v12_text_color_pick_1_2_2"] button,
div[class*="st-key-v12_text_color_pick_2_0_2"] button,
div[class*="st-key-v12_text_color_pick_2_1_2"] button,
div[class*="st-key-v12_text_color_pick_2_2_2"] button,
div[class*="st-key-v12_text_color_pick_3_0_2"] button,
div[class*="st-key-v12_text_color_pick_3_1_2"] button,
div[class*="st-key-v12_text_color_pick_3_2_2"] button,
div[class*="st-key-v12_text_color_pick_4_0_2"] button,
div[class*="st-key-v12_text_color_pick_4_1_2"] button,
div[class*="st-key-v12_text_color_pick_4_2_2"] button,
div[class*="st-key-v12_text_color_pick_5_0_2"] button,
div[class*="st-key-v12_text_color_pick_5_1_2"] button,
div[class*="st-key-v12_text_color_pick_5_2_2"] button,
div[class*="st-key-v12_text_color_pick_6_0_2"] button,
div[class*="st-key-v12_text_color_pick_6_1_2"] button,
div[class*="st-key-v12_text_color_pick_6_2_2"] button,
div[class*="st-key-v12_text_color_pick_7_0_2"] button,
div[class*="st-key-v12_text_color_pick_7_1_2"] button,
div[class*="st-key-v12_text_color_pick_7_2_2"] button{
  background:#F5222D !important;
  background-color:#F5222D !important;
  color:transparent !important;
  border:2px solid #F5222D !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_3"] button,
div[class*="st-key-v12_text_color_pick_0_1_3"] button,
div[class*="st-key-v12_text_color_pick_0_2_3"] button,
div[class*="st-key-v12_text_color_pick_1_0_3"] button,
div[class*="st-key-v12_text_color_pick_1_1_3"] button,
div[class*="st-key-v12_text_color_pick_1_2_3"] button,
div[class*="st-key-v12_text_color_pick_2_0_3"] button,
div[class*="st-key-v12_text_color_pick_2_1_3"] button,
div[class*="st-key-v12_text_color_pick_2_2_3"] button,
div[class*="st-key-v12_text_color_pick_3_0_3"] button,
div[class*="st-key-v12_text_color_pick_3_1_3"] button,
div[class*="st-key-v12_text_color_pick_3_2_3"] button,
div[class*="st-key-v12_text_color_pick_4_0_3"] button,
div[class*="st-key-v12_text_color_pick_4_1_3"] button,
div[class*="st-key-v12_text_color_pick_4_2_3"] button,
div[class*="st-key-v12_text_color_pick_5_0_3"] button,
div[class*="st-key-v12_text_color_pick_5_1_3"] button,
div[class*="st-key-v12_text_color_pick_5_2_3"] button,
div[class*="st-key-v12_text_color_pick_6_0_3"] button,
div[class*="st-key-v12_text_color_pick_6_1_3"] button,
div[class*="st-key-v12_text_color_pick_6_2_3"] button,
div[class*="st-key-v12_text_color_pick_7_0_3"] button,
div[class*="st-key-v12_text_color_pick_7_1_3"] button,
div[class*="st-key-v12_text_color_pick_7_2_3"] button{
  background:#FA8C16 !important;
  background-color:#FA8C16 !important;
  color:transparent !important;
  border:2px solid #FA8C16 !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_4"] button,
div[class*="st-key-v12_text_color_pick_0_1_4"] button,
div[class*="st-key-v12_text_color_pick_0_2_4"] button,
div[class*="st-key-v12_text_color_pick_1_0_4"] button,
div[class*="st-key-v12_text_color_pick_1_1_4"] button,
div[class*="st-key-v12_text_color_pick_1_2_4"] button,
div[class*="st-key-v12_text_color_pick_2_0_4"] button,
div[class*="st-key-v12_text_color_pick_2_1_4"] button,
div[class*="st-key-v12_text_color_pick_2_2_4"] button,
div[class*="st-key-v12_text_color_pick_3_0_4"] button,
div[class*="st-key-v12_text_color_pick_3_1_4"] button,
div[class*="st-key-v12_text_color_pick_3_2_4"] button,
div[class*="st-key-v12_text_color_pick_4_0_4"] button,
div[class*="st-key-v12_text_color_pick_4_1_4"] button,
div[class*="st-key-v12_text_color_pick_4_2_4"] button,
div[class*="st-key-v12_text_color_pick_5_0_4"] button,
div[class*="st-key-v12_text_color_pick_5_1_4"] button,
div[class*="st-key-v12_text_color_pick_5_2_4"] button,
div[class*="st-key-v12_text_color_pick_6_0_4"] button,
div[class*="st-key-v12_text_color_pick_6_1_4"] button,
div[class*="st-key-v12_text_color_pick_6_2_4"] button,
div[class*="st-key-v12_text_color_pick_7_0_4"] button,
div[class*="st-key-v12_text_color_pick_7_1_4"] button,
div[class*="st-key-v12_text_color_pick_7_2_4"] button{
  background:#FADB14 !important;
  background-color:#FADB14 !important;
  color:transparent !important;
  border:2px solid #FADB14 !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_5"] button,
div[class*="st-key-v12_text_color_pick_0_1_5"] button,
div[class*="st-key-v12_text_color_pick_0_2_5"] button,
div[class*="st-key-v12_text_color_pick_1_0_5"] button,
div[class*="st-key-v12_text_color_pick_1_1_5"] button,
div[class*="st-key-v12_text_color_pick_1_2_5"] button,
div[class*="st-key-v12_text_color_pick_2_0_5"] button,
div[class*="st-key-v12_text_color_pick_2_1_5"] button,
div[class*="st-key-v12_text_color_pick_2_2_5"] button,
div[class*="st-key-v12_text_color_pick_3_0_5"] button,
div[class*="st-key-v12_text_color_pick_3_1_5"] button,
div[class*="st-key-v12_text_color_pick_3_2_5"] button,
div[class*="st-key-v12_text_color_pick_4_0_5"] button,
div[class*="st-key-v12_text_color_pick_4_1_5"] button,
div[class*="st-key-v12_text_color_pick_4_2_5"] button,
div[class*="st-key-v12_text_color_pick_5_0_5"] button,
div[class*="st-key-v12_text_color_pick_5_1_5"] button,
div[class*="st-key-v12_text_color_pick_5_2_5"] button,
div[class*="st-key-v12_text_color_pick_6_0_5"] button,
div[class*="st-key-v12_text_color_pick_6_1_5"] button,
div[class*="st-key-v12_text_color_pick_6_2_5"] button,
div[class*="st-key-v12_text_color_pick_7_0_5"] button,
div[class*="st-key-v12_text_color_pick_7_1_5"] button,
div[class*="st-key-v12_text_color_pick_7_2_5"] button{
  background:#52C41A !important;
  background-color:#52C41A !important;
  color:transparent !important;
  border:2px solid #52C41A !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_6"] button,
div[class*="st-key-v12_text_color_pick_0_1_6"] button,
div[class*="st-key-v12_text_color_pick_0_2_6"] button,
div[class*="st-key-v12_text_color_pick_1_0_6"] button,
div[class*="st-key-v12_text_color_pick_1_1_6"] button,
div[class*="st-key-v12_text_color_pick_1_2_6"] button,
div[class*="st-key-v12_text_color_pick_2_0_6"] button,
div[class*="st-key-v12_text_color_pick_2_1_6"] button,
div[class*="st-key-v12_text_color_pick_2_2_6"] button,
div[class*="st-key-v12_text_color_pick_3_0_6"] button,
div[class*="st-key-v12_text_color_pick_3_1_6"] button,
div[class*="st-key-v12_text_color_pick_3_2_6"] button,
div[class*="st-key-v12_text_color_pick_4_0_6"] button,
div[class*="st-key-v12_text_color_pick_4_1_6"] button,
div[class*="st-key-v12_text_color_pick_4_2_6"] button,
div[class*="st-key-v12_text_color_pick_5_0_6"] button,
div[class*="st-key-v12_text_color_pick_5_1_6"] button,
div[class*="st-key-v12_text_color_pick_5_2_6"] button,
div[class*="st-key-v12_text_color_pick_6_0_6"] button,
div[class*="st-key-v12_text_color_pick_6_1_6"] button,
div[class*="st-key-v12_text_color_pick_6_2_6"] button,
div[class*="st-key-v12_text_color_pick_7_0_6"] button,
div[class*="st-key-v12_text_color_pick_7_1_6"] button,
div[class*="st-key-v12_text_color_pick_7_2_6"] button{
  background:#1677FF !important;
  background-color:#1677FF !important;
  color:transparent !important;
  border:2px solid #1677FF !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

div[class*="st-key-v12_text_color_pick_0_0_7"] button,
div[class*="st-key-v12_text_color_pick_0_1_7"] button,
div[class*="st-key-v12_text_color_pick_0_2_7"] button,
div[class*="st-key-v12_text_color_pick_1_0_7"] button,
div[class*="st-key-v12_text_color_pick_1_1_7"] button,
div[class*="st-key-v12_text_color_pick_1_2_7"] button,
div[class*="st-key-v12_text_color_pick_2_0_7"] button,
div[class*="st-key-v12_text_color_pick_2_1_7"] button,
div[class*="st-key-v12_text_color_pick_2_2_7"] button,
div[class*="st-key-v12_text_color_pick_3_0_7"] button,
div[class*="st-key-v12_text_color_pick_3_1_7"] button,
div[class*="st-key-v12_text_color_pick_3_2_7"] button,
div[class*="st-key-v12_text_color_pick_4_0_7"] button,
div[class*="st-key-v12_text_color_pick_4_1_7"] button,
div[class*="st-key-v12_text_color_pick_4_2_7"] button,
div[class*="st-key-v12_text_color_pick_5_0_7"] button,
div[class*="st-key-v12_text_color_pick_5_1_7"] button,
div[class*="st-key-v12_text_color_pick_5_2_7"] button,
div[class*="st-key-v12_text_color_pick_6_0_7"] button,
div[class*="st-key-v12_text_color_pick_6_1_7"] button,
div[class*="st-key-v12_text_color_pick_6_2_7"] button,
div[class*="st-key-v12_text_color_pick_7_0_7"] button,
div[class*="st-key-v12_text_color_pick_7_1_7"] button,
div[class*="st-key-v12_text_color_pick_7_2_7"] button{
  background:#722ED1 !important;
  background-color:#722ED1 !important;
  color:transparent !important;
  border:2px solid #722ED1 !important;
  box-shadow:0 0 0 1px rgba(128,128,128,.18) inset !important;
  min-height:52px !important;
  border-radius:11px !important;
}

@media (max-width:640px){
  div[class*="st-key-v12_text_color_pick_"] button{
    min-height:48px !important;
  }
}

/* V12 試作版｜目前選中的色卡：外框圈選 + 勾勾。
   白色色卡使用深色勾勾與深色外圈，避免在白色背景上看不見。 */
div[class*="st-key-v12_color_selected_"] button{
  position:relative !important;
  border:3px solid #FFFFFF !important;
  box-shadow:0 0 0 3px rgba(255,255,255,.95), 0 3px 10px rgba(0,0,0,.22) !important;
  transform:translateY(-1px) !important;
}
div[class*="st-key-v12_color_selected_"] button::after{
  content:"✓" !important;
  position:absolute !important;
  left:50% !important;
  top:50% !important;
  width:24px !important;
  height:24px !important;
  display:flex !important;
  align-items:center !important;
  justify-content:center !important;
  transform:translate(-50%,-50%) !important;
  border-radius:50% !important;
  background:rgba(0,0,0,.24) !important;
  color:#FFFFFF !important;
  font-size:18px !important;
  font-weight:900 !important;
  line-height:1 !important;
  pointer-events:none !important;
}
div[class*="st-key-v12_color_selected_white_"] button{
  border:3px solid #111111 !important;
  box-shadow:0 0 0 3px rgba(255,255,255,.98), 0 0 0 6px #111111, 0 3px 10px rgba(0,0,0,.22) !important;
}
div[class*="st-key-v12_color_selected_white_"] button::after{
  color:#111111 !important;
  background:rgba(255,255,255,.88) !important;
}

</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="v12-quick-nav" aria-label="創作步驟快捷鍵">
  <details>
    <summary title="開啟創作步驟快捷鍵" aria-label="開啟創作步驟快捷鍵">✦</summary>
    <nav class="v12-quick-nav-menu" aria-label="移動到創作步驟">
      <a class="v12-quick-nav-link" style="--nav-accent:#ff5c7a" href="#v12-step-01" title="01 人物照片"><span class="v12-quick-nav-label">人物</span><span class="v12-quick-nav-number">01</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#ff9f43" href="#v12-step-02" title="02 貼圖風格"><span class="v12-quick-nav-label">風格</span><span class="v12-quick-nav-number">02</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#2ecc71" href="#v12-step-03" title="03 人物特色"><span class="v12-quick-nav-label">特色</span><span class="v12-quick-nav-number">03</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#3498db" href="#v12-step-04" title="04 貼圖內容"><span class="v12-quick-nav-label">文字</span><span class="v12-quick-nav-number">04</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#8e67d8" href="#v12-step-05" title="05 背景設定"><span class="v12-quick-nav-label">背景</span><span class="v12-quick-nav-number">05</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#e67e22" href="#v12-step-06" title="06 開始生成"><span class="v12-quick-nav-label">生成</span><span class="v12-quick-nav-number">06</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#32c6c0" href="#v12-step-07" title="07 檢視成果"><span class="v12-quick-nav-label">成果</span><span class="v12-quick-nav-number">07</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#e74c3c" href="#v12-step-08" title="08 裁切下載"><span class="v12-quick-nav-label">裁切</span><span class="v12-quick-nav-number">08</span></a>
    </nav>
  </details>
</div>
""", unsafe_allow_html=True)

def v10_section(title, accent="#7c4dff"):
    """Render every major area as one consistent, compact creation-step card."""
    title = str(title or "")
    matched = re.match(r"^(\S+)\s+([①②③④⑤⑥⑦⑧])\s+(.+)$", title)
    flow_names = {
        "①": "素材準備", "②": "畫風設定", "③": "人物設計", "④": "貼圖內容",
        "⑤": "畫面設定", "⑥": "開始生成", "⑦": "成果確認", "⑧": "裁切輸出",
    }
    if matched:
        icon, circled_step, label = matched.groups()
        step_number = "①②③④⑤⑥⑦⑧".index(circled_step) + 1
        eyebrow = f"STEP {step_number:02d} · {flow_names[circled_step]}"
        section_id = f' id="v12-step-{step_number:02d}"'
    else:
        parts = title.split(maxsplit=1)
        icon = parts[0] if parts else "✨"
        label = parts[1] if len(parts) > 1 else title
        eyebrow = "創作工具"
        section_id = ""
    st.markdown(
        f'<div class="v10-section"{section_id} style="--accent:{accent}">'
        f'<div class="v10-section-icon">{html.escape(icon)}</div>'
        f'<div class="v10-section-copy">'
        f'<div class="v10-section-step">{html.escape(eyebrow)}</div>'
        f'<div class="v10-section-title">{html.escape(label)}</div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

def v10_subsection(title, accent="#4f8cff"):
    st.markdown(
        f'<div class="v10-subsection" style="--accent:{accent}">{title}</div>',
        unsafe_allow_html=True,
    )

def v12_mode_panel_heading(title, accent="#3498db"):
    st.markdown(
        f'<div class="v12-mode-panel-heading" style="--accent:{accent}">{title}</div>',
        unsafe_allow_html=True,
    )

# ============================================================
# Sticker Studio Next｜獨立預覽版／會員 Beta
#
# 這個專案不讀取、不呼叫舊版 LINE Sticker Web 的 Supabase 或網站
# OpenAI Key。本專案只使用新的 Supabase、Google 登入與點數帳本，
# 由站方 API 服務已登入的會員。
# ============================================================
BETA_API_MODE = "✨ 會員 Beta（登入享 2 次）"
BETA_SITE_URL = "https://sticker-studio-next.streamlit.app"


def _beta_config():
    """Read only the new project's public Supabase settings from Streamlit Secrets."""
    try:
        url = str(st.secrets.get("SUPABASE_URL", "") or "").strip().rstrip("/")
        anon_key = str(st.secrets.get("SUPABASE_ANON_KEY", "") or "").strip()
    except Exception:
        return None
    if not url.startswith("https://") or not anon_key:
        return None
    return {"url": url, "anon_key": anon_key}


def _beta_site_openai_key():
    """The server-side key is optional until the member-generation Beta is enabled."""
    try:
        return str(st.secrets.get("OPENAI_API_KEY", "") or "").strip()
    except Exception:
        return ""


def _beta_http(method, path, payload=None, access_token=None):
    """Call the new Supabase project without exposing server or transport errors."""
    config = _beta_config()
    if not config:
        raise RuntimeError("beta_not_configured")

    body = None
    headers = {
        "apikey": config["anon_key"],
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    request = urllib.request.Request(
        f"{config['url']}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        raise RuntimeError("beta_request_failed") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("beta_request_failed") from exc

    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("beta_invalid_response") from exc


def _beta_rpc(function_name, payload, access_token=None):
    return _beta_http(
        "POST",
        f"/rest/v1/rpc/{urllib.parse.quote(function_name, safe='')}",
        payload=payload,
        access_token=access_token,
    )


def _beta_raw_http(method, path, body=None, access_token=None,
                   content_type=None, extra_headers=None):
    """Authenticated binary Storage requests; never use a service-role key."""
    config = _beta_config()
    if not config:
        raise RuntimeError("beta_not_configured")
    headers = {"apikey": config["anon_key"]}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if content_type:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(
        f"{config['url']}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError("beta_storage_request_failed") from exc


def _beta_storage_upload(session, object_path, content, content_type):
    if not content:
        raise RuntimeError("library_asset_empty")
    return _beta_raw_http(
        "POST",
        "/storage/v1/object/member-projects/" + urllib.parse.quote(object_path, safe="/"),
        body=bytes(content),
        access_token=session["access_token"],
        content_type=content_type,
        extra_headers={"x-upsert": "false"},
    )


def _beta_storage_download(session, object_path):
    return _beta_raw_http(
        "GET",
        "/storage/v1/object/member-projects/" + urllib.parse.quote(object_path, safe="/"),
        access_token=session["access_token"],
    )


def _beta_storage_delete(session, object_paths):
    paths = [str(path) for path in object_paths if path]
    if not paths:
        return
    _beta_http(
        "DELETE",
        "/storage/v1/object/member-projects",
        payload={"prefixes": paths},
        access_token=session["access_token"],
    )


def _beta_library_status(session):
    response = _beta_rpc("get_my_library_status", {}, access_token=session["access_token"])
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("library_status_invalid")
    return {
        "saved_count": max(0, int(response.get("saved_count", 0) or 0)),
        "library_capacity": max(1, int(response.get("library_capacity", 5) or 5)),
    }


def _beta_list_library_projects(session):
    query = (
        "/rest/v1/library_projects?select=id,title,metadata,grid_path,package_path,"
        "preview_path,sticker_prefix,source_photo_path,style_reference_path,"
        "created_at,updated_at,completed_at&status=eq.active"
        "&order=created_at.desc"
    )
    response = _beta_http("GET", query, access_token=session["access_token"])
    return response if isinstance(response, list) else []


def _v12_webp_bytes(image_or_bytes, quality=86):
    """Compact persistent project assets without altering the downloadable PNG ZIP."""
    image = image_or_bytes
    if not isinstance(image, Image.Image):
        image = Image.open(BytesIO(bytes(image_or_bytes)))
    image = image.convert("RGBA")
    buf = BytesIO()
    image.save(buf, "WEBP", quality=quality, method=6)
    return buf.getvalue()


def _v12_jpeg_bytes(image_or_bytes, quality=90):
    """Restore a browser-safe JPEG input for the style-reference image API slot."""
    image = image_or_bytes
    if not isinstance(image, Image.Image):
        image = Image.open(BytesIO(bytes(image_or_bytes)))
    buf = BytesIO()
    image.convert("RGB").save(buf, "JPEG", quality=quality, optimize=True)
    return buf.getvalue()


def _beta_library_metadata(record):
    """Keep project settings only; asset bytes and all session-only data stay out."""
    metadata = _v12_normalize_project_metadata(record)
    keep = (
        "project_id", "project_name", "created_at", "updated_at", "style_name",
        "font_name", "text_style", "generation_method", "transparent_background",
        "style_mode", "custom_style_snapshot", "character_features",
        "custom_character", "custom_character_enabled",
        "sticker_mode", "expression_specs",
        "sticker_texts", "sticker_action_prompts", "sticker_color_segments", "schema_version", "identity",
        "timestamps", "creative_settings", "package_settings", "main_no", "tab_no",
    )
    result = {key: metadata[key] for key in keep if key in metadata}
    asset_manifest = record.get("cloud_asset_manifest")
    if isinstance(asset_manifest, dict):
        result["cloud_asset_manifest"] = asset_manifest
    return result


def _beta_save_library_project(session, record, progress_callback=None):
    """Save only selected final assets plus optional private creation materials."""
    def _report(progress, message):
        if progress_callback:
            progress_callback(progress, message)

    stickers = record.get("sticker_bytes")
    if not isinstance(stickers, dict) or not stickers:
        raise RuntimeError("library_selected_stickers_missing")
    selected_stickers = [
        (str(number), bytes(image_bytes))
        for number, image_bytes in sorted(
            stickers.items(),
            key=lambda item: (
                0, int(str(item[0]))
            ) if str(item[0]).isdigit() else (1, str(item[0])),
        )
        if isinstance(image_bytes, (bytes, bytearray)) and image_bytes
    ]
    if not selected_stickers:
        raise RuntimeError("library_selected_stickers_missing")

    _report(5, "正在建立雲端作品保存空間…")
    metadata = _beta_library_metadata(record)
    project_id = str(metadata.get("project_id") or __import__("uuid").uuid4())
    title = str(metadata.get("project_name") or _v12_default_project_name())[:80]
    source_photo = bytes(record.get("source_photo_bytes") or b"")
    style_reference = bytes(record.get("style_reference_bytes") or b"")
    manifest = {
        "version": 1,
        "stickers": [
            {"number": number, "path": f"stickers/{index:03d}.png"}
            for index, (number, _) in enumerate(selected_stickers, start=1)
        ],
        "has_source_photo": bool(source_photo),
        "has_style_reference": bool(style_reference),
        "main_asset_index": int(record.get("cloud_main_asset_index", 1) or 1),
        "tab_asset_index": int(record.get("cloud_tab_asset_index", 1) or 1),
    }
    metadata["cloud_asset_manifest"] = manifest
    slot = _beta_rpc(
        "create_library_project_slot",
        {"p_project_id": project_id, "p_title": title, "p_metadata": metadata},
        access_token=session["access_token"],
    )
    if isinstance(slot, list):
        slot = slot[0] if slot else {}
    if not isinstance(slot, dict):
        raise RuntimeError("library_slot_invalid")
    if not slot.get("project_id"):
        return {
            "saved": False,
            "saved_count": int(slot.get("saved_count", 0) or 0),
            "library_capacity": int(slot.get("library_capacity", 5) or 5),
        }

    user_id = str(session.get("user", {}).get("id", "") or "")
    if not user_id:
        raise RuntimeError("beta_invalid_session")
    prefix = f"{user_id}/{project_id}/"
    sticker_paths = [
        prefix + item["path"] for item in manifest["stickers"]
    ]
    paths = [prefix + "preview.webp", *sticker_paths]
    if source_photo:
        paths.append(prefix + "source-photo.webp")
    if style_reference:
        paths.append(prefix + "style-reference.webp")
    _report(15, "正在壓縮作品預覽圖…")
    preview_webp = _v12_webp_bytes(record.get("main_bytes") or selected_stickers[0][1])
    source_webp = _v12_webp_bytes(source_photo, quality=90) if source_photo else b""
    style_webp = _v12_webp_bytes(style_reference, quality=90) if style_reference else b""
    total_sticker_bytes = sum(len(image_bytes) for _, image_bytes in selected_stickers)
    try:
        _report(25, "正在上傳已選貼圖…")
        for index, ((_, image_bytes), path) in enumerate(
            zip(selected_stickers, sticker_paths), start=1
        ):
            _beta_storage_upload(session, path, image_bytes, "image/png")
            progress = 25 + round(50 * index / len(selected_stickers))
            _report(progress, f"正在上傳已選貼圖 {index}／{len(selected_stickers)}…")
        _report(78, "正在上傳作品預覽圖…")
        _beta_storage_upload(session, prefix + "preview.webp", preview_webp, "image/webp")
        if source_webp:
            _report(85, "正在保存私人創作素材…")
            _beta_storage_upload(session, prefix + "source-photo.webp", source_webp, "image/webp")
        if style_webp:
            _report(89, "正在保存畫風參考…")
            _beta_storage_upload(session, prefix + "style-reference.webp", style_webp, "image/webp")
        _report(94, "正在確認雲端作品資料…")
        _beta_rpc(
            "complete_library_project_upload",
            {
                "p_project_id": project_id,
                "p_preview_bytes": len(preview_webp),
                "p_sticker_bytes": total_sticker_bytes,
                "p_source_photo_bytes": len(source_webp),
                "p_style_reference_bytes": len(style_webp),
            },
            access_token=session["access_token"],
        )
    except Exception:
        # The slot remains only if cleanup itself fails; it is reclaimed server-side
        # after 30 minutes and never appears as a finished member project.
        try:
            _beta_storage_delete(session, paths)
            _beta_rpc(
                "finalize_my_library_project_delete",
                {"p_project_id": project_id},
                access_token=session["access_token"],
            )
        except Exception:
            pass
        raise
    _report(100, "雲端作品保存完成！")
    return {
        "saved": True,
        "saved_count": int(slot.get("saved_count", 0) or 0),
        "library_capacity": int(slot.get("library_capacity", 5) or 5),
    }


def _beta_load_library_project_record(session, project):
    """Download only the selected assets needed to resume a saved project."""
    project = dict(project or {})
    metadata = project.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    manifest = metadata.get("cloud_asset_manifest")

    # Legacy records retain their original 4×2 grid and optional ZIP.
    if not isinstance(manifest, dict) or not isinstance(manifest.get("stickers"), list):
        grid_path = str(project.get("grid_path") or "")
        if not grid_path:
            raise RuntimeError("library_assets_missing")
        record = dict(metadata)
        record.update(project)
        grid_webp = _beta_storage_download(session, grid_path)
        record["grid_bytes"] = _v12_png_bytes(Image.open(BytesIO(grid_webp)))
        return record

    prefix = str(project.get("sticker_prefix") or "")
    if not prefix:
        raise RuntimeError("library_assets_missing")
    download_paths = {}
    for position, item in enumerate(manifest["stickers"], start=1):
        if not isinstance(item, dict):
            continue
        relative_path = str(item.get("path") or "")
        if not relative_path:
            continue
        download_paths[f"sticker:{position:02d}"] = (
            prefix.rsplit("stickers/", 1)[0] + relative_path
        )
    preview_path = str(project.get("preview_path") or "")
    if preview_path:
        download_paths["preview"] = preview_path
    asset_root = prefix.rsplit("stickers/", 1)[0]
    if manifest.get("has_source_photo"):
        download_paths["source_photo"] = str(
            project.get("source_photo_path") or asset_root + "source-photo.webp"
        )
    if manifest.get("has_style_reference"):
        download_paths["style_reference"] = str(
            project.get("style_reference_path") or asset_root + "style-reference.webp"
        )
    if not any(name.startswith("sticker:") for name in download_paths):
        raise RuntimeError("library_assets_missing")

    # 雲端作品通常有多張精選貼圖；改為並行下載以避免每張網路往返累加。
    with ThreadPoolExecutor(max_workers=min(6, len(download_paths))) as executor:
        futures = {
            name: executor.submit(_beta_storage_download, session, object_path)
            for name, object_path in download_paths.items()
        }
        downloaded_assets = {
            name: future.result() for name, future in futures.items()
        }

    sticker_bytes = {
        name.split(":", 1)[1]: asset
        for name, asset in downloaded_assets.items()
        if name.startswith("sticker:")
    }
    if not sticker_bytes:
        raise RuntimeError("library_assets_missing")

    record = dict(metadata)
    record.update(project)
    record["grid_bytes"] = b""
    record["sticker_bytes"] = sticker_bytes
    record["cloud_asset_manifest"] = manifest
    if downloaded_assets.get("preview"):
        record["main_bytes"] = _v12_png_bytes(
            Image.open(BytesIO(downloaded_assets["preview"]))
        )
    if downloaded_assets.get("source_photo"):
        # 雲端為節省空間保存 WEBP；載回時轉回 PNG，與 images.edit 的
        # image/png 宣告保持一致，避免照片雖下載成功卻無法再次生成。
        record["source_photo_bytes"] = _v12_png_bytes(
            Image.open(BytesIO(downloaded_assets["source_photo"]))
        )
    if downloaded_assets.get("style_reference"):
        record["style_reference_bytes"] = _v12_jpeg_bytes(
            Image.open(BytesIO(downloaded_assets["style_reference"]))
        )
    return record


def _beta_delete_library_project(session, project_id):
    response = _beta_rpc(
        "get_my_library_project_delete_paths",
        {"p_project_id": str(project_id)},
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("library_project_not_found")
    _beta_storage_delete(session, response.get("object_paths", []))
    _beta_rpc(
        "finalize_my_library_project_delete",
        {"p_project_id": str(project_id)},
        access_token=session["access_token"],
    )


def _beta_b64url(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _beta_new_login_url():
    """Create a one-time PKCE login attempt, then return the Google authorize URL."""
    config = _beta_config()
    if not config:
        raise RuntimeError("beta_not_configured")

    state = _beta_b64url(secrets.token_bytes(32))
    code_verifier = _beta_b64url(secrets.token_bytes(64))
    code_challenge = _beta_b64url(hashlib.sha256(code_verifier.encode("ascii")).digest())
    state_digest = hashlib.sha256(state.encode("ascii")).hexdigest()
    _beta_rpc(
        "create_oauth_login_state",
        {"p_state_digest": state_digest, "p_code_verifier": code_verifier},
    )

    redirect_to = f"{BETA_SITE_URL}?{urllib.parse.urlencode({'oauth_state': state})}"
    params = urllib.parse.urlencode(
        {
            "provider": "google",
            "redirect_to": redirect_to,
            "code_challenge": code_challenge,
            "code_challenge_method": "s256",
        }
    )
    return f"{config['url']}/auth/v1/authorize?{params}"


def _beta_store_auth_session(token_response, user):
    access_token = str(token_response.get("access_token", "") or "")
    refresh_token = str(token_response.get("refresh_token", "") or "")
    if not access_token or not refresh_token or not isinstance(user, dict):
        raise RuntimeError("beta_invalid_session")
    try:
        expires_in = max(60, int(token_response.get("expires_in", 3600)))
    except (TypeError, ValueError):
        expires_in = 3600
    st.session_state["beta_auth_session"] = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": time.time() + expires_in,
        "user": user,
    }


def _beta_verify_access_token(access_token):
    user = _beta_http("GET", "/auth/v1/user", access_token=access_token)
    if not isinstance(user, dict) or not user.get("id"):
        raise RuntimeError("beta_invalid_session")
    return user


def _beta_current_user():
    """Refresh when needed, then verify the token with Supabase before using credits."""
    session = st.session_state.get("beta_auth_session")
    if not isinstance(session, dict):
        return None

    try:
        if float(session.get("expires_at", 0)) - time.time() < 90:
            token_response = _beta_http(
                "POST",
                "/auth/v1/token?grant_type=refresh_token",
                payload={"refresh_token": str(session.get("refresh_token", "") or "")},
            )
            user = _beta_verify_access_token(str(token_response.get("access_token", "") or ""))
            _beta_store_auth_session(token_response, user)
            session = st.session_state["beta_auth_session"]
        else:
            user = _beta_verify_access_token(str(session.get("access_token", "") or ""))
            session["user"] = user
        return session
    except Exception:
        st.session_state.pop("beta_auth_session", None)
        return None


def _beta_complete_oauth_callback():
    """Consume a PKCE verifier once and exchange the returned authorization code."""
    code = str(st.query_params.get("code", "") or "")
    state = str(st.query_params.get("oauth_state", "") or "")
    if not code or not state:
        return

    try:
        state_digest = hashlib.sha256(state.encode("ascii")).hexdigest()
        code_verifier = _beta_rpc(
            "consume_oauth_login_state", {"p_state_digest": state_digest}
        )
        if not isinstance(code_verifier, str) or not code_verifier:
            raise RuntimeError("beta_invalid_login_state")
        token_response = _beta_http(
            "POST",
            "/auth/v1/token?grant_type=pkce",
            payload={"auth_code": code, "code_verifier": code_verifier},
        )
        user = _beta_verify_access_token(str(token_response.get("access_token", "") or ""))
        _beta_store_auth_session(token_response, user)
        st.session_state.pop("beta_pending_login_url", None)
        st.query_params.clear()
        st.rerun()
    except Exception:
        st.session_state["beta_login_error"] = "Google 登入未完成，請重新開始登入。"
        st.query_params.clear()


def _beta_credit_balance(session):
    response = _beta_rpc(
        "get_my_credit_balance", {}, access_token=session["access_token"]
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if isinstance(response, dict):
        response = response.get("credit_balance", 0)
    try:
        return max(0, int(response))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("beta_invalid_balance") from exc


def _beta_effective_entitlement(session):
    """Read the member's currently effective subscription or credit-pack rights."""
    response = _beta_rpc(
        "get_my_effective_entitlement", {}, access_token=session["access_token"]
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("beta_invalid_entitlement")
    return response


def _beta_entitlement_label(entitlement):
    product_names = {
        "basic_monthly": "基本方案",
        "standard_monthly": "標準方案",
        "advanced_monthly": "進階方案",
        "credit_pack_7": "7 次生成包",
    }
    kind = str(entitlement.get("entitlement_kind", "free") or "free")
    product_name = product_names.get(
        str(entitlement.get("product_code", "") or ""), "會員體驗"
    )
    if kind == "subscription":
        prefix = "目前訂閱方案"
    elif kind == "credit_pack":
        prefix = "目前使用"
    else:
        return "目前權益：免費體驗"
    expires_at = str(entitlement.get("expires_at", "") or "")[:10]
    expiry_text = f"｜權益至 {expires_at}" if expires_at else ""
    return f"{prefix}：{product_name}{expiry_text}"


def _beta_reserve_generation(session):
    response = _beta_rpc(
        "reserve_generation_credit",
        {"p_idempotency_key": str(__import__("uuid").uuid4())},
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("beta_invalid_reservation")
    return {
        "job_id": str(response.get("job_id", "") or ""),
        "granted": bool(response.get("granted", False)),
        "balance": max(0, int(response.get("balance", 0))),
    }


def _beta_finish_generation(session, job_id, failure_code=None):
    function_name = "fail_generation" if failure_code else "complete_generation"
    payload = {"p_job_id": job_id}
    if failure_code:
        payload["p_failure_code"] = str(failure_code)[:80]
    return _beta_rpc(function_name, payload, access_token=session["access_token"])


def _beta_copy_usage_status(session):
    """Read the logged-in member's Taiwan-day copywriting allowance."""
    response = _beta_rpc(
        "get_my_copy_usage_status", {}, access_token=session["access_token"]
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("beta_invalid_copy_status")
    try:
        daily_limit = max(1, int(response.get("daily_limit", 2)))
        used_count = max(0, int(response.get("used_count", 0)))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("beta_invalid_copy_status") from exc
    return {
        "usage_date": str(response.get("usage_date", "") or ""),
        "daily_limit": daily_limit,
        "used_count": min(used_count, daily_limit),
        "remaining_count": max(0, int(response.get("remaining_count", 0) or 0)),
        "entitlement_kind": str(response.get("entitlement_kind", "free") or "free"),
        "entitlement_expires_at": str(
            response.get("entitlement_expires_at", "") or ""
        ),
    }


def _beta_copy_usage_status_cached(session, max_age_seconds=120):
    """Return a short-lived display cache so ordinary UI clicks do not query Supabase."""
    user_id = str((session.get("user") or {}).get("id", "") or "")
    cache = st.session_state.get("beta_copy_usage_status_cache")
    now = time.monotonic()
    if (
        isinstance(cache, dict)
        and cache.get("user_id") == user_id
        and isinstance(cache.get("value"), dict)
        and now - float(cache.get("checked_at", 0) or 0) < max_age_seconds
    ):
        return dict(cache["value"])

    status = _beta_copy_usage_status(session)
    st.session_state["beta_copy_usage_status_cache"] = {
        "user_id": user_id,
        "checked_at": now,
        "value": dict(status),
    }
    return status


def _beta_set_cached_copy_usage_status(session, status):
    """Keep the displayed allowance accurate immediately after a reservation."""
    user_id = str((session.get("user") or {}).get("id", "") or "")
    st.session_state["beta_copy_usage_status_cache"] = {
        "user_id": user_id,
        "checked_at": time.monotonic(),
        "value": dict(status),
    }


def _beta_clear_cached_copy_usage_status():
    st.session_state.pop("beta_copy_usage_status_cache", None)


def _beta_reserve_copy_generation(session):
    """Reserve one daily copywriting use before the station API is called."""
    response = _beta_rpc(
        "reserve_copy_generation",
        {"p_idempotency_key": str(__import__("uuid").uuid4())},
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("beta_invalid_copy_reservation")
    try:
        daily_limit = max(1, int(response.get("daily_limit", 2)))
        remaining_count = max(0, int(response.get("remaining_count", 0) or 0))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("beta_invalid_copy_reservation") from exc
    return {
        "job_id": str(response.get("job_id", "") or ""),
        "granted": bool(response.get("granted", False)),
        "usage_date": str(response.get("usage_date", "") or ""),
        "daily_limit": daily_limit,
        "remaining_count": min(remaining_count, daily_limit),
        "entitlement_kind": str(response.get("entitlement_kind", "free") or "free"),
        "entitlement_expires_at": str(
            response.get("entitlement_expires_at", "") or ""
        ),
    }


def _beta_finish_copy_generation(session, job_id, failure_code=None):
    function_name = "fail_copy_generation" if failure_code else "complete_copy_generation"
    payload = {"p_job_id": job_id}
    if failure_code:
        payload["p_failure_code"] = str(failure_code)[:80]
    return _beta_rpc(function_name, payload, access_token=session["access_token"])


def _beta_is_admin(session):
    """Ask the database, rather than the UI, whether this account is an administrator."""
    response = _beta_rpc("is_beta_admin", {}, access_token=session["access_token"])
    if isinstance(response, list):
        response = response[0] if response else False
    return bool(response)


def _beta_admin_list_members(session, search_text=""):
    response = _beta_rpc(
        "admin_list_members",
        {"p_search": str(search_text or "")[:120]},
        access_token=session["access_token"],
    )
    return response if isinstance(response, list) else []


def _beta_admin_adjust_credits(session, user_id, delta, note):
    response = _beta_rpc(
        "admin_adjust_member_credits",
        {
            "p_user_id": str(user_id),
            "p_delta": int(delta),
            "p_note": str(note or "")[:160],
        },
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    return response if isinstance(response, dict) else {}


def _beta_admin_grant_test_purchase(session, user_id, product_code, note):
    """Create an audited, zero-charge test purchase for an administrator."""
    response = _beta_rpc(
        "admin_grant_test_purchase",
        {
            "p_user_id": str(user_id),
            "p_product_code": str(product_code),
            "p_note": str(note or "管理員測試購買")[:160],
        },
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    return response if isinstance(response, dict) else {}


def _beta_render_admin_console(session):
    """Show a small audited console only when Supabase authorizes this member."""
    if not session:
        return
    try:
        if not _beta_is_admin(session):
            return
    except Exception:
        return

    st.markdown("---")
    with st.expander("🛠️ Beta 管理後台（僅管理者）", expanded=False):
        st.caption("可補回或扣除測試生成次數，也可建立不收款的測試購買。所有操作都會記錄操作者、原因與時間。")
        search_text = st.text_input(
            "搜尋會員 Email 或名稱（空白會顯示最近 100 位）",
            key="beta_admin_member_search",
        )
        if st.button("查詢會員", key="beta_admin_search_members"):
            try:
                st.session_state["beta_admin_members"] = _beta_admin_list_members(
                    session, search_text
                )
            except Exception:
                st.error("會員資料暫時無法讀取，請稍後再試。")

        members = st.session_state.get("beta_admin_members", [])
        if not isinstance(members, list) or not members:
            st.info("輸入 Email 後按「查詢會員」，即可進行補次或扣次。")
            return

        options = {}
        for member in members:
            if not isinstance(member, dict) or not member.get("user_id"):
                continue
            label = (
                f"{member.get('email') or '未提供 Email'}"
                f"｜目前 {int(member.get('credit_balance', 0) or 0)} 次"
            )
            options[label] = member
        if not options:
            st.info("找不到符合的會員。")
            return

        selected_label = st.selectbox(
            "選擇會員", list(options.keys()), key="beta_admin_selected_member"
        )
        selected = options[selected_label]
        adjustment = st.number_input(
            "調整生成次數（正數補次、負數扣次）",
            min_value=-50,
            max_value=50,
            value=2,
            step=1,
            key="beta_admin_adjustment",
        )
        note = st.text_input(
            "調整原因（會寫入管理紀錄）",
            value="Beta 測試補次",
            max_chars=160,
            key="beta_admin_adjustment_note",
        )
        if st.button("確認調整會員生成次數", type="primary", key="beta_admin_adjust_credits"):
            if int(adjustment) == 0 or len(note.strip()) < 3:
                st.warning("請填寫非 0 的調整次數與至少 3 個字的原因。")
                return
            try:
                result = _beta_admin_adjust_credits(
                    session, selected["user_id"], adjustment, note
                )
                new_balance = int(result.get("credit_balance", 0))
                st.success(f"已完成調整；該會員目前有 {new_balance} 次體驗額度。")
                st.session_state["beta_admin_members"] = _beta_admin_list_members(
                    session, search_text
                )
            except Exception:
                st.error("調整未完成。請確認生成次數不會低於 0，或稍後再試。")

        st.markdown("#### 🧪 管理員測試購買")
        st.info("此操作只用於驗證方案權益：不會收款、不會寄送付款通知，也不會對外顯示為正式訂單。")
        product_options = {
            "7 次生成包｜圖片生成 7 次／AI幫想每日 7 次／作品集 7 組｜效期 30 天": "credit_pack_7",
            "基本方案｜圖片生成 20 次／AI幫想每日 15 次／作品集 20 組｜週期 1 個月": "basic_monthly",
            "標準方案｜圖片生成 30 次／AI幫想每日 20 次／作品集 30 組｜週期 1 個月": "standard_monthly",
            "進階方案｜圖片生成 40 次／AI幫想每日 25 次／作品集 40 組｜週期 1 個月": "advanced_monthly",
        }
        selected_product_label = st.selectbox(
            "模擬購買方案", list(product_options.keys()), key="beta_admin_test_purchase_product"
        )
        purchase_note = st.text_input(
            "測試備註（會寫入管理紀錄）",
            value="管理員測試購買",
            max_chars=160,
            key="beta_admin_test_purchase_note",
        )
        if st.button("建立管理員測試購買", type="primary", key="beta_admin_test_purchase"):
            if len(purchase_note.strip()) < 3:
                st.warning("請填寫至少 3 個字的測試備註。")
            else:
                try:
                    result = _beta_admin_grant_test_purchase(
                        session,
                        selected["user_id"],
                        product_options[selected_product_label],
                        purchase_note,
                    )
                    credits = int(result.get("granted_credits", 0) or 0)
                    daily = int(result.get("daily_copy_limit", 0) or 0)
                    capacity = int(result.get("library_capacity", 0) or 0)
                    expiry = str(result.get("credit_expires_at", "") or "")[:10]
                    st.success(
                        f"測試購買已建立：加入圖片生成 {credits} 次、AI幫想每日 {daily} 次、"
                        f"作品集上限 {capacity} 組；圖片生成次數效期至 {expiry}。"
                    )
                    st.session_state["beta_admin_members"] = _beta_admin_list_members(
                        session, search_text
                    )
                except Exception:
                    st.error("測試購買建立失敗，請確認資料庫遷移已完成後再試。")


def _beta_render_account_panel():
    """Render the member login and current member usage."""
    if not _beta_config():
        return None

    _beta_complete_oauth_callback()
    session = _beta_current_user()
    if session:
        st.markdown("#### ✨ 會員 Beta")
        user = session.get("user", {})
        email = html.escape(str(user.get("email", "已登入會員") or "已登入會員"))
        account_status_col, refresh_balance_col = st.columns([5, 1], vertical_alignment="center")
        with account_status_col:
            try:
                balance = _beta_credit_balance(session)
                st.success(f"已登入：{email}　｜　可用體驗額度：{balance} 次")
            except Exception:
                st.warning(f"已登入：{email}　｜　可用生成次數暫時無法讀取，請稍後再試。")
        with refresh_balance_col:
            if st.button("刷新次數", key="beta_refresh_credit_balance", use_container_width=True):
                _beta_clear_cached_copy_usage_status()
        try:
            st.markdown(f"**📋 {_beta_entitlement_label(_beta_effective_entitlement(session))}**")
        except Exception:
            st.caption("📋 目前方案暫時無法讀取。")
        try:
            copy_status = _beta_copy_usage_status_cached(session)
            st.markdown(
                "**AI幫想 今日額度（台灣時間）："
                f"剩 {copy_status['remaining_count']} / {copy_status['daily_limit']} 次**"
            )
        except Exception:
            st.caption("AI幫想 今日額度暫時無法讀取。")
        if st.button("登出會員 Beta", type="primary", key="beta_logout"):
            st.session_state.pop("beta_auth_session", None)
            st.session_state.pop("beta_pending_login_url", None)
            _beta_clear_cached_copy_usage_status()
            st.rerun()
        if not _beta_site_openai_key():
            st.info("會員登入與生成次數帳本已啟用；站方生成 API 正在進行最後設定。")
        with st.expander("📜 生成次數與雲端作品庫規則", expanded=False):
            st.markdown(
                """
                #### 🎨 服務內容

                Sticker Studio Next 是一項 AI 輔助 LINE 貼圖創作服務。會員可上傳人物照片、選擇貼圖風格或上傳畫風參考，設定貼圖文字與動作後，產生 4×2 貼圖總圖；完成裁切後，可挑選單張貼圖下載或保存至雲端作品庫。

                #### 💳 方案與提供額度

                | 方案 | 月費／售價 | 每月圖片生成次數 | AI幫想每日額度 | 雲端作品庫上限 |
                | --- | ---: | ---: | ---: | ---: |
                | 基本方案 | NT$99／月 | 20 次 | 15 次 | 20 組 |
                | 標準方案 | NT$149／月 | 30 次 | 20 次 | 30 組 |
                | 進階方案 | NT$199／月 | 40 次 | 25 次 | 40 組 |
                | 單次生成包 | NT$49／包 | 7 次 | 7 次 | 7 組 |

                #### ✨ 圖片生成次數與使用方式

                - 每次成功生成一張 **4×2 原始貼圖總圖**，扣除 **1 次圖片生成次數**；生成失敗不計入使用。
                - AI幫想額度以台灣時間每日計算，供「主題 AI 幫想」等文字輔助功能使用；與圖片生成次數分開計算。
                - 單次生成包為一次性購買，不會自動續訂；購買後有效期為 **30 天**。
                - 訂閱方案每月提供的新圖片生成次數，未使用部分可保留至 **下一個帳務週期結束**；系統會優先使用最早到期的次數。

                > **例子：**你在 5 月 10 日訂閱基本方案，取得 20 次圖片生成次數；到了 6 月 9 日還剩 5 次。6 月 10 日進入新一期後會再加入 20 次，因此共有 25 次可用；原本保留的 5 次最晚可用到 7 月 9 日，系統會優先使用這 5 次。

                #### 🔁 訂閱規則（正式付款開通後適用）

                - 訂閱自付款成功日起生效，並依所選方案按月提供圖片生成次數、AI幫想額度與作品庫容量。
                - 取消訂閱後，當期已取得的服務權益可使用至該帳務週期結束；下一期不再續扣。

                > **例子：**你的訂閱週期是 5 月 10 日至 6 月 9 日；若在 5 月 20 日取消，仍可使用當期的圖片生成次數、AI幫想額度與作品庫權益至 6 月 9 日。6 月 10 日起不會再扣款，也不會發放新一期的方案額度。
                - 變更方案或訂閱到期後，雲端作品不會自動刪除；若現有作品數超過新方案上限，需先整理作品庫後才能新增保存。
                - 單次生成包與訂閱生成次數可併用；服務會優先使用最早到期的可用次數。

                #### ☁️ 雲端作品庫與下載

                - 每組作品可保存原始人物照片、已上傳的畫風參考與使用者選取的貼圖成品；單組最多保存 **40 張貼圖**，**MAIN／TAB** 規格圖不計入此上限。
                - 未勾選保存的裁切貼圖不會寫入作品庫；ZIP 壓縮檔不會另行保存，可在日後將作品載回創作頁後重新下載。
                - 作品庫容量依目前有效方案計算；免費會員預設可保存 5 組作品，訂閱或單次生成包則依上表權益提供容量。

                #### 📌 重要提醒

                本服務提供數位化 AI 創作與雲端保存功能。使用者須確認上傳照片、參考圖、文字與生成內容具有合法使用權，且不侵害他人權利。正式付款頁面將同步揭示付款、續訂、取消與退款相關說明。
                """
            )
        return session

    login_error = st.session_state.pop("beta_login_error", "")
    if login_error:
        # 失效或未完成的 OAuth state 不可重用；下次畫面直接準備新的單次登入連結。
        st.session_state.pop("beta_pending_login_url", None)
        st.warning(login_error)
    st.markdown('<div class="v12-beta-login-title">✨ 會員 Beta</div>', unsafe_allow_html=True)
    st.markdown('<div class="v12-beta-login-copy">初次登入會員有 2 次體驗額度。</div>', unsafe_allow_html=True)

    login_url = str(st.session_state.get("beta_pending_login_url", "") or "")
    if not login_url:
        try:
            login_url = _beta_new_login_url()
            st.session_state["beta_pending_login_url"] = login_url
        except Exception:
            st.error("登入服務暫時無法啟動，請稍後再試。")

    if login_url:
        _login_left, _login_center, _login_right = st.columns([1, 1.35, 1])
        with _login_center:
            st.markdown(
                '<a class="v12-google-login-link" href="'
                f'{html.escape(login_url, quote=True)}" target="_self">Google帳號登入</a>',
                unsafe_allow_html=True,
            )
        st.markdown(
            '<div class="v12-beta-login-copy">登入連結有效 10 分鐘，完成後會自動回到本站。</div>',
            unsafe_allow_html=True,
        )
    return None


def _supabase_rpc(function_name):
    """Legacy quota bridge deliberately disabled in Sticker Studio Next."""
    raise RuntimeError("legacy_quota_disabled")


def _get_daily_ai_quota():
    """從 Supabase RPC 讀取全站今日額度。失敗時回傳 None。"""
    try:
        row = _supabase_rpc("get_daily_ai_quota")
        used = int(row.get("used_count", 0))
        remaining = int(row.get("remaining", 10))
        limit = int(row.get("quota_limit", 10))

        limit = max(1, limit)
        used = max(0, min(used, limit))
        remaining = max(0, min(remaining, limit))

        return {
            "used_count": used,
            "remaining": remaining,
            "quota_limit": limit,
        }

    except Exception as exc:
        # 公開版不要把 Supabase endpoint、secret 或完整錯誤內容顯示給訪客。
        st.session_state["public_quota_error"] = type(exc).__name__
        return None


def _consume_daily_ai_quota():
    """
    真正取得 1 次全站 AI 額度。
    成功回傳 quota dict；沒有額度或 RPC 失敗則回傳 None。
    """
    try:
        row = _supabase_rpc("consume_daily_ai_quota")
        used = int(row.get("used_count", 0))
        remaining = int(row.get("remaining", 0))
        limit = int(row.get("quota_limit", 10))

        limit = max(1, limit)
        used = max(0, min(used, limit))
        remaining = max(0, min(remaining, limit))

        # consume function 若沒有取得額度，通常會回傳 remaining=0。
        # 這裡不自行修改資料庫，避免前端繞過 RPC 規則。
        granted = bool(row.get("granted", False))

        return {
            "granted": granted,
            "used_count": used,
            "remaining": remaining,
            "quota_limit": limit,
        }

    except Exception as exc:
        st.session_state["public_quota_error"] = type(exc).__name__
        return None


def _refund_daily_ai_quota():
    """OpenAI 生成失敗時，退回本次已取得的 1 次額度。"""
    try:
        row = _supabase_rpc("refund_daily_ai_quota")
        used = int(row.get("used_count", 0))
        remaining = int(row.get("remaining", 0))
        limit = int(row.get("quota_limit", 10))

        limit = max(1, limit)
        used = max(0, min(used, limit))
        remaining = max(0, min(remaining, limit))

        return {
            "used_count": used,
            "remaining": remaining,
            "quota_limit": limit,
        }
    except Exception as exc:
        st.session_state["public_quota_error"] = type(exc).__name__
        return None


def _show_daily_ai_quota():
    """顯示所有訪客共用的全站每日 AI 使用進度。"""
    quota = _get_daily_ai_quota()

    st.markdown(
        """
        <div style="
            max-width:820px;
            margin:10px auto 14px;
            padding:16px 20px;
            border:1px solid #3b4658;
            border-radius:14px;
            background:linear-gradient(135deg,#151b26,#202938);
            text-align:center;
        ">
            <div style="font-size:22px;font-weight:800;color:#ffffff;">
                🤖 今日 AI 使用量
            </div>
            <div style="font-size:14px;color:#b9c4d4;margin-top:4px;">
                全站共用每日額度
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if quota is None:
        st.warning(
            "⚠️ 目前無法讀取全站 AI 使用量。"
            "請確認 Supabase Secrets 已設定正確。"
        )
        return None

    used = quota["used_count"]
    remaining = quota["remaining"]
    limit = quota["quota_limit"]
    ratio = used / limit if limit else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("今日已使用", f"{used} 次")
    c2.metric("今日剩餘", f"{remaining} 次")
    c3.metric("每日上限", f"{limit} 次")

    st.progress(ratio)

    if remaining > 0:
        st.success(f"🟢 全站今日剩餘 **{remaining} 次** AI 生成額度")
    else:
        st.error("🔴 全站今日 AI 額度已用完，請明天再試。")

    return quota


COMMON_PHRASES = [
    "早安","晚安","午安","嗨","哈囉","加油","辛苦了","謝謝",
    "感謝","不客氣","沒問題","OK","收到","了解","好喔","好的",
    "太好了","讚","超讚","棒棒的","恭喜","祝福你","一起加油",
    "慢慢來","等等我","馬上來","我來了","出發","回來了","先這樣",
    "掰掰","再見","哈哈哈","笑死","真的嗎","真的假的","好開心",
    "好幸福","太可愛了","我愛你","想你","抱抱","親親","不要啦",
    "不要鬧","傻眼","無言","生氣","氣死我了","好累","累了",
    "忙死了","休息一下","我不行了","好餓","吃飯了嗎","等等再說",
    "拜託","求你了","可以嗎","好嗎","當然可以","當然好","隨便你",
    "沒事","沒關係","別擔心","放心","我懂","我知道","我明白",
    "太扯了","救命","完蛋了","糟糕","慘了","好可怕","不要怕","冷靜"
]

# ============================================================
# V8.6.4 資料模組移植：語詞／風格／貼圖字型／125 帶圖字型
# 資料內容沿用 V8，不改名稱與排列順序。
# ============================================================
V8_STYLES = ['Q版黏土3D', 'Q版收藏公仔', '3D收藏公仔', '軟陶公仔', '木雕玩具', '微縮場景', '療癒系Cute風', '日系可愛插畫', '韓系簡約插畫', '手繪水彩', '色鉛筆手繪', '粉彩蠟筆', '扁平插畫', '貼紙插畫風', '復古漫畫', '美式漫畫', '日系漫畫', '像素藝術', '毛氈玩偶', '羊毛氈手作', '紙雕立體', '紙黏土手作', '奶油霧面3D', '玻璃質感3D', '玩具盒收藏風', '盲盒公仔風', '電影級3D', '黏土定格動畫風', '低多邊形Low Poly', 'Dreamy夢幻療癒', '童話繪本風', '森林療癒風', '極簡精品風', '復古80年代', '復古90年代', '街頭潮流插畫', 'Emoji表情貼風', 'LINE訊息貼圖風', '透明背景貼圖風', '⭐ 自訂1', '⭐ 自訂2', '⭐ 自訂3', '⭐ 自訂4', '⭐ 自訂5']
V8_DEFAULT_CONTENT = ['早安', '晚安', '謝謝', '收到', 'OK', '加油', '辛苦了', '太棒了']
V8_RANDOM_POOLS = {'日常對話': ['你好', '哈囉', 'Hello', 'Hi', '安安', '嗨嗨', '請多多指教', '是我', '我來了', '來打聲招呼', '很高興認識你', '好久不見', '我來了', '想我了嗎', '在忙嗎？', '掰掰', 'Bye', '再聊', '再會', '再見', '晚點聊'], '生活應用': ['OK', '好的', '沒問題', '嗯嗯', '可以呀', '我可以', '收到', '了解', '知道了', '加油', '你可以的', '我相信你', '別緊張', '給你加加油', '生日快樂'], '時間問候': ['早安', '早呀', '早上好', '咕摸寧', '午安', '下午好', '晚安', '咕奈', '好好睡', '一覺好眠'], '溫暖關愛': ['愛你', '給你愛心', '給你一個小心心', '抱', '別哭', '抱一個', '給你大大的擁抱', '別擔心', '別想太多'], '歡樂笑聲': ['哈哈哈', '好好笑', '也太好笑', '笑死', '笑爛', '廢到笑', '呵呵', '嘻嘻', '噗', '開心', '耶耶耶', '耶伊', 'YEAH', '呀比', '好開心', '撒花', '讚', '棒', '100分', '優秀', '厲害', '好可愛'], '表現難過': ['哭哭', '嗚嗚', '傷心', '桑心', '難過', '難受', '崩潰', '今天心情不美麗'], '祝賀對方': ['恭喜', '可喜可賀', '以你為榮', '替你開心', '給你第一名', '你真的hen棒', '這感覺太美妙'], '帶點懷疑': ['真的嗎', '真的假的', '真假', '是喔？', '屁啦', '你騙人', '我不相信'], '尷尬反應': ['尷尬了', '好尷尬', '誤會大了', '希望沒事'], '驚訝震驚': ['哇哇哇', '哇嗚', '哇塞', '哇靠', '到底', '這…', '驚', 'OMG', '天啊', '驚訝', '好Shock', '我的天'], '無言傻眼': ['瞎', '扯', '蛤', '呃', '呿', '…', '無言', '傻眼', '暈倒', '我暈'], '誇張荒謬': ['誇張', '離譜', '有事嗎', '很有事', '很有病', '太扯了', '沒救了', '忘了吃藥', '比扯鈴還扯'], '調皮搗蛋': ['幹嘛', '幹什麼', '想幹嘛', '你怪怪的'], '衷心感謝': ['謝謝', '感謝你', '大感激', '甘溫', '乾蝦', '辛苦了', '有你真好', '好貼心'], '誠摯道歉': ['抱歉', '對不起', 'Sorry', '拍謝', '不好意思'], '不必客氣': ['不客氣', '小事啦', '一塊小蛋糕', '沒關係', '沒事的', '應該的', '別介意', 'No mind', '別放心上'], '時間行程': ['等我一下', '等等我', '等你', '我等你', '晚點見', '明天見', 'See you', '路上小心', '一路順風'], '交通出門': ['在路上了', '我快到了', 'on the way', '我出門了', '出發', '剛出發', '已離開', '走囉'], '地點詢問': ['約哪兒', '在哪', '到哪裡了'], '忙碌相關': ['好忙', '忙翻', '忙到爆', '有話快說!'], '用餐相關': ['一起吃飯吧', '吃飯', '開飯', '開動', '開吃', '好好吃', '好美味', '吃飽沒'], '休息睡覺': ['好睏', '好想睡', '我先睡了', '想睡覺', '小睡片刻', '補眠中', '補眠去'], '天氣感受': ['好熱', '好冷', '快中暑了', '瑟瑟發抖', '秋風氣爽'], '注意提醒': ['注意', '緊盯', '嗶嗶嗶', '提高警覺', '給我小心點'], '意見表達': ['YES', '沒錯', '就是這樣', '同意', 'NO', '不行啦', '不可以', '我拒絕', '放過我', '母湯'], '參與話題': ['加1', '+1', '加我一個', '一起一起', '我也要'], '表態行動': ['我會加油的', '交給我吧', '使命必達', '為你效勞'], '約定承諾': ['打勾勾', '一言為定', '成交', '說好囉', '就這麼說定', '+1+1'], '思考回應': ['這樣啊', '我想想', '晚點回', '晚點再說', '我考慮一下'], '請求拜託': ['拜託拜託', '麻煩你了', '求求你', '考慮考慮麻!'], '正向情緒': ['打起精神來', '一切都會越來越好', '一切都是最好的安排', '好感動', '好感人', '活在當下', '珍惜當下', '期待', '充滿希望', '羨慕', '好幸福', '小確幸', '真幸運'], '負面情緒': ['好累', '心累', '累歪', '已攤', '心好累', '好無奈', '別逼我', '讓我靜靜', '懷疑人生', '為什麼要逼我', '好衰', '衰衰的', '有夠衰', '好倒霉哦', '惡人退散', '也太衰了吧'], '緊張憤怒': ['生氣', '氣死', '森77', '超級不爽', '好可怕', '嚇到我', '嚇我一跳'], '逃避現實': ['裝死', '逃避', '不想面對', '不想努力', '來啊', '我就爛', '誰怕誰', '來互相傷害啊'], '職場學習': ['開會中', '忙碌中', '加班中', '信回不完', '事情做不完', '耍廢中', '休假中', '別吵我', '今天放假', '要正向', '馬上處理', '考試加油', '一起努力', '想下班', '想放假', '不想上班', '週末快樂', '放假愉快', '下班啦', '可以回家了', '現在是星期五晚上', '來杯咖啡', '打起精神吧'], '幽默趣味': ['穴穴尼', 'ㄎㄎ', 'ㄏㄏ', '喵', '哼', '啾咪', '就醬吧', '美麥', '好開勳', '轉圈圈', '棒棒der', '開玩笑的啦', '認真就輸了', '登愣', '蝦毀', '突破盲點', '我的老天鵝', '聽你在唬爛', '要不要聽聽你在說什麼', '腦波弱', '當仙女好累', '靜靜的看著你', '沒看過仙女嗎？', '給你尷尬又不失禮的微笑', '不要問 很可怕'], '戀愛表達': ['我愛你', '最愛你了', '喜歡你', '妳是最可愛的', '有妳好幸福', '啾', '親親', '抱抱', '吻你', '親一個', '好想抱抱你', '想你', '想念你', '好想你', '期待重逢', '期待見面', '期待約會', '陪我', '來接我', '一起吃飯吧']}
V8_TEXT_EFFECT_CATALOG = {1: '胖胖貼紙字', 2: '棉花糖圓字', 3: '果凍QQ字', 4: '奶油餅乾字', 5: '糖霜甜點字', 6: '蠟筆童趣字', 7: '軟萌手寫字', 8: '日系手帳圓字', 9: '韓系軟萌字', 10: '漫畫衝擊字', 11: '對話泡泡字', 12: '貼紙白邊字', 13: '泡棉玩具字', 14: '橡膠軟墊字', 15: '樹脂亮面字', 16: '壓克力透明字', 17: '珐瑯徽章字', 18: '刺繡布章字', 19: '羊毛氈字', 20: '皮革壓印字', 21: '紙雕層疊字', 22: '摺紙立體字', 23: '陶瓷裂釉字', 24: '玉石浮雕字', 25: '大理石雕字', 26: '銀鋼浮雕字', 27: '黃金立體字', 28: '青銅復古字', 29: '冰晶透明字', 30: '水晶玻璃字', 31: '霓虹發光字', 32: '香港霓虹字', 33: '燈泡招牌字', 34: '木雕質感字', 35: '石刻厚重字', 36: '毛筆書法字', 37: '印章篆刻字', 38: '剪紙藝術字', 39: '復古海報字', 40: '打字機復古字', 41: '塗鴉潮流字', 42: '街頭塗鴉字', 43: '漫畫爆炸字', 44: '熱血漫畫字', 45: '手繪塗鴉字', 46: '極簡手寫字', 47: '浪漫手寫字', 48: '粉筆黑板字', 49: '神聖光輝字', 50: '暗黑哥德字', 51: '黏土手作字', 52: '拼布縫線字', 53: '珍珠貝殼字', 54: '羽毛飄逸字', 55: '竹編工藝字', 56: '稻草編織字', 57: '苔蘚森林字', 58: '藤蔓花園字', 59: '花瓣拼貼字', 60: '水彩暈染字', 61: '墨彩渲染字', 62: '彩鉛塗層字', 63: '蠟染布紋字', 64: '馬賽克磚字', 65: '植絨絨面字', 66: '蒸汽齒輪字', 67: '像素電玩字', 68: '八位元方塊字', 69: '故障數位字', 70: '全息雷射字', 71: '雲朵蓬鬆字', 72: '海鹽砂粒字', 73: '珊瑚海洋字', 74: '星砂夢幻字', 75: '宇宙星雲字', 76: '巧克力糖漿字', 77: '爆米花零食字', 78: '棉麻編織字', 79: '牛仔布貼字', 80: '蕾絲花邊字', 81: '鈕扣拼貼字', 82: '鉤針編織字', 83: '彩窗教堂字', 84: '鐵鐵鉚釘字', 85: '鐵鏽工業字', 86: '電路晶片字', 87: '液晶螢幕字', 88: '雷達掃描字', 89: '橡皮印刷字', 90: '油畫筆觸字', 91: '膠卷電影字', 92: '報紙剪貼字', 93: '牛皮紙包裝字', 94: '露珠葉脈字', 95: '螢火森林字', 96: '星座占卜字', 97: '草本藥鋪字', 98: '珍奶Q彈字', 99: '壽司食玩字', 100: '甜甜圈糖針字', 101: '水墨毛筆字', 102: '行書連筆字', 103: '潑墨寫意字', 104: '篆刻印章字', 105: '鋼筆流線字', 106: '打字機復古字', 107: '粉筆手繪字', 108: '彩虹手繪字', 109: '塗鴉手繪字', 110: '立體貼紙字', 111: '氣球立體字', 112: '布料拼貼字', 113: '草地綠植字', 114: '沙灘沙粒字', 115: '雲朵棉花字', 116: '寒霜冰裂字', 117: '熔岩裂石字', 118: '鏽蝕金屬字', 119: '乾裂泥土字', 120: '鑽石切割字', 121: '雷射幻彩字', 122: '液態金屬字', 123: '霓虹燈管字', 124: '復古燈泡字', 125: '像素點陣字'}
V8_TEXT_STYLE_OPTIONS = [
    "不使用外框",
    "厚白貼紙描邊",
    "雙層撞色描邊",
    "漫畫爆炸對話框",
    "雲朵對話泡泡框",
    "圓角膠囊標籤框",
    "鋸齒票券外框",
    "火焰爆裂外框",
    "愛心泡泡外框",
    "速度線聚焦外框",
    "金屬徽章外框",
    "彩虹波浪外框",
    "塗鴉手繪外框",
]
V8_TEXT_STYLE_LEGACY_MAP = {
    "不使用效果": "不使用外框",
    "高級立體金字": "金屬徽章外框",
    "彩色漫畫爆炸": "漫畫爆炸對話框",
    "柔和立體氣泡": "雲朵對話泡泡框",
    "霓虹發光字": "速度線聚焦外框",
    "手寫貼紙字": "厚白貼紙描邊",
    "清爽白底字": "圓角膠囊標籤框",
    "雙層粗描邊字": "雙層撞色描邊",
    "鋸齒漫畫框字": "鋸齒票券外框",
    "虛線貼紙框字": "厚白貼紙描邊",
    "彩虹漸層描邊字": "彩虹波浪外框",
    "金屬鉚釘外框字": "金屬徽章外框",
    "水波透明外框字": "愛心泡泡外框",
    "毛絨縫線外框字": "塗鴉手繪外框",
    "火焰熔邊外框字": "火焰爆裂外框",
}
V8_TEXT_EFFECT_VALUES = [f"{i:03d}｜{V8_TEXT_EFFECT_CATALOG[i]}" for i in range(1,126)]
V8_FONT_PREVIEW_DIR = Path(__file__).with_name("font_reference") / "previews"

POPULAR_STYLES = [
    "↓ 請選擇風格","Q版黏土3D","Q版收藏公仔","3D收藏公仔",
    "療癒系Cute Wstyle","大頭小身","LINE貼圖風","柔和光影",
    "手作玩偶風","可愛漫畫風","立體卡通風",
]

CHARACTER_OPTIONS = [
    "五官比例保留","服飾配件保留","人物辨識度保留",
    "Q版收藏公仔","療癒系 Cute Wstyle","大頭小身",
    "LINE貼圖風","柔和光影",
]


# ============================================================
# V12｜STEP 01A①
# Project 基礎資料骨架
#
# 本階段只建立「目前作品」的 Session 架構與 UI 導航。
# 尚未寫入 Supabase / 伺服器 / 永久資料庫。
# API Key 絕不納入 Project。
# ============================================================
from datetime import datetime, timezone, timedelta
from uuid import uuid4

_V12_TAIPEI_TZ = timezone(timedelta(hours=8))

def _v12_format_display_time(value):
    """將作品時間轉成使用者容易閱讀的台灣時間格式。"""
    raw = str(value or "").strip()
    if not raw:
        return "未記錄"
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(_V12_TAIPEI_TZ)
        return dt.strftime("%Y/%m/%d %H:%M")
    except Exception:
        return raw


def _v12_now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _v12_clean_character_features(value):
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


V12_STICKER_TEXT_MODE = "💬 使用文字貼圖"
V12_STICKER_EXPRESSION_MODE = "😶 使用表情貼圖（無文字）"
V12_STYLE_PRESET_MODE = "貼圖風格快選"
V12_STYLE_REFERENCE_MODE = "畫風參考上傳"
V12_EXPRESSION_EFFECTS = [
    "無額外特效（專注表情特寫）",
    "漫畫速度線",
    "漫畫驚嘆圖框",
    "放射聚焦背景",
    "人物朦朧殘影",
    "馬賽克眼神",
    "冷汗水滴",
    "怒氣爆筋符號",
    "頭頂烏雲",
    "閃亮星星特效",
    "愛心泡泡",
    "震動波浪線",
    "聚光燈",
    "幽靈半透明",
]

# 「隨機」不是交由模型任意猜測，而是先在網站端抽出具體配方。
# 每個配方另有臉部、動作與鏡頭的變化池；即使新一輪再次抽到同一配方，
# 實際送出的方向仍會不同。
V12_RANDOM_EXPRESSION_RECIPE_SEEDS = [
    # 驚訝／慌張
    ("surprise_01", "驚訝／慌張", "突發嚇到", "瞬間嚇到、瞳孔放大", "雙手貼住臉頰往後縮", ("漫畫驚嘆圖框", "放射聚焦背景")),
    ("surprise_02", "驚訝／慌張", "晴天霹靂", "完全不敢相信、嘴巴張成 O 型", "抬頭僵住、雙手停在半空", ("放射聚焦背景", "震動波浪線")),
    ("surprise_03", "驚訝／慌張", "看到怪東西", "驚恐瞪大眼、眉毛高挑", "縮起肩膀、手指指向遠方", ("漫畫速度線", "漫畫驚嘆圖框")),
    ("surprise_04", "驚訝／慌張", "突然被點名", "被抓包般慌張、眼神飄移", "單手指著自己、另一手停住", ("冷汗水滴", "震動波浪線")),
    ("surprise_05", "驚訝／慌張", "快跌倒", "失去平衡的慌張表情", "雙手大張、身體明顯傾斜", ("漫畫速度線", "震動波浪線")),
    ("surprise_06", "驚訝／慌張", "壓力爆表", "緊張到冒汗、嘴角發抖", "抱住頭、縮成一團", ("冷汗水滴", "頭頂烏雲")),
    ("surprise_07", "驚訝／慌張", "找不到人", "焦急張望、眼神快速搜尋", "一手遮在額頭前四處看", ("漫畫速度線", "放射聚焦背景")),
    ("surprise_08", "驚訝／慌張", "突然來電", "被聲音嚇醒的呆滯表情", "肩膀聳起、手邊像接到突發訊息", ("漫畫驚嘆圖框", "震動波浪線")),
    ("surprise_09", "驚訝／慌張", "出大事了", "驚恐失色、呼吸停住", "兩手摀住嘴、身體後仰", ("放射聚焦背景", "冷汗水滴")),
    ("surprise_10", "驚訝／慌張", "腦袋當機", "呆住睜大眼、表情空白", "雙手停在臉旁、整個人定格", ("震動波浪線", "幽靈半透明")),
    # 搞笑／荒謬
    ("funny_01", "搞笑／荒謬", "靈魂出竅", "眼神死、靈魂抽離般空白", "身體軟掉、靈魂像要飄走", ("幽靈半透明", "人物朦朧殘影")),
    ("funny_02", "搞笑／荒謬", "荒謬瞇眼", "半瞇眼露出不可置信的怪表情", "雙手攤開、像在問到底怎麼了", ("漫畫速度線", "震動波浪線")),
    ("funny_03", "搞笑／荒謬", "無語扶額", "尷尬又無言的苦笑", "一手扶額、另一手無力垂下", ("頭頂烏雲", "人物朦朧殘影")),
    ("funny_04", "搞笑／荒謬", "戲劇性暈倒", "眼睛轉圈、誇張昏眩", "身體歪斜、手在空中亂揮", ("震動波浪線", "幽靈半透明")),
    ("funny_05", "搞笑／荒謬", "裝傻逃避", "假裝沒聽到的無辜表情", "側過臉、手指偷偷比安靜", ("人物朦朧殘影", "冷汗水滴")),
    ("funny_06", "搞笑／荒謬", "偷偷吃瓜", "八卦興奮又忍笑的眼神", "身體前傾、手遮住嘴偷笑", ("閃亮星星特效", "漫畫速度線")),
    ("funny_07", "搞笑／荒謬", "嘴角抽搐", "尷尬到嘴角不受控制", "雙手僵在胸前、眼神斜看", ("震動波浪線", "冷汗水滴")),
    ("funny_08", "搞笑／荒謬", "小丑模式", "荒謬大笑、表情過度用力", "雙手張開像在登場表演", ("聚光燈", "漫畫驚嘆圖框")),
    ("funny_09", "搞笑／荒謬", "社死瞬間", "尷尬假笑、眼神想逃跑", "手掌遮住半張臉、肩膀縮起", ("冷汗水滴", "人物朦朧殘影")),
    ("funny_10", "搞笑／荒謬", "反應太慢", "延遲理解後才震驚的表情", "一根手指停在臉旁、身體微歪", ("漫畫驚嘆圖框", "震動波浪線")),
    # 開心／得意
    ("happy_01", "開心／得意", "中獎歡呼", "眼睛發亮、開心尖叫", "雙手高舉歡呼", ("閃亮星星特效", "放射聚焦背景")),
    ("happy_02", "開心／得意", "愛心暴擊", "愛慕又陶醉、臉頰泛紅", "雙手捧臉、頭微微傾斜", ("愛心泡泡", "閃亮星星特效")),
    ("happy_03", "開心／得意", "任務完成", "鬆一口氣的燦爛笑容", "握拳小幅慶祝", ("閃亮星星特效", "聚光燈")),
    ("happy_04", "開心／得意", "得意登場", "眉毛上挑、帶點跩的笑", "雙手叉腰、自信挺胸", ("聚光燈", "閃亮星星特效")),
    ("happy_05", "開心／得意", "小小勝利", "滿足偷笑、眼神有光", "在胸前比出勝利手勢", ("閃亮星星特效", "愛心泡泡")),
    ("happy_06", "開心／得意", "興奮轉圈", "開心到停不下來的笑容", "張開雙手、身體帶旋轉感", ("人物朦朧殘影", "漫畫速度線")),
    ("happy_07", "開心／得意", "被稱讚", "害羞又開心、雙眼彎彎", "雙手放在臉旁、微微縮肩", ("愛心泡泡", "閃亮星星特效")),
    ("happy_08", "開心／得意", "熱情招手", "元氣滿滿的大笑", "手臂大幅揮動招呼", ("漫畫速度線", "閃亮星星特效")),
    ("happy_09", "開心／得意", "快樂尖叫", "張嘴歡呼、眼神閃亮", "雙手握拳靠近臉頰", ("漫畫驚嘆圖框", "閃亮星星特效")),
    ("happy_10", "開心／得意", "開心比心", "甜甜微笑、眼神溫柔", "雙手在胸前比出愛心", ("愛心泡泡", "聚光燈")),
    # 生氣／不耐煩
    ("angry_01", "生氣／不耐煩", "火大握拳", "怒目瞪人、眉頭深鎖", "單手握拳舉在胸前", ("怒氣爆筋符號", "漫畫速度線")),
    ("angry_02", "生氣／不耐煩", "氣到發抖", "咬牙忍耐、臉部緊繃", "雙拳握緊、肩膀顫抖", ("震動波浪線", "怒氣爆筋符號")),
    ("angry_03", "生氣／不耐煩", "冷眼不爽", "側眼瞪人、嘴角下壓", "雙手抱胸、頭微微偏開", ("頭頂烏雲", "人物朦朧殘影")),
    ("angry_04", "生氣／不耐煩", "無奈爆氣", "無奈又快爆炸的表情", "一手抓頭、另一手攤開", ("怒氣爆筋符號", "震動波浪線")),
    ("angry_05", "生氣／不耐煩", "準備反擊", "銳利瞪眼、表情嚴肅", "手指向前、身體略前傾", ("漫畫速度線", "怒氣爆筋符號")),
    ("angry_06", "生氣／不耐煩", "生悶氣", "鼓起臉頰、不想說話", "別過臉、雙手抱胸", ("頭頂烏雲", "震動波浪線")),
    ("angry_07", "生氣／不耐煩", "忍耐極限", "眼角抽動、強忍怒火", "雙手交握用力、身體僵住", ("怒氣爆筋符號", "冷汗水滴")),
    ("angry_08", "生氣／不耐煩", "臉紅怒吼", "張口大喊、臉部漲紅", "握拳向下用力揮", ("漫畫驚嘆圖框", "怒氣爆筋符號")),
    ("angry_09", "生氣／不耐煩", "別惹我", "低頭瞪人、氣場很強", "一手伸出做停止手勢", ("聚光燈", "漫畫速度線")),
    ("angry_10", "生氣／不耐煩", "不耐煩催促", "皺眉看時間、嘴角不耐", "手指點著手腕催促", ("震動波浪線", "頭頂烏雲")),
    # 難過／崩潰
    ("sad_01", "難過／崩潰", "委屈落淚", "眼眶泛淚、嘴角委屈下垂", "雙手縮在胸前、肩膀微縮", ("冷汗水滴", "頭頂烏雲")),
    ("sad_02", "難過／崩潰", "崩潰大哭", "張嘴大哭、眼淚止不住", "雙手握在胸前、身體顫抖", ("震動波浪線", "頭頂烏雲")),
    ("sad_03", "難過／崩潰", "下雨心情", "失落垂眼、整個人沒精神", "抱住自己、頭微微低下", ("頭頂烏雲", "幽靈半透明")),
    ("sad_04", "難過／崩潰", "失望嘆氣", "無奈閉眼、長長嘆氣", "一手托著額頭、肩膀下垂", ("人物朦朧殘影", "頭頂烏雲")),
    ("sad_05", "難過／崩潰", "被忽略", "委屈抿嘴、眼神想哭", "手指輕碰自己、默默縮起", ("幽靈半透明", "冷汗水滴")),
    ("sad_06", "難過／崩潰", "心碎", "心痛皺眉、淚光閃爍", "一手按住胸口、另一手垂下", ("愛心泡泡", "頭頂烏雲")),
    ("sad_07", "難過／崩潰", "求安慰", "可憐巴巴地望著前方", "雙手張開像想討抱抱", ("冷汗水滴", "愛心泡泡")),
    ("sad_08", "難過／崩潰", "累倒", "疲憊睜不開眼、表情虛弱", "整個人歪向一側、手無力垂下", ("幽靈半透明", "人物朦朧殘影")),
    ("sad_09", "難過／崩潰", "悲傷放空", "眼神空洞、表情平靜卻難過", "雙手垂下、身體完全靜止", ("幽靈半透明", "頭頂烏雲")),
    ("sad_10", "難過／崩潰", "偷偷擦淚", "強忍眼淚、嘴角微微顫抖", "用手背擦眼角、別過臉", ("冷汗水滴", "人物朦朧殘影")),
    # 日常／社交反應
    ("daily_01", "日常／社交", "睡眼惺忪", "半閉眼、剛睡醒的迷糊表情", "一手揉眼睛、身體微歪", ("幽靈半透明", "震動波浪線")),
    ("daily_02", "日常／社交", "思考中", "皺眉思考、眼神往上看", "一手托下巴、手指輕點臉頰", ("放射聚焦背景", "閃亮星星特效")),
    ("daily_03", "日常／社交", "收到了解", "冷靜點頭、帶著可靠微笑", "單手比出 OK 或拇指", ("閃亮星星特效", "聚光燈")),
    ("daily_04", "日常／社交", "等一下", "認真提醒、眉毛微挑", "手掌向前做暫停手勢", ("漫畫速度線", "震動波浪線")),
    ("daily_05", "日常／社交", "偷偷看", "眼神偷瞄、帶點好奇", "從側邊探出頭、手遮住嘴", ("人物朦朧殘影", "漫畫速度線")),
    ("daily_06", "日常／社交", "真的假的", "懷疑挑眉、嘴角微張", "一手攤開、另一手指向前方", ("漫畫驚嘆圖框", "放射聚焦背景")),
    ("daily_07", "日常／社交", "害羞", "臉頰泛紅、眼神不敢直視", "雙手捧臉、身體微微內縮", ("愛心泡泡", "閃亮星星特效")),
    ("daily_08", "日常／社交", "社交電量低", "禮貌微笑但眼神疲憊", "一手小幅揮手、另一手扶肩", ("幽靈半透明", "頭頂烏雲")),
    ("daily_09", "日常／社交", "裝沒事", "假裝鎮定、嘴角僵住", "雙手背在身後、視線飄開", ("冷汗水滴", "人物朦朧殘影")),
    ("daily_10", "日常／社交", "下班逃跑", "迫不及待又興奮的表情", "身體朝外衝、手臂大幅擺動", ("漫畫速度線", "閃亮星星特效")),
]

V12_RANDOM_EXPRESSION_RECIPES = [
    {
        "id": recipe_id,
        "category": category,
        "title": title,
        "emotion": emotion,
        "action": action,
        "effects": tuple(effects),
    }
    for recipe_id, category, title, emotion, action, effects
    in V12_RANDOM_EXPRESSION_RECIPE_SEEDS
]

V12_RANDOM_EXPRESSION_VARIATIONS = {
    "驚訝／慌張": {
        "face": ("眼睛圓睜、呼吸停住", "眉毛高挑、嘴型僵住", "瞳孔縮小、表情突然定格", "先呆住再露出強烈慌張"),
        "motion": ("肩膀瞬間聳起", "上半身向後縮", "手指停在半空", "頭部微微後仰"),
        "framing": ("臉部近景、保留清楚手勢", "肩膀以上特寫、動作集中", "正面近景、表情放大"),
    },
    "搞笑／荒謬": {
        "face": ("五官誇張扭曲但仍可愛", "露出荒謬的無言表情", "眼神死卻帶喜劇節奏", "忍笑到表情快失控"),
        "motion": ("動作刻意停在最尷尬的一刻", "身體帶一點滑稽歪斜", "手勢做得略微過頭", "肩膀跟著喜劇節奏抖動"),
        "framing": ("臉部特寫、表情優先", "上半身近景、手勢清楚", "微側臉近景、保留荒謬動作"),
    },
    "開心／得意": {
        "face": ("眼睛彎成月牙、笑容很亮", "眉眼上揚、充滿成就感", "開心到臉頰微紅", "露出自信又俏皮的笑"),
        "motion": ("肩膀帶著上揚節奏", "手勢做出俐落收尾", "身體微微前傾分享喜悅", "頭部輕輕歪向一側"),
        "framing": ("正面近景、笑容清楚", "肩膀以上特寫、保留手勢", "微仰角近景、帶主角感"),
    },
    "生氣／不耐煩": {
        "face": ("眉頭深鎖、眼神銳利", "嘴角下壓、明顯不耐煩", "咬牙忍住怒氣", "怒氣寫在眼神裡"),
        "motion": ("肩膀繃緊、動作有力", "上半身略向前壓", "手勢停在最有張力的位置", "頭部微低、視線往上瞪"),
        "framing": ("臉部近景、保留拳頭或手勢", "正面上半身、氣勢集中", "微側臉近景、眼神突出"),
    },
    "難過／崩潰": {
        "face": ("眼眶水亮、嘴角下垂", "強忍情緒卻快哭出來", "疲憊垂眼、神情脆弱", "眼神放空又讓人心疼"),
        "motion": ("肩膀微微下垂", "動作小而收縮", "頭部慢慢低下", "手勢停在安慰自己的位置"),
        "framing": ("臉部近景、眼神清楚", "肩膀以上特寫、保留手勢", "微側臉近景、情緒集中"),
    },
    "日常／社交": {
        "face": ("自然但辨識度高的表情", "帶一點生活感的小反應", "眼神有明確社交情境", "表情簡潔又容易看懂"),
        "motion": ("手勢乾淨清楚", "上半身微微前傾", "頭部輕微偏轉", "肩膀放鬆、動作自然"),
        "framing": ("正面近景、日常感清楚", "肩膀以上特寫、保留手勢", "微側臉近景、姿態自然"),
    },
}

# 「60 組」只負責表情與動作的骨架；背景、視覺特效與鏡頭另外抽選。
# 這可避免不同配方仍反覆落在同一種放射背景、星星或愛心的問題。
# 每個畫面包是高階視覺方向，而非要複製任何既有作品或角色。
V12_EXPRESSION_VISUAL_PACK_SEEDS = [
    ("impact_red", "紅橙衝擊爆發", "漫畫驚嘆圖框", "紅橙爆發放射圖形與少量碎片", "微低角度、衝出畫面的近景"),
    ("impact_blue", "藍色冰裂震撼", "震動波浪線", "藍白冰裂波紋與放射衝擊", "正面近景、畫面帶停格感"),
    ("impact_yellow", "黃色警報閃光", "漫畫速度線", "明黃警示閃光與斜向速度線", "斜側近景、動作方向明確"),
    ("impact_purple", "紫色電流驚嚇", "放射聚焦背景", "紫藍電流光芒與不規則衝擊圈", "仰角特寫、表情放大"),
    ("motion_left", "左向疾跑殘影", "人物朦朧殘影", "由右往左的淡色殘影與短速度線", "微側臉近景、保留前伸手勢"),
    ("motion_right", "右向甩動殘影", "人物朦朧殘影", "由左往右的彩色殘影與彎曲動勢線", "微側臉近景、頭部與手勢有方向感"),
    ("motion_spin", "旋轉暈眩旋渦", "震動波浪線", "環繞角色的簡潔旋渦與搖晃線", "正面近景、姿態傾斜"),
    ("motion_bounce", "彈跳節奏泡泡", "漫畫速度線", "上下彈跳弧線與少量節奏圓點", "稍遠的上半身近景、留出手勢"),
    ("dream_pink", "粉紅夢幻愛心", "愛心泡泡", "粉紅愛心、柔焦光點與輕盈漂浮感", "微仰角特寫、眼神為焦點"),
    ("dream_lilac", "薰衣草夢境", "閃亮星星特效", "淡紫星塵、小月光與柔和暈染", "正面特寫、表情乾淨"),
    ("dream_mint", "薄荷清新閃光", "閃亮星星特效", "薄荷綠光點與輕快小星芒", "微側近景、角色像剛回頭"),
    ("dream_gold", "金色主角時刻", "聚光燈", "暖金聚光與舞台感亮點", "低角度近景、主角氣勢明確"),
    ("comic_pop", "普普漫畫點陣", "漫畫驚嘆圖框", "高彩度點陣、跳色幾何與短促驚嘆線", "正面近景、畫面節奏活潑"),
    ("comic_panel", "斜切漫畫分鏡", "漫畫速度線", "簡潔斜切色塊與單一方向速度線", "斜側近景、強調姿勢輪廓"),
    ("comic_zoom", "鏡頭急速拉近", "放射聚焦背景", "由外向內收束的簡潔聚焦圈", "臉部超近景但仍完整保留頭部與手勢"),
    ("comic_shock", "荒謬停格震動", "震動波浪線", "不規則黑白停格線與小型驚訝符號", "正面近景、誇張五官"),
    ("weather_rain", "小雨低氣壓", "頭頂烏雲", "小烏雲、稀疏雨點與偏冷的留白", "微低頭近景、情緒集中"),
    ("weather_wind", "冷風掠過", "人物朦朧殘影", "單方向冷色風線與飄動碎葉", "側臉近景、髮絲或衣角帶動勢"),
    ("weather_heat", "熱氣蒸騰", "冷汗水滴", "暖色熱浪、小汗滴與扭曲空氣感", "正面上半身近景、表情清楚"),
    ("weather_night", "深夜孤單光圈", "幽靈半透明", "深藍小光圈與稀疏星點，不要複雜場景", "微側近景、留出安靜空間"),
    ("gag_mosaic", "荒謬馬賽克反應", "馬賽克眼神", "簡潔跳色馬賽克、歪斜小符號與喜劇節奏", "臉部近景、五官反應明顯"),
    ("gag_ghost", "靈魂飄出喜劇", "幽靈半透明", "淡色小幽靈殘影與空白喜劇停頓", "正面近景、姿態放鬆或癱軟"),
    ("gag_angry", "火花怒氣爆發", "怒氣爆筋符號", "紅色小火花、爆筋符號與短促衝擊線", "微低角度近景、手勢有力"),
    ("gag_sweat", "尷尬冷汗警報", "冷汗水滴", "藍色冷汗、斜向警報線與少量空白", "微側近景、眼神飄移"),
]

V12_EXPRESSION_VISUAL_PACKS = [
    {
        "id": pack_id,
        "title": title,
        "effect": effect,
        "stage": stage,
        "camera": camera,
    }
    for pack_id, title, effect, stage, camera in V12_EXPRESSION_VISUAL_PACK_SEEDS
]


def _v12_pick_balanced_expression_recipe_block(recipes, count, category_counts=None):
    """Pick a short block while distributing the six categories as evenly as possible."""
    available = list(recipes)
    selected = []
    category_counts = dict(category_counts or {})
    while len(selected) < count and available:
        categories = {}
        for recipe in available:
            categories.setdefault(recipe["category"], []).append(recipe)
        least_used = min(category_counts.get(category, 0) for category in categories)
        candidates = [
            category for category in categories
            if category_counts.get(category, 0) == least_used
        ]
        category = random.choice(candidates)
        recipe = random.choice(categories[category])
        selected.append(recipe)
        category_counts[category] = category_counts.get(category, 0) + 1
        available = [item for item in available if item["id"] != recipe["id"]]
    return selected, category_counts


def _v12_pick_random_expression_recipes(count):
    """Pick balanced, never-repeated-in-session recipes for the random slots."""
    count = max(0, int(count or 0))
    used_ids = {
        str(value) for value in st.session_state.get(
            "v12_expression_used_recipe_ids", []
        )
    }
    available = [
        recipe for recipe in V12_RANDOM_EXPRESSION_RECIPES
        if recipe["id"] not in used_ids
    ]
    if len(available) >= count:
        selected, _ = _v12_pick_balanced_expression_recipe_block(available, count)
        return selected, None

    # A batch can cross the 60-recipe boundary. First use every remaining
    # unused recipe, then begin the next round only for the still-needed slots.
    selected, category_counts = _v12_pick_balanced_expression_recipe_block(
        available, len(available)
    )
    new_cycle_start = len(selected)
    selected_ids = {recipe["id"] for recipe in selected}
    fresh_pool = [
        recipe for recipe in V12_RANDOM_EXPRESSION_RECIPES
        if recipe["id"] not in selected_ids
    ]
    extra, _ = _v12_pick_balanced_expression_recipe_block(
        fresh_pool, count - len(selected), category_counts
    )
    return selected + extra, new_cycle_start


def _v12_pick_expression_visual_packs(count):
    """Pick distinct background/effect/camera packs, avoiding recent session reuse."""
    count = max(0, int(count or 0))
    used_ids = {
        str(value) for value in st.session_state.get(
            "v12_expression_used_visual_pack_ids", []
        )
    }
    available = [
        pack for pack in V12_EXPRESSION_VISUAL_PACKS
        if pack["id"] not in used_ids
    ]
    # A visual pack must not repeat within a single 4×2 result.  When the
    # session memory is exhausted, start a fresh visual cycle only for this
    # selection; the 24-pack library still comfortably covers an 8-slot batch.
    if len(available) < count:
        available = list(V12_EXPRESSION_VISUAL_PACKS)
        used_ids = set()
    return random.sample(available, min(count, len(available)))


def _v12_prepare_random_expression_specs(specs):
    """Keep random expression slots open-ended while enforcing batch contrast.

    Earlier versions injected a fixed 60-recipe catalogue and a visual-effect
    catalogue.  That made the output easy to label but unintentionally made
    poses, backgrounds and symbols look manufactured.  Random now means the
    image model may invent the reaction freely, with only anti-repeat and
    low-effect guardrails.
    """
    resolved = _v12_normalize_expression_specs(specs)
    random_indexes = [
        index for index, spec in enumerate(resolved) if spec["emotion_random"]
    ]
    labels = []
    for index in random_indexes:
        original = resolved[index]
        user_effect = str(original.get("effect", "") or "")
        user_action = str(original.get("effect_action_prompt", "") or "").strip()
        directions = [
            "由 AI 自由設計一個清楚、自然且有辨識度的情緒反應",
            "此格的臉部表情、手勢、身體方向與鏡頭角度都必須與本批其他隨機格不同",
            "優先以人物的表情與姿態製造差異，不要依賴大面積背景、漫畫符號或搶眼特效",
        ]
        if user_action:
            directions.append("加入使用者指定方向：" + user_action)
        resolved[index] = {
            "emotion": "由 AI 自由安排，且與同批其他格明顯不同",
            "emotion_random": False,
            "effect": (
                user_effect
                if user_effect and user_effect != V12_EXPRESSION_EFFECTS[0]
                else V12_EXPRESSION_EFFECTS[0]
            ),
            "effect_action_prompt": "；".join(directions),
        }
        labels.append({"slot": index + 1, "title": "自由隨機"})
    return resolved, labels


def _v12_commit_random_expression_recipes():
    """Clear obsolete catalogue memory after a successful free-random job."""
    st.session_state["v12_expression_used_recipe_ids"] = []
    st.session_state["v12_expression_used_visual_pack_ids"] = []
    st.session_state["v12_expression_last_recipe_labels"] = []


def _v12_reset_random_expression_memory():
    st.session_state["v12_expression_used_recipe_ids"] = []
    st.session_state["v12_expression_used_visual_pack_ids"] = []
    st.session_state["v12_expression_last_recipe_labels"] = []
    st.session_state["v12_expression_recipe_cycle"] = 1


def _v12_default_expression_specs():
    return [
        {
            "emotion": "",
            "emotion_random": False,
            "effect": V12_EXPRESSION_EFFECTS[0],
            "effect_action_prompt": "",
        }
        for _ in range(8)
    ]


def _v12_normalize_expression_specs(value):
    """Normalize the 8 expression-sticker directions stored with a project."""
    raw = value if isinstance(value, list) else []
    normalized = []
    for i in range(8):
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        effect = str(item.get("effect", "") or "").strip()
        if effect not in V12_EXPRESSION_EFFECTS:
            effect = V12_EXPRESSION_EFFECTS[0]
        normalized.append({
            "emotion": str(item.get("emotion", "") or "").strip()[:160],
            "emotion_random": bool(item.get("emotion_random", False)),
            "effect": effect,
            "effect_action_prompt": str(
                item.get("effect_action_prompt", "") or ""
            ).strip()[:500],
        })
    return normalized


def _v12_default_sticker_action_prompts():
    return [""] * 8


def _v12_normalize_sticker_action_prompts(value):
    raw = value if isinstance(value, list) else []
    prompts = [str(item or "").strip()[:500] for item in raw[:8]]
    return prompts + [""] * (8 - len(prompts))


def _v12_sticker_mode_value(value=None):
    value = str(value if value is not None else st.session_state.get(
        "v12_sticker_content_mode", V12_STICKER_TEXT_MODE
    ))
    return "expression" if value == V12_STICKER_EXPRESSION_MODE else "text"


def _v12_get_expression_specs():
    return _v12_normalize_expression_specs([
        {
            "emotion": "" if st.session_state.get(
                f"v12_expression_emotion_random_{i}", False
            ) else st.session_state.get(f"v12_expression_emotion_{i}", ""),
            "emotion_random": st.session_state.get(
                f"v12_expression_emotion_random_{i}", False
            ),
            "effect": st.session_state.get(
                f"v12_expression_effect_{i}", V12_EXPRESSION_EFFECTS[0]
            ),
            "effect_action_prompt": st.session_state.get(
                f"v12_expression_effect_action_prompt_{i}", ""
            ),
        }
        for i in range(8)
    ])


def _v12_current_custom_style_snapshot():
    """Capture the exact custom style used by the current project, including a draft."""
    mode = str(st.session_state.get("v10_style_mode", "") or "")
    snapshot = {
        "mode": mode,
        "name": "",
        "prompt": "",
        "source": "preset",
    }
    if mode != "⭐ 自定義風格":
        return snapshot

    loaded = st.session_state.get("v12_loaded_project_custom_style")
    if isinstance(loaded, dict) and str(loaded.get("prompt", "") or "").strip():
        snapshot.update({
            "name": str(loaded.get("name", "") or "").strip(),
            "prompt": str(loaded.get("prompt", "") or "").strip(),
            "source": "project_snapshot",
        })
        return snapshot

    choice = str(st.session_state.get("v12_saved_custom_style_choice", "") or "").strip()
    if choice and choice != "↓ 請選擇已儲存風格":
        try:
            slot = int(choice.split("｜", 1)[0])
            entry = _public02a_get_style_bank().get(str(slot), {})
            snapshot.update({
                "name": str(entry.get("name", "") or "").strip(),
                "prompt": str(entry.get("prompt", "") or "").strip(),
                "source": "saved_style",
            })
            return snapshot
        except (TypeError, ValueError):
            pass

    # 未按下「儲存此風格」的草稿也屬於本次創作的一部分。
    snapshot.update({
        "name": str(st.session_state.get("v12_style_editor_name", "") or "").strip(),
        "prompt": str(st.session_state.get("v12_style_editor_prompt", "") or "").strip(),
        "source": "unsaved_draft",
    })
    return snapshot


def _v12_new_project_metadata():
    """V12｜01B①：建立目前作品的標準化 metadata。

    保留既有 flat keys，確保目前 UI / 打包流程完全相容；
    同時新增 canonical nested schema，供後續搜尋、篩選、排序與持久化使用。
    """
    now = _v12_now_iso()
    return {
        # ---- Backward-compatible flat fields (existing UI reads these) ----
        "project_id": str(uuid4()),
        "project_name": "",
        "style_name": "",
        "font_name": "",
        "text_style": "",
        "created_at": now,
        "updated_at": now,
        "generation_method": "",
        "transparent_background": False,
        "style_reference_strength": "明顯融入",
        "style_reference_elements_enabled": False,
        "style_reference_elements": [],
        "style_reference_custom_elements": "",
        "style_mode": "",
        "custom_style_snapshot": {},
        "character_features": [],
        "custom_character": "",
        "custom_character_enabled": False,
        "sticker_mode": "text",
        "expression_specs": _v12_default_expression_specs(),
        "sticker_texts": [""] * 8,
        "sticker_action_prompts": _v12_default_sticker_action_prompts(),
        "sticker_color_segments": [],
        "source_image": None,
        "generated_grid": None,
        "stickers": [],
        "main_image": None,
        "tab_image": None,

        # ---- V12｜STEP 01B① canonical metadata ----
        "schema_version": "v12.project.1",
        "identity": {
            "project_id": "",
            "project_name": "",
        },
        "timestamps": {
            "created_at": now,
            "updated_at": now,
        },
        "creative_settings": {
            "style": {"name": "", "mode": "", "custom_snapshot": {}},
            "font": {"name": ""},
            "text_style": "",
            "generation_method": "",
            "transparent_background": False,
            "style_reference_strength": "明顯融入",
            "style_reference_elements_enabled": False,
            "style_reference_elements": [],
            "style_reference_custom_elements": "",
            "character_features": [],
            "custom_character": "",
            "custom_character_enabled": False,
            "sticker_mode": "text",
            "expression_specs": _v12_default_expression_specs(),
            "sticker_texts": [""] * 8,
            "sticker_action_prompts": _v12_default_sticker_action_prompts(),
            "sticker_color_segments": [],
        },
        "package_settings": {
            "main_no": "",
            "tab_no": "",
            "sticker_count": 8,
        },
    }


def _v12_normalize_project_metadata(metadata):
    """Return canonical metadata while preserving all legacy flat fields.

    This is intentionally non-destructive: old history records can be opened
    without migration, while new records always receive the standard schema.
    """
    base = _v12_new_project_metadata()
    data = dict(metadata or {})

    # Identity / timestamps: canonical values may be supplied by newer records,
    # otherwise fall back to the existing flat structure.
    identity = data.get("identity") if isinstance(data.get("identity"), dict) else {}
    timestamps = data.get("timestamps") if isinstance(data.get("timestamps"), dict) else {}
    creative = data.get("creative_settings") if isinstance(data.get("creative_settings"), dict) else {}
    package = data.get("package_settings") if isinstance(data.get("package_settings"), dict) else {}

    style = creative.get("style") if isinstance(creative.get("style"), dict) else {}
    font = creative.get("font") if isinstance(creative.get("font"), dict) else {}

    style_mode = str(data.get("style_mode", style.get("mode", "")) or "")
    raw_custom_style = data.get(
        "custom_style_snapshot", style.get("custom_snapshot", {})
    )
    custom_style_snapshot = dict(raw_custom_style) if isinstance(raw_custom_style, dict) else {}
    custom_style_snapshot = {
        "mode": str(custom_style_snapshot.get("mode", style_mode) or ""),
        "name": str(custom_style_snapshot.get("name", "") or "")[:120],
        "prompt": str(custom_style_snapshot.get("prompt", "") or "")[:12000],
        "source": str(custom_style_snapshot.get("source", "") or "")[:40],
    }
    character_features = _v12_clean_character_features(
        data.get("character_features", creative.get("character_features", []))
    )
    custom_character = str(
        data.get("custom_character", creative.get("custom_character", "")) or ""
    )[:2000]
    custom_character_enabled = bool(
        data.get(
            "custom_character_enabled",
            creative.get("custom_character_enabled", False),
        )
    )
    sticker_mode = str(data.get("sticker_mode", creative.get("sticker_mode", "text")) or "text")
    if sticker_mode not in {"text", "expression"}:
        sticker_mode = "text"
    expression_specs = _v12_normalize_expression_specs(
        data.get("expression_specs", creative.get("expression_specs", []))
    )
    style_reference_strength = str(
        data.get("style_reference_strength", creative.get("style_reference_strength", "明顯融入"))
        or "明顯融入"
    )
    if style_reference_strength not in {"柔和融入", "明顯融入", "強烈融入"}:
        style_reference_strength = "明顯融入"
    # V12｜畫風參考改為單一自訂說明，不再保存已移除的多選元素。
    style_reference_elements = []
    style_reference_elements_enabled = False
    style_reference_custom_elements = str(data.get(
        "style_reference_custom_elements",
        creative.get("style_reference_custom_elements", ""),
    ) or "")[:1200]

    project_id = str(data.get("project_id") or identity.get("project_id") or base["project_id"])
    project_name = _v12_normalize_project_name(
        data.get("project_name", identity.get("project_name", ""))
    )
    created_at = str(data.get("created_at") or timestamps.get("created_at") or base["created_at"])
    updated_at = str(data.get("updated_at") or timestamps.get("updated_at") or created_at)

    sticker_texts = data.get("sticker_texts", creative.get("sticker_texts", [""] * 8))
    if not isinstance(sticker_texts, list):
        sticker_texts = list(sticker_texts or [])
    sticker_texts = [str(x) for x in sticker_texts[:8]]
    sticker_texts += [""] * (8 - len(sticker_texts))
    sticker_action_prompts = _v12_normalize_sticker_action_prompts(
        data.get(
            "sticker_action_prompts",
            creative.get("sticker_action_prompts", []),
        )
    )

    sticker_color_segments = data.get(
        "sticker_color_segments",
        creative.get("sticker_color_segments", [])
    )
    if not isinstance(sticker_color_segments, list):
        sticker_color_segments = []
    normalized_color_segments = []
    for item in sticker_color_segments[:24]:
        if not isinstance(item, dict):
            continue
        try:
            si = int(item.get("sticker_index", 0))
            ji = int(item.get("segment_index", 0))
        except Exception:
            continue
        part = str(item.get("text", "") or "").strip()
        color_name = str(item.get("color_name", "") or "").strip()
        hex_code = str(item.get("hex", "") or "").strip()
        if 1 <= si <= 8 and 1 <= ji <= 3 and part and color_name and hex_code:
            normalized_color_segments.append({
                "sticker_index": si,
                "segment_index": ji,
                "text": part,
                "color_name": color_name,
                "hex": hex_code,
            })

    normalized = dict(data)
    normalized.update({
        "project_id": project_id,
        "project_name": project_name,
        "created_at": created_at,
        "updated_at": updated_at,
        "style_name": str(data.get("style_name", style.get("name", "")) or ""),
        "font_name": str(data.get("font_name", font.get("name", "")) or ""),
        "text_style": str(data.get("text_style", creative.get("text_style", "")) or ""),
        "generation_method": str(data.get("generation_method", creative.get("generation_method", "")) or ""),
        "transparent_background": bool(data.get("transparent_background", creative.get("transparent_background", False))),
        "style_reference_strength": style_reference_strength,
        "style_reference_elements_enabled": style_reference_elements_enabled,
        "style_reference_elements": style_reference_elements,
        "style_reference_custom_elements": style_reference_custom_elements,
        "style_mode": style_mode,
        "custom_style_snapshot": custom_style_snapshot,
        "character_features": character_features,
        "custom_character": custom_character,
        "custom_character_enabled": custom_character_enabled,
        "sticker_mode": sticker_mode,
        "expression_specs": expression_specs,
        "sticker_texts": sticker_texts,
        "sticker_action_prompts": sticker_action_prompts,
        "sticker_color_segments": normalized_color_segments,
        "schema_version": "v12.project.1",
    })

    normalized["identity"] = {
        "project_id": normalized["project_id"],
        "project_name": normalized["project_name"],
    }
    normalized["timestamps"] = {
        "created_at": normalized["created_at"],
        "updated_at": normalized["updated_at"],
    }
    normalized["creative_settings"] = {
        "style": {
            "name": normalized["style_name"],
            "mode": normalized["style_mode"],
            "custom_snapshot": dict(normalized["custom_style_snapshot"]),
        },
        "font": {"name": normalized["font_name"]},
        "text_style": normalized["text_style"],
        "generation_method": normalized["generation_method"],
        "transparent_background": normalized["transparent_background"],
        "style_reference_strength": normalized["style_reference_strength"],
        "style_reference_elements_enabled": normalized["style_reference_elements_enabled"],
        "style_reference_elements": list(normalized["style_reference_elements"]),
        "style_reference_custom_elements": normalized["style_reference_custom_elements"],
        "character_features": list(normalized["character_features"]),
        "custom_character": normalized["custom_character"],
        "custom_character_enabled": normalized["custom_character_enabled"],
        "sticker_mode": normalized["sticker_mode"],
        "expression_specs": list(normalized["expression_specs"]),
        "sticker_texts": list(normalized["sticker_texts"]),
        "sticker_action_prompts": list(normalized["sticker_action_prompts"]),
        "sticker_color_segments": list(normalized["sticker_color_segments"]),
    }
    normalized["package_settings"] = {
        "main_no": str(data.get("main_no", package.get("main_no", "")) or ""),
        "tab_no": str(data.get("tab_no", package.get("tab_no", "")) or ""),
        "sticker_count": int(package.get("sticker_count", 8) or 8),
    }
    return normalized


st.session_state.setdefault("v12_current_project", _v12_new_project_metadata())
st.session_state.setdefault("v12_projects_local_index", [])

# ============================================================
# V12｜STEP 01A③｜作品暫存與歷史紀錄
# V12｜STEP 01B①｜資料結構標準化
# ============================================================
V12_HISTORY_LIMIT = 12
V12_PROJECT_STICKER_LIMIT = 40

def _v12_png_bytes(image):
    buf = BytesIO()
    image.convert("RGBA").save(buf, "PNG", optimize=True)
    return buf.getvalue()


def _v12_crop_grid_cell(sheet, idx):
    """Create one LINE-size sticker from a standard 4×2 source grid."""
    sw, sh = sheet.size
    col = int(idx) % 4
    row = int(idx) // 4
    x1 = round(col * sw / 4)
    x2 = round((col + 1) * sw / 4)
    y1 = round(row * sh / 2)
    y2 = round((row + 1) * sh / 2)
    crop = sheet.crop((x1, y1, x2, y2)).convert("RGBA")
    out = Image.new("RGBA", (370, 320), (255, 255, 255, 0))
    resampling = getattr(Image, "Resampling", Image)
    scale = min(370 / crop.width, 320 / crop.height)
    resized = crop.resize(
        (max(1, round(crop.width * scale)), max(1, round(crop.height * scale))),
        resampling.LANCZOS,
    )
    out.alpha_composite(resized, ((370 - resized.width) // 2, (320 - resized.height) // 2))
    return out


def _v12_add_grid_breathing_room(sheet, shrink_ratio=0.88):
    """Shrink each transparent 4×2 cell independently to create crop room.

    Image models do not reliably obey relative spacing instructions.  When the
    output genuinely contains an alpha channel, we can preserve every pose and
    visual effect while making the layout deterministic: scale each full cell
    down a little and re-center it in its original position.  Opaque images are
    left untouched because shrinking a painted background would create visible
    rectangular seams between cells.
    """
    source = sheet.convert("RGBA")
    if source.getchannel("A").getextrema()[0] >= 255:
        return source, False

    try:
        ratio = float(shrink_ratio)
    except (TypeError, ValueError):
        ratio = 0.88
    ratio = max(0.75, min(0.95, ratio))
    sw, sh = source.size
    result = Image.new("RGBA", source.size, (255, 255, 255, 0))
    resampling = getattr(Image, "Resampling", Image)

    for idx in range(8):
        col = idx % 4
        row = idx // 4
        x1 = round(col * sw / 4)
        x2 = round((col + 1) * sw / 4)
        y1 = round(row * sh / 2)
        y2 = round((row + 1) * sh / 2)
        cell = source.crop((x1, y1, x2, y2))
        scaled = cell.resize(
            (
                max(1, round(cell.width * ratio)),
                max(1, round(cell.height * ratio)),
            ),
            resampling.LANCZOS,
        )
        result.alpha_composite(
            scaled,
            (
                x1 + (cell.width - scaled.width) // 2,
                y1 + (cell.height - scaled.height) // 2,
            ),
        )
    return result, True


def _v12_get_sticker_basket():
    basket = st.session_state.get("v12_sticker_basket", [])
    if not isinstance(basket, list):
        basket = []
    normalized = []
    for item in basket:
        if not isinstance(item, dict):
            continue
        image_bytes = item.get("image_bytes")
        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            continue
        normalized.append({
            "id": str(item.get("id") or uuid4()),
            "image_bytes": bytes(image_bytes),
            "batch_label": str(item.get("batch_label") or "未命名批次")[:80],
            "source_slot": max(1, min(8, int(item.get("source_slot", 1) or 1))),
            "source_grid_hash": str(item.get("source_grid_hash") or ""),
        })
    st.session_state["v12_sticker_basket"] = normalized
    return normalized


def _v12_add_current_grid_to_sticker_basket(grid_bytes):
    """Crop the current 4×2 result once and add all eight images to the session basket."""
    payload = bytes(grid_bytes or b"")
    if not payload:
        raise ValueError("尚未取得可裁切的原始總圖。")
    source_hash = hashlib.sha256(payload).hexdigest()
    basket = _v12_get_sticker_basket()
    if any(item.get("source_grid_hash") == source_hash for item in basket):
        return False, "這批原始總圖已加入暫存作品籃。"

    sheet = Image.open(BytesIO(payload)).convert("RGBA")
    batch_number = int(st.session_state.get("v12_sticker_basket_batch_number", 0)) + 1
    batch_label = f"批次 {batch_number:02d}"
    additions = []
    for index in range(8):
        item_id = str(uuid4())
        additions.append({
            "id": item_id,
            "image_bytes": _v12_png_bytes(_v12_crop_grid_cell(sheet, index)),
            "batch_label": batch_label,
            "source_slot": index + 1,
            "source_grid_hash": source_hash,
        })
        st.session_state.setdefault(f"v12_basket_select_{item_id}", True)

    st.session_state["v12_sticker_basket"] = basket + additions
    st.session_state["v12_sticker_basket_batch_number"] = batch_number
    return True, f"已將 {batch_label} 的 8 張貼圖加入暫存作品籃。"


def _v12_remove_sticker_basket_item(item_id):
    item_id = str(item_id)
    st.session_state["v12_sticker_basket"] = [
        item for item in _v12_get_sticker_basket()
        if str(item.get("id")) != item_id
    ]
    st.session_state.pop(f"v12_basket_select_{item_id}", None)


def _v12_selected_sticker_basket_items():
    return [
        item for item in _v12_get_sticker_basket()
        if bool(st.session_state.get(f"v12_basket_select_{item['id']}", True))
    ]


def _v12_make_special_sticker(image_bytes, size):
    source = Image.open(BytesIO(image_bytes)).convert("RGBA")
    output = Image.new("RGBA", size, (255, 255, 255, 0))
    resampling = getattr(Image, "Resampling", Image)
    scale = min(size[0] / source.width, size[1] / source.height)
    resized = source.resize(
        (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        ),
        resampling.LANCZOS,
    )
    output.alpha_composite(
        resized,
        ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2),
    )
    return output


@st.fragment
def _v12_render_sticker_basket_workspace():
    """Allow an unlimited session basket, then make one final limited-size project ZIP."""
    basket = _v12_get_sticker_basket()
    if not basket:
        st.info("暫存作品籃目前沒有圖片。先把上方目前這批 8 張加入後，再繼續累積下一批。")
        return

    selected = _v12_selected_sticker_basket_items()
    st.info(
        f"🧺 暫存作品籃共有 {len(basket)} 張；目前已勾選 {len(selected)} 張要打包。"
        f"可不限張數累積；但每一份打包並保存的作品集最多 {V12_PROJECT_STICKER_LIMIT} 張貼圖"
        "（MAIN／TAB 另計，不占上限）。"
    )

    control_left, control_right = st.columns(2)
    with control_left:
        if st.button("☑️ 全部保留下載", key="v12_basket_select_all", use_container_width=True):
            for item in basket:
                st.session_state[f"v12_basket_select_{item['id']}"] = True
            st.rerun(scope="fragment")
    with control_right:
        if st.button("◻️ 全部取消選取", key="v12_basket_deselect_all", use_container_width=True):
            for item in basket:
                st.session_state[f"v12_basket_select_{item['id']}"] = False
            st.rerun(scope="fragment")

    for row_start in range(0, len(basket), 4):
        columns = st.columns(4)
        for offset, column in enumerate(columns):
            index = row_start + offset
            if index >= len(basket):
                continue
            item = basket[index]
            item_id = item["id"]
            with column:
                st.image(
                    item["image_bytes"],
                    caption=f"{item['batch_label']}｜原圖 {item['source_slot']:02d}",
                    use_container_width=True,
                )
                st.session_state.setdefault(f"v12_basket_select_{item_id}", True)
                st.checkbox("保留下載", key=f"v12_basket_select_{item_id}")
                # 手機／平板通常不方便處理 ZIP；作品籃中的每一張都能直接存成 PNG。
                st.download_button(
                    "⬇️ 單張下載 PNG",
                    data=item["image_bytes"],
                    file_name=(
                        f"LINE貼圖_{item['batch_label'].replace(' ', '')}_"
                        f"{item['source_slot']:02d}.png"
                    ),
                    mime="image/png",
                    use_container_width=True,
                    key=f"v12_basket_download_{item_id}",
                )
                if st.button("🗑️ 移除", key=f"v12_basket_remove_{item_id}", use_container_width=True):
                    _v12_remove_sticker_basket_item(item_id)
                    st.rerun(scope="fragment")

    selected = _v12_selected_sticker_basket_items()
    st.divider()
    if not selected:
        st.warning("請先至少勾選 1 張貼圖，才能指定 MAIN／TAB 與建立 ZIP。")
        return
    if len(selected) > V12_PROJECT_STICKER_LIMIT:
        excess = len(selected) - V12_PROJECT_STICKER_LIMIT
        st.warning(
            f"單一作品集最多可保存 {V12_PROJECT_STICKER_LIMIT} 張貼圖；目前已勾選 "
            f"{len(selected)} 張（MAIN／TAB 另計）。請先取消至少 {excess} 張後，再建立 ZIP 或同步保存。"
        )
        return

    options = {
        f"{item['batch_label']}・原圖 {item['source_slot']:02d}": item
        for item in selected
    }
    option_labels = list(options.keys())

    st.markdown("### ⭐ 指定 MAIN／TAB")
    st.caption("MAIN 會輸出為 240×240，TAB 會輸出為 96×74；可從所有已勾選的暫存貼圖中指定。")
    main_key = "v12_basket_main_choice"
    tab_key = "v12_basket_tab_choice"
    if st.session_state.get(main_key) not in option_labels:
        st.session_state[main_key] = option_labels[0]
    main_choices, tab_choices = st.columns(2)
    with main_choices:
        main_label = st.selectbox("選擇 MAIN", option_labels, key=main_key)
    tab_labels = [label for label in option_labels if label != main_label] or [main_label]
    if st.session_state.get(tab_key) not in tab_labels:
        st.session_state[tab_key] = tab_labels[0]
    with tab_choices:
        tab_label = st.selectbox("選擇 TAB", tab_labels, key=tab_key)

    if len(selected) == 1:
        st.caption("只勾選 1 張時，MAIN 與 TAB 會使用同一張圖片；勾選更多張後可分別指定。")
    st.info(
        f"目前會打包 {len(selected)}／{V12_PROJECT_STICKER_LIMIT} 張貼圖｜"
        f"MAIN：{main_label}｜TAB：{tab_label}（兩者另計）"
    )

    st.markdown("### 🏷️ 作品名稱")
    st.caption("此名稱會同時用於「我的作品」紀錄與 ZIP 下載檔案名稱。")
    st.text_input(
        "為這組作品命名",
        key="v12_project_name_input",
        placeholder="例如：阿土的日常貼圖",
        max_chars=80,
        on_change=_v12_project_name_on_change,
    )
    name_preview = _v12_project_snapshot().get("project_name", "")
    if not name_preview:
        st.caption(f"未填寫時，會自動使用：{_v12_default_project_name()}")

    cloud_session = _beta_current_user() if _beta_config() else None
    save_to_cloud = st.checkbox(
        "☁️ 同步保存到雲端「我的作品」",
        value=False,
        key="v12_save_to_cloud_on_package",
        disabled=not bool(cloud_session),
    )
    if cloud_session:
        st.caption("勾選後會同步保存到雲端，需要等候較久時間，請勿重新整理網頁。")
    else:
        st.caption("登入會員後可選擇同步保存到雲端「我的作品」。未勾選時，作品僅暫存於本次瀏覽。")

    _active_style_reference_for_save = (
        st.session_state.get("v12_style_source_mode") == V12_STYLE_REFERENCE_MODE
    )
    has_creation_materials = bool(
        st.session_state.get("uploaded_image_bytes")
        or (
            _active_style_reference_for_save
            and st.session_state.get("v12_style_reference_image_bytes")
        )
    )
    save_creation_materials = st.checkbox(
        "🔒 同時保存私人創作素材（人物照片／畫風參考），方便下次繼續製作",
        value=True,
        key="v12_save_creation_materials_on_package",
        disabled=not bool(cloud_session) or not has_creation_materials,
    )
    if has_creation_materials:
        st.caption("創作素材僅自己可讀；取消勾選後，雲端作品只會保存已選成品與設定。")

    if st.button(
        f"📦 將已選 {len(selected)} 張貼圖一次打包",
        type="primary",
        use_container_width=True,
        key="v12_basket_package_selected",
    ):
        try:
            zip_buffer = BytesIO()
            history_stickers = {}
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for number, item in enumerate(selected, start=1):
                    png_bytes = bytes(item["image_bytes"])
                    history_stickers[f"{number:02d}"] = png_bytes
                    archive.writestr(f"{number:02d}.png", png_bytes)

                main_image = _v12_make_special_sticker(options[main_label]["image_bytes"], (240, 240))
                tab_image = _v12_make_special_sticker(options[tab_label]["image_bytes"], (96, 74))
                main_buffer, tab_buffer = BytesIO(), BytesIO()
                main_image.save(main_buffer, "PNG", optimize=True, dpi=(72, 72))
                tab_image.save(tab_buffer, "PNG", optimize=True, dpi=(72, 72))
                archive.writestr("main.png", main_buffer.getvalue())
                archive.writestr("tab.png", tab_buffer.getvalue())

            zip_bytes = zip_buffer.getvalue()
            history_project = _v12_project_snapshot()
            history_project["project_name"] = (
                history_project.get("project_name") or _v12_default_project_name()
            )
            history_record = _v12_history_record(
                metadata=history_project,
                zip_bytes=zip_bytes,
                # 雲端不會同步這張 4×2 總圖；本機暫存保留原本流程相容性。
                grid_bytes=st.session_state.get("generated_4x2_bytes"),
                main_bytes=main_buffer.getvalue(),
                tab_bytes=tab_buffer.getvalue(),
                sticker_bytes=history_stickers,
                main_no=main_label,
                tab_no=tab_label,
                sticker_count=len(selected),
                source_photo_bytes=st.session_state.get("uploaded_image_bytes") or b"",
                style_reference_bytes=(
                    st.session_state.get("v12_style_reference_image_bytes") or b""
                ) if _active_style_reference_for_save else b"",
            )
            history_record["cloud_main_asset_index"] = next(
                index for index, item in enumerate(selected, start=1)
                if item["id"] == options[main_label]["id"]
            )
            history_record["cloud_tab_asset_index"] = next(
                index for index, item in enumerate(selected, start=1)
                if item["id"] == options[tab_label]["id"]
            )
            download_name = (
                f"{_v12_safe_download_stem(history_project.get('project_name'))}.zip"
            )
            st.session_state["v12_basket_last_zip_bytes"] = zip_bytes
            st.session_state["v12_basket_last_zip_name"] = download_name
            st.success(
                f"🎉 完成！已將 {len(selected)} 張貼圖＋main.png＋tab.png 打包，並加入「我的作品」。"
            )

            active_cloud_session = _beta_current_user() if save_to_cloud and _beta_config() else None
            if save_to_cloud and active_cloud_session:
                try:
                    cloud_record = dict(history_record)
                    if not save_creation_materials:
                        cloud_record["source_photo_bytes"] = b""
                        cloud_record["style_reference_bytes"] = b""
                    cloud_progress = st.progress(
                        0,
                        text="正在準備同步保存到雲端，請勿重新整理網頁…",
                    )
                    cloud_save = _beta_save_library_project(
                        active_cloud_session,
                        cloud_record,
                        progress_callback=lambda progress, message: cloud_progress.progress(
                            progress, text=message + " 請勿重新整理網頁。"
                        ),
                    )
                    if cloud_save.get("saved"):
                        cloud_progress.progress(100, text="雲端作品保存完成！")
                        st.success(
                            f"☁️ 已同步保存至雲端作品庫（{cloud_save['saved_count']}／"
                            f"{cloud_save['library_capacity']} 組）。"
                        )
                    else:
                        cloud_progress.empty()
                        st.warning(
                            f"雲端作品庫已達 {cloud_save['saved_count']}／"
                            f"{cloud_save['library_capacity']} 組；請到「我的作品」下載並刪除一組後再保存。"
                        )
                except Exception:
                    st.warning("本次作品已保留在本機暫存，但雲端保存暫時未完成。請稍後重新打包再試。")
            elif save_to_cloud:
                st.warning("雲端保存尚未執行：請重新登入會員後再試。")
            else:
                st.caption("本次未同步保存到雲端；作品仍可在本次瀏覽的「我的作品」中下載。")
        except Exception as error:
            st.error(f"打包失敗：{error}")

    last_zip = st.session_state.get("v12_basket_last_zip_bytes")
    if last_zip:
        st.download_button(
            "⬇️ 下載最近建立的完整 LINE 套件 ZIP",
            data=last_zip,
            file_name=st.session_state.get("v12_basket_last_zip_name") or "LINE貼圖.zip",
            mime="application/zip",
            use_container_width=True,
            key="v12_basket_download_latest",
        )


def _v12_build_assets_manifest(grid_bytes=None, main_bytes=None, tab_bytes=None,
                               sticker_bytes=None, zip_bytes=None):
    """Store lightweight asset descriptors only; binary data stays in legacy fields."""
    sticker_bytes = dict(sticker_bytes or {})
    return {
        "grid": {"present": bool(grid_bytes), "bytes": len(grid_bytes or b"")},
        "stickers": {
            "count": len(sticker_bytes),
            "numbers": sorted(str(k) for k in sticker_bytes.keys()),
            "total_bytes": sum(len(v or b"") for v in sticker_bytes.values()),
        },
        "main": {"present": bool(main_bytes), "bytes": len(main_bytes or b"")},
        "tab": {"present": bool(tab_bytes), "bytes": len(tab_bytes or b"")},
        "zip": {"present": bool(zip_bytes), "bytes": len(zip_bytes or b"")},
    }


def _v12_history_record(metadata, zip_bytes, grid_bytes=None,
                        main_bytes=None, tab_bytes=None,
                        sticker_bytes=None, main_no=None, tab_no=None,
                        sticker_count=None, source_photo_bytes=None,
                        style_reference_bytes=None):
    metadata = _v12_normalize_project_metadata(metadata)
    project_id = str(metadata.get("project_id") or uuid4())
    now = _v12_now_iso()

    metadata["project_id"] = project_id
    metadata["updated_at"] = now
    metadata["package_settings"] = {
        "main_no": str(main_no or metadata.get("package_settings", {}).get("main_no", "")),
        "tab_no": str(tab_no or metadata.get("package_settings", {}).get("tab_no", "")),
        "sticker_count": max(1, int(sticker_count or len(sticker_bytes or {}) or 8)),
    }
    metadata = _v12_normalize_project_metadata(metadata)

    record = dict(metadata)
    record.update({
        "main_no": metadata["package_settings"]["main_no"],
        "tab_no": metadata["package_settings"]["tab_no"],
        "zip_bytes": bytes(zip_bytes or b""),
        "grid_bytes": bytes(grid_bytes or b""),
        "main_bytes": bytes(main_bytes or b""),
        "tab_bytes": bytes(tab_bytes or b""),
        "sticker_bytes": dict(sticker_bytes or {}),
        "source_photo_bytes": bytes(source_photo_bytes or b""),
        "style_reference_bytes": bytes(style_reference_bytes or b""),
    })
    record["assets_manifest"] = _v12_build_assets_manifest(
        grid_bytes=record["grid_bytes"],
        main_bytes=record["main_bytes"],
        tab_bytes=record["tab_bytes"],
        sticker_bytes=record["sticker_bytes"],
        zip_bytes=record["zip_bytes"],
    )

    projects = list(st.session_state.get("v12_projects_local_index", []))
    projects = [p for p in projects if str(p.get("project_id")) != project_id]
    projects.insert(0, record)
    st.session_state["v12_projects_local_index"] = projects[:V12_HISTORY_LIMIT]

    project = _v12_current_project()
    project.update(_v12_normalize_project_metadata(metadata))
    st.session_state["v12_current_project"] = project
    return record


def _v12_load_history_record(record):
    record = dict(record or {})
    metadata = _v12_normalize_project_metadata(record)

    st.session_state["v12_current_project"] = metadata
    st.session_state["v12_project_name_input"] = metadata.get("project_name", "")
    st.session_state["generated_4x2_bytes"] = record.get("grid_bytes") or None

    source_photo = bytes(record.get("source_photo_bytes") or b"")
    style_reference = bytes(record.get("style_reference_bytes") or b"")
    st.session_state["uploaded_image_bytes"] = source_photo or None
    st.session_state["v12_style_reference_image_bytes"] = style_reference or None
    if style_reference:
        st.session_state["v12_style_reference_filename"] = "已保存的畫風參考"
        st.session_state["v12_style_reference_fingerprint"] = hashlib.sha256(
            style_reference
        ).hexdigest()
    else:
        st.session_state["v12_style_reference_filename"] = ""
        st.session_state["v12_style_reference_fingerprint"] = ""
    st.session_state["v12_style_source_mode"] = (
        V12_STYLE_REFERENCE_MODE if style_reference else V12_STYLE_PRESET_MODE
    )

    st.session_state["v12_style_reference_strength"] = str(
        metadata.get("style_reference_strength", "明顯融入") or "明顯融入"
    )
    st.session_state["v12_style_reference_elements_enabled"] = False
    st.session_state["v12_style_reference_elements"] = []
    st.session_state["v12_style_reference_custom_elements"] = str(
        metadata.get("style_reference_custom_elements", "") or ""
    )

    # 已保存的精選成品可直接重新打包，不需重新生成 AI 圖片。
    stored_stickers = record.get("sticker_bytes")
    if isinstance(stored_stickers, dict) and stored_stickers:
        restored = []
        for position, (number, image_bytes) in enumerate(
            sorted(
                stored_stickers.items(),
                key=lambda item: (
                    0, int(str(item[0]))
                ) if str(item[0]).isdigit() else (1, str(item[0])),
            ),
            start=1,
        ):
            if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
                continue
            item_id = str(uuid4())
            restored.append({
                "id": item_id,
                "image_bytes": bytes(image_bytes),
                "batch_label": "作品集",
                "source_slot": position,
                "source_grid_hash": f"library:{metadata.get('project_id', '')}",
            })
            st.session_state[f"v12_basket_select_{item_id}"] = True
        st.session_state["v12_sticker_basket"] = restored
        st.session_state["v12_sticker_basket_batch_number"] = 1
        manifest = record.get("cloud_asset_manifest")
        if isinstance(manifest, dict):
            main_index = max(1, min(len(restored), int(manifest.get("main_asset_index", 1) or 1)))
            tab_index = max(1, min(len(restored), int(manifest.get("tab_asset_index", 1) or 1)))
            st.session_state["v12_basket_main_choice"] = f"作品集・原圖 {main_index:02d}"
            st.session_state["v12_basket_tab_choice"] = f"作品集・原圖 {tab_index:02d}"

    package = metadata.get("package_settings", {})
    st.session_state["v12_loaded_history_main_no"] = str(
        record.get("main_no") or package.get("main_no") or ""
    )
    st.session_state["v12_loaded_history_tab_no"] = str(
        record.get("tab_no") or package.get("tab_no") or ""
    )

    # 作品專屬創作設定：載回時覆蓋目前畫面設定，但不改寫使用者的預設資料庫。
    st.session_state["v10_character_options"] = list(
        metadata.get("character_features", []) or []
    )
    st.session_state["v10_character_custom_1"] = str(
        metadata.get("custom_character", "") or ""
    )
    st.session_state["v10_character_enabled_1"] = bool(
        metadata.get("custom_character_enabled", False)
    )

    loaded_style_mode = str(metadata.get("style_mode", "") or "")
    if loaded_style_mode:
        st.session_state["v10_style_mode"] = loaded_style_mode
    loaded_custom_style = metadata.get("custom_style_snapshot")
    if (
        loaded_style_mode == "⭐ 自定義風格"
        and isinstance(loaded_custom_style, dict)
        and str(loaded_custom_style.get("prompt", "") or "").strip()
    ):
        st.session_state["v12_loaded_project_custom_style"] = dict(loaded_custom_style)
        # 先維持作品快照，使用者主動選擇已儲存風格後才會切換成新的設定。
        st.session_state["v12_saved_custom_style_choice"] = "↓ 請選擇已儲存風格"
        st.session_state["v12_style_editor_name"] = str(
            loaded_custom_style.get("name", "") or ""
        )
        st.session_state["v12_style_editor_prompt"] = str(
            loaded_custom_style.get("prompt", "") or ""
        )
    else:
        st.session_state.pop("v12_loaded_project_custom_style", None)

    loaded_sticker_mode = str(metadata.get("sticker_mode", "text") or "text")
    st.session_state["v12_sticker_content_mode"] = (
        V12_STICKER_EXPRESSION_MODE
        if loaded_sticker_mode == "expression"
        else V12_STICKER_TEXT_MODE
    )
    for i, spec in enumerate(_v12_normalize_expression_specs(
        metadata.get("expression_specs", [])
    )):
        st.session_state[f"v12_expression_emotion_{i}"] = spec["emotion"]
        st.session_state[f"v12_expression_emotion_random_{i}"] = spec[
            "emotion_random"
        ]
        st.session_state[f"v12_expression_effect_{i}"] = spec["effect"]
        st.session_state[f"v12_expression_effect_action_prompt_{i}"] = spec[
            "effect_action_prompt"
        ]

    texts = list(metadata.get("sticker_texts") or [""] * 8)
    for i in range(8):
        value = str(texts[i] if i < len(texts) else "")
        st.session_state[f"sticker_text_{i}"] = value
        st.session_state[f"v12_sticker_text_widget_{i}"] = value
    for i, action_prompt in enumerate(_v12_normalize_sticker_action_prompts(
        metadata.get("sticker_action_prompts", [])
    )):
        st.session_state[f"v12_sticker_action_prompt_{i}"] = action_prompt
        st.session_state[f"v12_sticker_action_prompt_widget_{i}"] = action_prompt
        st.session_state[f"v12_sticker_action_enabled_{i}"] = bool(action_prompt)
        st.session_state[f"v12_sticker_action_enabled_widget_{i}"] = bool(action_prompt)
    st.session_state["v12_sticker_action_draft"] = [
        {"enabled": bool(action_prompt), "prompt": action_prompt}
        for action_prompt in _v12_normalize_sticker_action_prompts(
            metadata.get("sticker_action_prompts", [])
        )
    ]

    _v12_clear_text_color_segments()
    for item in metadata.get("sticker_color_segments", []) or []:
        try:
            si = int(item.get("sticker_index", 0)) - 1
            ji = int(item.get("segment_index", 0)) - 1
        except Exception:
            continue
        if 0 <= si < 8 and 0 <= ji < 3:
            segs = _v12_get_color_segments(si)
            segs[ji] = {
                "text": str(item.get("text", "") or ""),
                "color_name": str(item.get("color_name", "") or ""),
                "hex": str(item.get("hex", "") or ""),
            }
            st.session_state[f"v12_text_color_count_{si}"] = max(
                int(st.session_state.get(f"v12_text_color_count_{si}", 0)),
                ji + 1
            )
            st.session_state[f"v12_text_color_enabled_{si}"] = True
            st.session_state[f"v12_text_color_enabled_widget_{si}"] = True


def _v12_delete_history_record(project_id):
    projects = list(st.session_state.get("v12_projects_local_index", []))
    st.session_state["v12_projects_local_index"] = [
        p for p in projects if str(p.get("project_id")) != str(project_id)
    ]


def _v12_current_project():
    project = st.session_state.get("v12_current_project")
    if not isinstance(project, dict):
        project = _v12_new_project_metadata()
    project = _v12_normalize_project_metadata(project)
    st.session_state["v12_current_project"] = project
    return project

def _v12_default_project_name():
    return datetime.now().strftime("LINE貼圖_%Y%m%d_%H%M")

def _v12_normalize_project_name(value):
    value = str(value or "").strip()
    return value[:80] if value else ""

def _v12_safe_download_stem(value):
    value = _v12_normalize_project_name(value) or _v12_default_project_name()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return value[:80] or _v12_default_project_name()

def _v12_project_name_on_change():
    project = _v12_current_project()
    project["project_name"] = _v12_normalize_project_name(
        st.session_state.get("v12_project_name_input", "")
    )
    project["updated_at"] = _v12_now_iso()
    st.session_state["v12_current_project"] = project

def _v12_project_snapshot():
    """取得目前作品的可保存 metadata 快照；絕不包含 API Key。"""
    project = dict(_v12_current_project())
    project["updated_at"] = _v12_now_iso()
    project["project_name"] = _v12_normalize_project_name(
        st.session_state.get("v12_project_name_input", project.get("project_name", ""))
    )
    project["sticker_texts"] = [
        str(st.session_state.get(f"sticker_text_{i}", ""))
        for i in range(8)
    ]
    project["sticker_color_segments"] = _v12_valid_color_segments(
        project["sticker_texts"]
    )
    project["sticker_action_prompts"] = _v12_get_sticker_action_prompts()
    project["sticker_mode"] = _v12_sticker_mode_value()
    project["expression_specs"] = _v12_get_expression_specs()
    project["transparent_background"] = bool(
        st.session_state.get("transparent_png_option", False)
    )
    project["style_reference_strength"] = str(
        st.session_state.get("v12_style_reference_strength", "明顯融入")
    )
    project["style_reference_elements_enabled"] = False
    project["style_reference_elements"] = []
    project["style_reference_custom_elements"] = str(
        st.session_state.get("v12_style_reference_custom_elements", "") or ""
    ).strip()[:1200]
    project["character_features"] = _v12_clean_character_features(
        st.session_state.get("v10_character_options", [])
    )
    project["custom_character"] = str(
        st.session_state.get("v10_character_custom_1", "") or ""
    ).strip()
    project["custom_character_enabled"] = bool(
        st.session_state.get("v10_character_enabled_1", False)
    )
    project["custom_style_snapshot"] = _v12_current_custom_style_snapshot()
    project["style_mode"] = project["custom_style_snapshot"].get("mode", "")
    # V12｜STEP 01A③ FIX：
    # 若使用「⭐ 自定義風格」，作品庫不要只顯示泛稱，
    # 而是記錄使用者實際選取的「已儲存風格名稱」。
    _style_mode = str(
        st.session_state.get("v10_style_mode", project.get("style_name", ""))
    )
    if _style_mode == "⭐ 自定義風格":
        _style_snapshot_name = str(
            project["custom_style_snapshot"].get("name", "") or ""
        ).strip()
        if _style_snapshot_name:
            project["style_name"] = _style_snapshot_name
        else:
            project["style_name"] = _style_mode
    else:
        project["style_name"] = _style_mode

    project["text_style"] = str(
        st.session_state.get("v8_text_style", project.get("text_style", ""))
    )
    selected_font = st.session_state.get("v8_selected_font")
    if selected_font:
        try:
            project["font_name"] = (
                f"{int(selected_font):03d}｜"
                f"{V8_TEXT_EFFECT_CATALOG[int(selected_font)]}"
            )
        except Exception:
            project["font_name"] = str(selected_font)
    else:
        project["font_name"] = ""

    # 僅記錄生成方式，不記錄 API Key。
    api_mode = str(st.session_state.get("public_api_mode", ""))
    if "自己的" in api_mode:
        project["generation_method"] = "own_api"
    elif "免費" in api_mode or "網站" in api_mode:
        project["generation_method"] = "website_api"

    st.session_state["v12_current_project"] = project
    return dict(project)


for i in range(8):
    st.session_state.setdefault(f"sticker_text_{i}", "")
    st.session_state.setdefault(
        f"v12_sticker_text_widget_{i}",
        st.session_state.get(f"sticker_text_{i}", ""),
    )
st.session_state.setdefault("uploaded_image_bytes", None)
st.session_state.setdefault("v12_style_reference_image_bytes", None)
st.session_state.setdefault("v12_style_reference_filename", "")
st.session_state.setdefault("v12_style_reference_fingerprint", "")
st.session_state.setdefault("v12_style_source_mode", V12_STYLE_PRESET_MODE)
st.session_state.setdefault("v12_expression_used_recipe_ids", [])
st.session_state.setdefault("v12_expression_used_visual_pack_ids", [])
st.session_state.setdefault("v12_expression_last_recipe_labels", [])
st.session_state.setdefault("v12_expression_recipe_cycle", 1)
st.session_state.setdefault("v12_style_reference_strength", "明顯融入")
st.session_state.setdefault("v12_style_reference_elements_enabled", False)
st.session_state.setdefault("v12_style_reference_elements", [])
st.session_state.setdefault("v12_style_reference_custom_elements", "")
st.session_state.setdefault("generated_4x2_bytes", None)
st.session_state.setdefault("v12_grid_breathing_room_applied", False)
st.session_state.setdefault("last_prompt", "")
st.session_state.setdefault("crop_boxes", None)
st.session_state.setdefault("v12_sticker_basket", [])
st.session_state.setdefault("v12_sticker_basket_batch_number", 0)
st.session_state.setdefault("v12_basket_last_zip_bytes", None)
st.session_state.setdefault("v12_basket_last_zip_name", "")
st.session_state.setdefault("v12_ai_copy_candidates", [])
st.session_state.setdefault("v12_ai_copy_topic", "")
st.session_state.setdefault("v12_ai_copy_tone", "😂 搞笑自然")
st.session_state.setdefault("v12_ai_copy_selected", [])

# ============================================================
# V12 試作版｜局部文字上色
# 每格最多 3 段：使用者自行輸入文字片段，再用色卡點選顏色。
# ============================================================
V12_TEXT_COLOR_PALETTE = [
    ("白色", "#FFFFFF", "⬜"),
    ("黑色", "#111111", "⬛"),
    ("紅色", "#F5222D", "🟥"),
    ("橘色", "#FA8C16", "🟧"),
    ("黃色", "#FADB14", "🟨"),
    ("綠色", "#52C41A", "🟩"),
    ("藍色", "#1677FF", "🟦"),
    ("紫色", "#722ED1", "🟪"),
]

def _v12_default_color_segments():
    return [
        {"text": "", "color_name": "", "hex": ""}
        for _ in range(3)
    ]

def _v12_sync_text_color_enabled(i):
    """把局部文字上色 checkbox 的 widget 狀態同步到獨立 canonical state。
    使用獨立 widget key，避免後續 rerun / 新增片段時直接改動已建立的 checkbox。
    """
    widget_key = f"v12_text_color_enabled_widget_{i}"
    st.session_state[f"v12_text_color_enabled_{i}"] = bool(
        st.session_state.get(widget_key, False)
    )

def _v12_init_color_segment_state():
    for i in range(8):
        canonical_key = f"v12_text_color_enabled_{i}"
        widget_key = f"v12_text_color_enabled_widget_{i}"
        st.session_state.setdefault(canonical_key, False)
        # checkbox 使用獨立 widget key；只在第一次建立時從 canonical state 帶入。
        st.session_state.setdefault(widget_key, bool(st.session_state.get(canonical_key, False)))
        st.session_state.setdefault(f"v12_text_color_count_{i}", 0)
        segs = st.session_state.get(f"v12_text_color_segments_{i}")
        if not isinstance(segs, list):
            segs = _v12_default_color_segments()
        normalized = _v12_default_color_segments()
        for j in range(min(3, len(segs))):
            item = segs[j] if isinstance(segs[j], dict) else {}
            normalized[j] = {
                "text": str(item.get("text", "") or ""),
                "color_name": str(item.get("color_name", "") or ""),
                "hex": str(item.get("hex", "") or ""),
            }
        st.session_state[f"v12_text_color_segments_{i}"] = normalized

def _v12_get_color_segments(i):
    segs = st.session_state.get(f"v12_text_color_segments_{i}")
    if not isinstance(segs, list):
        segs = _v12_default_color_segments()
        st.session_state[f"v12_text_color_segments_{i}"] = segs
    return segs

def _v12_set_segment_color(i, j, color_name, hex_code):
    segs = _v12_get_color_segments(i)
    if 0 <= j < 3:
        segs[j]["color_name"] = str(color_name)
        segs[j]["hex"] = str(hex_code)


def _v12_pick_segment_color(i, j, color_name, hex_code):
    """Colour buttons update state before their fragment redraws."""
    _v12_set_segment_color(i, j, color_name, hex_code)


def _v12_paste_sticker_text_to_color_segment(i, j):
    """Copy the matching sticker text once, while keeping the segment editable."""
    segs = _v12_get_color_segments(i)
    if not 0 <= j < len(segs):
        return
    value = str(st.session_state.get(f"sticker_text_{i}", "") or "")
    segs[j]["text"] = value
    st.session_state[f"v12_text_color_segment_{i}_{j}"] = value


def _v12_add_text_color_segment(i):
    count = int(st.session_state.get(f"v12_text_color_count_{i}", 0))
    st.session_state[f"v12_text_color_count_{i}"] = min(3, count + 1)

def _v12_valid_color_segments(texts):
    out = []
    for i in range(8):
        source = str(texts[i] or "") if i < len(texts) else ""
        if not source.strip():
            continue
        for j, seg in enumerate(_v12_get_color_segments(i)):
            part = str(seg.get("text", "") or "").strip()
            color_name = str(seg.get("color_name", "") or "").strip()
            hex_code = str(seg.get("hex", "") or "").strip()
            if part and color_name and hex_code and part in source:
                out.append({
                    "sticker_index": i + 1,
                    "segment_index": j + 1,
                    "text": part,
                    "color_name": color_name,
                    "hex": hex_code,
                })
    return out

def _v12_clear_text_color_segments():
    for i in range(8):
        st.session_state[f"v12_text_color_enabled_{i}"] = False
        st.session_state[f"v12_text_color_enabled_widget_{i}"] = False
        st.session_state[f"v12_text_color_count_{i}"] = 0
        st.session_state[f"v12_text_color_segments_{i}"] = _v12_default_color_segments()
        for j in range(3):
            st.session_state.pop(f"v12_text_color_segment_{i}_{j}", None)


def _v12_action_draft():
    """Persistent action draft, deliberately independent from conditional widgets."""
    raw = st.session_state.get("v12_sticker_action_draft")
    raw = raw if isinstance(raw, list) else []
    normalized = []
    for i in range(8):
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        normalized.append({
            "enabled": bool(item.get(
                "enabled", st.session_state.get(f"v12_sticker_action_enabled_{i}", False)
            )),
            "prompt": str(item.get(
                "prompt", st.session_state.get(f"v12_sticker_action_prompt_{i}", "")
            ) or "").strip()[:500],
        })
    st.session_state["v12_sticker_action_draft"] = normalized
    return normalized


def _v12_write_sticker_action_draft_slot(i, enabled=None, prompt=None):
    draft = _v12_action_draft()
    if 0 <= i < 8:
        if enabled is not None:
            draft[i]["enabled"] = bool(enabled)
        if prompt is not None:
            draft[i]["prompt"] = str(prompt or "").strip()[:500]
        st.session_state[f"v12_sticker_action_enabled_{i}"] = bool(draft[i]["enabled"])
        st.session_state[f"v12_sticker_action_prompt_{i}"] = str(draft[i]["prompt"])
    st.session_state["v12_sticker_action_draft"] = draft


def _v12_capture_sticker_action_draft():
    """Capture visible widgets before their text-mode branch disappears."""
    for i in range(8):
        draft = _v12_action_draft()[i]
        enabled = st.session_state.get(
            f"v12_sticker_action_enabled_widget_{i}", draft["enabled"]
        )
        prompt = st.session_state.get(
            f"v12_sticker_action_prompt_widget_{i}", draft["prompt"]
        )
        _v12_write_sticker_action_draft_slot(i, enabled=enabled, prompt=prompt)


def _v12_restore_sticker_action_widgets():
    """Restore widget values from the persistent draft before text widgets render."""
    for i, item in enumerate(_v12_action_draft()):
        enabled = bool(item["enabled"])
        prompt = str(item["prompt"])
        st.session_state[f"v12_sticker_action_enabled_{i}"] = enabled
        st.session_state[f"v12_sticker_action_prompt_{i}"] = prompt
        st.session_state[f"v12_sticker_action_enabled_widget_{i}"] = enabled
        st.session_state[f"v12_sticker_action_prompt_widget_{i}"] = prompt


def _v12_sync_sticker_action_enabled(i):
    _v12_write_sticker_action_draft_slot(
        i,
        enabled=st.session_state.get(f"v12_sticker_action_enabled_widget_{i}", False),
    )


def _v12_sync_sticker_action_prompt(i):
    _v12_write_sticker_action_draft_slot(
        i,
        prompt=st.session_state.get(f"v12_sticker_action_prompt_widget_{i}", ""),
    )


def _v12_init_sticker_action_state():
    _v12_action_draft()
    for i, item in enumerate(_v12_action_draft()):
        st.session_state.setdefault(f"v12_sticker_action_enabled_{i}", bool(item["enabled"]))
        st.session_state.setdefault(f"v12_sticker_action_prompt_{i}", str(item["prompt"]))


def _v12_get_sticker_action_prompts():
    return [item["prompt"] if item["enabled"] else "" for item in _v12_action_draft()]


def _v12_clear_sticker_action_prompts():
    st.session_state["v12_sticker_action_draft"] = [
        {"enabled": False, "prompt": ""} for _ in range(8)
    ]
    for i in range(8):
        st.session_state[f"v12_sticker_action_enabled_{i}"] = False
        st.session_state[f"v12_sticker_action_enabled_widget_{i}"] = False
        st.session_state[f"v12_sticker_action_prompt_{i}"] = ""
        st.session_state[f"v12_sticker_action_prompt_widget_{i}"] = ""


_v12_init_color_segment_state()
_v12_init_sticker_action_state()

def _v12_sync_sticker_text(i):
    """把文字輸入 widget 的值同步到不受條件式 rerun 影響的 canonical state。"""
    key = f"v12_sticker_text_widget_{i}"
    value = str(st.session_state.get(key, "") or "")
    st.session_state[f"sticker_text_{i}"] = value

def get_texts():
    return [st.session_state.get(f"sticker_text_{i}", "") for i in range(8)]

def set_texts(values):
    values = list(values)[:8] + [""] * 8
    for i in range(8):
        value = str(values[i])
        st.session_state[f"sticker_text_{i}"] = value
        # 同步到實際輸入 widget，避免後續任何 rerun 讓 01～08 文字回復成空白。
        st.session_state[f"v12_sticker_text_widget_{i}"] = value
    # 批次換字後清除舊的局部上色，避免顏色片段與新文字不一致。
    _v12_clear_text_color_segments()
    # 新的一批文字不沿用舊的動作描述，避免指令與文字語意不一致。
    _v12_clear_sticker_action_prompts()

def base_boxes(w, h):
    boxes = []
    for i in range(8):
        c, r = i % 4, i // 4
        x1 = round(c * w / 4)
        x2 = round((c + 1) * w / 4)
        y1 = round(r * h / 2)
        y2 = round((r + 1) * h / 2)
        boxes.append([x1, y1, x2, y2])
    return boxes

# ============================================================
# V12｜AI 貼圖文案助手 V6.2
# - 保留「主題＋文案語氣」介面
# - 強化「文字構成畫面 → 找生活瞬間 → 真人反應 → 台灣語感／經典梗 → 短句化」流程
# - 與 gpt-image-2 圖片生成分開，不改動原本圖片生成流程
# - AI 文案助手使用會員每日額度，不扣除圖片生成點數
# - 文案額度與圖片點數分開計算
# ============================================================
V12_AI_COPY_MODEL = "gpt-5.6-luna"
V12_AI_COPY_TONES = [
    "😂 搞笑自然",
    "😤 憤憤不平",
    "😈 嘲諷吐槽",
    "☁️ 委屈可愛",
    "👑 霸氣有梗",
    "💬 台灣口語",
    "✨ 溫暖療癒",
]

def _v12_ai_copy_generate(topic, tone, api_key):
    """V7.2｜AI 貼圖文字助手。
    核心：優先幫使用者快速找到「真的會用」的貼圖字眼，
    混合超日常、經典用語、主題場景與少量驚喜；不追求每句都想梗。

    注意：API 呼叫結構刻意沿用已實測可工作的 V6.5，這一版只更換文案思考邏輯，
    避免測試文案方向時同時改動 API 協定造成無法定位的錯誤。
    """
    topic = str(topic or "").strip()
    tone = str(tone or "").strip()
    if not topic:
        raise ValueError("missing_topic")

    if not api_key:
        raise ValueError("missing_api_key")
    _copy_client = OpenAI(api_key=api_key)

    schema = {
        "type": "object",
        "properties": {
            "phrases": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 16,
                "maxItems": 16,
            }
        },
        "required": ["phrases"],
        "additionalProperties": False,
    }

    _previous = st.session_state.get("v12_ai_copy_candidates", []) or []
    _previous = [str(x).strip() for x in _previous if str(x).strip()][:16]

    _pool_samples = []
    try:
        _theme_pools = st.session_state.get("v12_theme_pools", {}) or {}
        for _theme_name, _phrases in _theme_pools.items():
            for _phrase in (_phrases or []):
                _phrase = str(_phrase).strip()
                if _phrase and _phrase not in _pool_samples:
                    _pool_samples.append(_phrase)
                if len(_pool_samples) >= 80:
                    break
            if len(_pool_samples) >= 80:
                break
    except Exception:
        _pool_samples = []

    system_prompt = (
        "你是一位非常懂台灣日常聊天與 LINE 貼圖的文字助手。"
        "你的主要工作不是參加文案比賽，也不是每一句都想出新梗；"
        "而是幫使用者快速找到『這句我平常真的會用』的貼圖文字。\n\n"
        "【V7 核心任務】"
        "使用者給你一個主題與語氣，你要整理出一批可以直接拿來做 LINE 貼圖的短句。"
        "16 句放在一起時，使用者應該能很快挑出至少 8 句想留下。"
        "超日常、很普通但很好用的句子是高價值結果，不要因為不夠有創意就淘汰。"
        "經典老梗、熟悉的網路用語、台灣口語也可以直接使用；熟悉不代表不好用。"
        "主題場景用語同樣重要。少量真正自然的意外句可以混進來，但驚喜只是調味。\n\n"
        "【四種來源，自然混合】"
        "1. 超日常：收到、好的、等一下、我看看、馬上、先這樣、沒問題、辛苦了、晚點回等。"
        "2. 經典／網路語感：蛤？、傻眼、笑死、真的假的、不要鬧、先不要、修但幾勒、我就爛、是在哈囉、真的會謝等；符合情境即可使用。"
        "3. 主題場景：從主題找大家真的會遇到的小事件，再給出自然反應。"
        "4. 少量驚喜：偶爾加入反轉、擬人、比喻、雙關或有點怪但成立的短句。"
        "不要刻意平均分配四類，讓結果自然混合。\n\n"
        "【最重要的判斷標準】"
        "每一句都先問：『如果今天真的遇到這件事，我會不會把這句傳給朋友、同事、家人或群組？』"
        "如果會，就算普通也可以留下。"
        "如果只是看起來很有創意、很像 AI 在努力寫金句，但真人不太會這樣說，就改掉。"
        "不要為了顯得厲害，把正常的話硬改得很奇怪。\n\n"
        "【台灣語感】"
        "使用自然繁體中文與台灣聊天語感。啦、欸、齁、捏、咧等語助詞可以使用，但不要每句都塞。"
        "經典老梗可以使用；不要刻意追新，也不要刻意避開老梗。"
        "不要大量堆流行語來假裝台灣。\n\n"
        "【短句】"
        "以 2～10 個中文字為主要目標。口語碎句、反問、感嘆、半句話都可以。"
        "貼圖文字要一眼讀完，不必像完整句子，也不要寫成文章、標語或雞湯。\n\n"
        "【語氣】"
        f"主題：{topic}。\n"
        f"使用者選擇的語氣：{tone}。\n"
        "語氣會影響措辭與反應，但不要因為選了搞笑自然就強迫每一句搞笑。"
        "所有語氣都以真人會說、真的能拿來聊天為前提。\n\n"
        "【不要做】"
        "不要為了創新而創新。不要每句都想梗。不要過度文青、雞湯、廣告腔。"
        "不要把主題硬塞進每句。不要只是同義詞替換。不要大量產生 AI 式金句。"
        "不要刻意避開經典用語。不要因為一句很普通就淘汰它。\n\n"
        "【輸出】"
        "只輸出最後 16 句候選文案。不要輸出分析、分類、編號、引號、emoji 或其他說明。"
    )

    _avoid_text = ""
    if _previous:
        _avoid_text += (
            "\n\n上一批已生成的 16 句。這次盡量換掉相同句子與高度相似的說法；"
            "但如果某個經典或超常用語真的非常適合主題，可以自然再次出現：\n"
            + "\n".join(f"- {x}" for x in _previous)
        )
    if _pool_samples:
        _avoid_text += (
            "\n\n使用者過去保存過的文案池。盡量補充不同的句子，不要機械重複：\n"
            + "、".join(_pool_samples)
        )

    user_prompt = (
        f"使用者主題：{topic}\n"
        f"希望的文案語氣：{tone}\n\n"
        "請產生 16 句可以直接拿來做 LINE 貼圖的候選文字。"
        "先在內部想像這個主題下真實會發生的生活小情境，再決定人物最自然的第一反應。"
        "結果可以混合：很普通但超好用的話、經典用語、台灣口語、主題場景反應，以及少量意外句。"
        "不要把 16 句全部做成梗，也不要為了看起來厲害而把正常話改得很怪。"
        "最重要的是：使用者看到結果後，可以快速勾出自己真的想用的 8 句。"
        + _avoid_text
    )

    # 保持 V6.5 已實測的 API 呼叫結構，不在這一版改 API 協定。
    response = _copy_client.responses.create(
        model=V12_AI_COPY_MODEL,
        input=[
            {"role": "developer", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "line_sticker_copy",
                "strict": True,
                "schema": schema,
            }
        },
        max_output_tokens=1600,
    )
    raw = str(response.output_text or "").strip()
    data = json.loads(raw)
    phrases = data.get("phrases", []) if isinstance(data, dict) else []
    phrases = [str(x).strip() for x in phrases if str(x).strip()]
    phrases = list(dict.fromkeys(phrases))
    if len(phrases) != 16:
        raise ValueError("invalid_phrase_count")
    return phrases

@st.fragment
def _v12_render_ai_copy_assistant():
    """V12｜AI 文案助手＋多主題暫存池（Session-only）。
    規則：主題只在使用者完成 AI 生成並選擇儲存文案後建立；
    每次 AI 產生 16 句，使用者可只勾選想保存的句子；
    每個主題最多 30 句；本次創作最多同時使用 3 個已建立主題；
    最後從已選主題的詞語池中挑選 8 句套用到 01～08。
    """
    st.session_state.setdefault("v12_theme_pools", {})
    st.session_state.setdefault("v12_selected_themes", [])
    st.session_state.setdefault("v12_ai_copy_candidates", [])
    st.session_state.setdefault("v12_ai_copy_selected", [])
    st.session_state.setdefault("v12_ai_copy_topic", "")
    st.session_state.setdefault("v12_ai_copy_topic_used", "")
    st.session_state.setdefault("v12_ai_copy_tone_used", "")

    # ------------------------------------------------------------
    # ① 主題：這裡只是「創作輸入」，不會因為打字就自動建立主題。
    #    只有 AI 生成後，使用者實際儲存至少一個文案時，才建立主題。
    # ------------------------------------------------------------
    _topic_before = str(st.session_state.get("v12_ai_copy_topic", "") or "").strip()
    _topic = st.text_input(
        "💡 你想做什麼主題？",
        key="v12_ai_copy_topic",
        placeholder="例如：醫師日常篇、情侶吵架篇、媽媽的日常",
    ).strip()

    # 使用者修改尚未建立的創作主題時，清除上一批候選文案，避免拿舊主題的結果誤存到新主題。
    if _topic != _topic_before:
        st.session_state["v12_ai_copy_candidates"] = []
        st.session_state["v12_ai_copy_selected"] = []
        # 主題改變時，舊一批 16 句的 checkbox 狀態也一併清除。
        for _i in range(16):
            st.session_state.pop(f"v12_ai_copy_pick_{_i}", None)

    # ------------------------------------------------------------
    # ② 文案語氣
    # ------------------------------------------------------------
    _tone = st.selectbox(
        "🎭 希望文案是什麼感覺？",
        V12_AI_COPY_TONES,
        key="v12_ai_copy_tone",
    )

    # 在點選生成前就先讓會員看見今日剩餘額度。
    # 勾選候選文案時只顯示既有額度，避免每次局部互動都重新驗證登入。
    _copy_status_session = st.session_state.get("beta_auth_session")
    if _copy_status_session:
        try:
            _copy_status = _beta_copy_usage_status_cached(_copy_status_session)
            st.info(
                "✨ 今日 AI幫想額度（台灣時間）："
                f"剩 {_copy_status['remaining_count']} / {_copy_status['daily_limit']} 次"
            )
        except Exception:
            st.warning("今日文案額度暫時無法確認，請稍後再試。")
    else:
        st.info("完成 Google 登入後，即可查看並使用每日 AI幫想額度。")

    _generate = st.button(
        "✨ AI 幫我想 16 句",
        type="primary",
        use_container_width=True,
        key="v12_ai_copy_generate",
    )
    if _generate:
        if not _topic:
            st.warning("請先輸入一個主題，例如「職場的憤憤不平」。")
        else:
            _copy_session = None
            _copy_reservation = None
            _copy_api_key = ""
            _copy_session = _beta_current_user()
            if not _copy_session:
                st.warning("請先完成 Google 登入，才能使用會員 AI幫想額度。")
            else:
                _copy_api_key = _beta_site_openai_key()
                if not _copy_api_key:
                    st.error("❌ 站方 AI幫想服務尚未完成設定，請稍後再試。")
                else:
                    try:
                        _copy_reservation = _beta_reserve_copy_generation(_copy_session)
                    except Exception:
                        st.error("❌ 今日文案額度暫時無法確認，請稍後再試。")
                    else:
                        if not _copy_reservation["granted"]:
                            st.warning(
                                "今日 AI幫想額度已用完"
                                f"（每日 {_copy_reservation['daily_limit']} 次），"
                                "將在台灣時間午夜重置。"
                            )
                            _copy_api_key = ""

                        _beta_set_cached_copy_usage_status(
                            _copy_session,
                            {
                                "usage_date": _copy_reservation["usage_date"],
                                "daily_limit": _copy_reservation["daily_limit"],
                                "used_count": max(
                                    0,
                                    _copy_reservation["daily_limit"]
                                    - _copy_reservation["remaining_count"],
                                ),
                                "remaining_count": _copy_reservation["remaining_count"],
                                "entitlement_kind": _copy_reservation["entitlement_kind"],
                                "entitlement_expires_at": _copy_reservation[
                                    "entitlement_expires_at"
                                ],
                            },
                        )

            if _copy_api_key:
                try:
                    with st.spinner("🤖 AI 正在延伸主題與生活情境……"):
                        _phrases = _v12_ai_copy_generate(_topic, _tone, _copy_api_key)
                except RuntimeError as _e:
                    if _copy_session and _copy_reservation and _copy_reservation["job_id"]:
                        try:
                            _beta_finish_copy_generation(
                                _copy_session,
                                _copy_reservation["job_id"],
                                "copy_api_runtime_error",
                            )
                        except Exception:
                            pass
                    _beta_clear_cached_copy_usage_status()
                    st.error("❌ AI幫想服務目前無法使用。")
                    st.caption(f"錯誤資訊：{_e}")
                except Exception:
                    if _copy_session and _copy_reservation and _copy_reservation["job_id"]:
                        try:
                            _beta_finish_copy_generation(
                                _copy_session,
                                _copy_reservation["job_id"],
                                "copy_generation_failed",
                            )
                        except Exception:
                            pass
                    _beta_clear_cached_copy_usage_status()
                    st.error("❌ AI幫想產生失敗，請稍後再試。")
                else:
                    if _copy_session and _copy_reservation and _copy_reservation["job_id"]:
                        try:
                            _beta_finish_copy_generation(
                                _copy_session, _copy_reservation["job_id"]
                            )
                        except Exception:
                            st.warning("文案已生成，但今日額度狀態暫時無法更新。")
                    st.session_state["v12_ai_copy_candidates"] = _phrases
                    st.session_state["v12_ai_copy_topic_used"] = _topic
                    st.session_state["v12_ai_copy_tone_used"] = _tone
                    st.session_state["v12_ai_copy_selected"] = []
                    if _copy_reservation:
                        st.success(
                            "文案已生成；今日剩餘 "
                            f"{_copy_reservation['remaining_count']} / "
                            f"{_copy_reservation['daily_limit']} 次。"
                        )
                    for _i in range(16):
                        st.session_state[f"v12_ai_copy_pick_{_i}"] = False

    # ------------------------------------------------------------
    # ③ 本次 AI 生成結果：只勾選想保存的，不自動建立主題或全部進池。
    # ------------------------------------------------------------
    _candidates = list(st.session_state.get("v12_ai_copy_candidates") or [])
    if _candidates:
        _used_topic = str(st.session_state.get("v12_ai_copy_topic_used", "") or "").strip()
        _used_tone = st.session_state.get("v12_ai_copy_tone_used", _tone)
        st.markdown("#### 🆕 本次 AI 生成結果")
        st.caption(f"主題：{_used_topic}　｜　語氣：{_used_tone}　｜　請勾選想保存的文案")

        _gen_selected = st.session_state.get("v12_ai_copy_selected", [])
        if not isinstance(_gen_selected, list):
            _gen_selected = []
        _gen_selected = [
            int(i) for i in _gen_selected
            if isinstance(i, int) and 0 <= i < len(_candidates)
        ]

        # 「全選／全部取消」必須在 checkbox widget 建立前修改 widget state。
        # 使用 on_click callback，避免 Streamlit 在 widget 已建立後再次寫入同一個 key，
        # 造成 StreamlitWidgetAlreadyInstantiatedError。
        def _v12_ai_copy_select_all_cb():
            _indices = list(range(len(st.session_state.get("v12_ai_copy_candidates") or [])))
            st.session_state["v12_ai_copy_selected"] = _indices
            for _i in _indices:
                st.session_state[f"v12_ai_copy_pick_{_i}"] = True

        def _v12_ai_copy_clear_all_cb():
            _count = len(st.session_state.get("v12_ai_copy_candidates") or [])
            st.session_state["v12_ai_copy_selected"] = []
            for _i in range(_count):
                st.session_state[f"v12_ai_copy_pick_{_i}"] = False

        _cols = st.columns(4)
        for _idx, _phrase in enumerate(_candidates):
            with _cols[_idx % 4]:
                _pick_key = f"v12_ai_copy_pick_{_idx}"
                # 此處仍可安全初始化，因為 callback 已在本次 rerun 開始前執行。
                if _pick_key not in st.session_state:
                    st.session_state[_pick_key] = _idx in _gen_selected
                st.checkbox(
                    f"{_idx+1:02d}. {_phrase}",
                    key=_pick_key,
                )

        # 以 widget 的最新值重新整理目前選擇。
        _new_gen_selected = [
            _idx for _idx in range(len(_candidates))
            if bool(st.session_state.get(f"v12_ai_copy_pick_{_idx}", False))
        ]
        st.session_state["v12_ai_copy_selected"] = _new_gen_selected
        st.caption(f"本次準備保存：{len(_new_gen_selected)} / 16 句")

        _ga, _gb, _gc = st.columns(3)
        with _ga:
            st.button(
                "☑️ 全選",
                key="v12_ai_copy_select_all",
                use_container_width=True,
                on_click=_v12_ai_copy_select_all_cb,
            )
        with _gb:
            st.button(
                "↩️ 全部取消",
                key="v12_ai_copy_clear_all",
                use_container_width=True,
                on_click=_v12_ai_copy_clear_all_cb,
            )
        with _gc:
            if st.button(
                f"💾 儲存勾選文案到「{_used_topic}」主題",
                key="v12_ai_copy_save_pool",
                use_container_width=True,
            ):
                if not _used_topic:
                    st.warning("⚠️ 沒有可儲存的主題。請重新輸入主題並生成 16 句。")
                elif not _new_gen_selected:
                    st.warning("⚠️ 請至少勾選 1 句想保存的文案。")
                else:
                    _pools = st.session_state["v12_theme_pools"]
                    _pool = list(_pools.get(_used_topic, []))[:30]
                    _chosen_phrases = [_candidates[i] for i in _new_gen_selected]
                    _new_unique = [x for x in _chosen_phrases if x not in _pool]
                    _remaining = 30 - len(_pool)
                    if _remaining <= 0:
                        st.warning(f"⚠️「{_used_topic}」主題用語池已達上限 30 句。")
                    elif len(_new_unique) > _remaining:
                        st.warning(
                            f"⚠️「{_used_topic}」目前已有 {len(_pool)}/30 句，這次最多還能儲存 {_remaining} 句。\n"
                            f"目前勾選 {len(_new_unique)} 句，請取消 {len(_new_unique)-_remaining} 句後再儲存。"
                        )
                    elif not _new_unique:
                        st.info("ℹ️ 勾選的文案都已存在於這個主題用語池，不會重複儲存。")
                    else:
                        _pool.extend(_new_unique)
                        _pools[_used_topic] = _pool[:30]

                        # 只有在真正儲存文案後，才建立主題紀錄。
                        _selected = [
                            x for x in st.session_state.get("v12_selected_themes", [])
                            if x in _pools
                        ][:3]
                        if _used_topic not in _selected and len(_selected) < 3:
                            _selected.append(_used_topic)
                        st.session_state["v12_selected_themes"] = _selected[:3]
                        st.session_state["v12_ai_copy_selected"] = []
                        st.success(
                            f"✅ 已將 {len(_new_unique)} 句加入「{_used_topic}」主題用語池，"
                            f"目前 {len(_pools[_used_topic])}/30 句。"
                        )

    # ------------------------------------------------------------
    # ④ 主題用語池：只有真正儲存過文案的主題才會出現在這裡。
    #    每個主題可勾選「加入本次創作」，最多同時 3 個主題。
    # ------------------------------------------------------------
    st.markdown("#### 📚 主題用語池")
    _pool_themes = list(st.session_state["v12_theme_pools"].keys())
    if not _pool_themes:
        st.info("目前尚未建立主題用語池。先輸入主題、生成 16 句，再勾選想保存的文案即可。")
    else:
        # 清掉已不存在的選擇，並限制最多 3 個。
        st.session_state["v12_selected_themes"] = [
            x for x in st.session_state.get("v12_selected_themes", [])
            if x in _pool_themes
        ][:3]

        st.caption(
            f"選擇本次創作要參考的主題（最多 3 個）。目前已選："
            f"{len(st.session_state['v12_selected_themes'])} / 3"
        )

        for _theme_i, _theme in enumerate(_pool_themes):
            _pool = list(st.session_state["v12_theme_pools"].get(_theme, []))[:30]
            _theme_key = f"v12_theme_use_{_theme_i}"
            if _theme_key not in st.session_state:
                st.session_state[_theme_key] = _theme in st.session_state["v12_selected_themes"]

            _currently_selected = _theme in st.session_state["v12_selected_themes"]
            _can_select = _currently_selected or len(st.session_state["v12_selected_themes"]) < 3
            _use_theme = st.checkbox(
                f"🎯 加入本次創作：{_theme}（{len(_pool)}/30）",
                key=_theme_key,
                disabled=not _can_select,
            )

            if _use_theme and _theme not in st.session_state["v12_selected_themes"]:
                if len(st.session_state["v12_selected_themes"]) < 3:
                    st.session_state["v12_selected_themes"].append(_theme)
                else:
                    st.session_state[_theme_key] = False
            elif not _use_theme and _theme in st.session_state["v12_selected_themes"]:
                st.session_state["v12_selected_themes"].remove(_theme)

            with st.expander(f"📚 {_theme}｜{len(_pool)}/30 句", expanded=False):
                if not _pool:
                    st.caption("目前沒有保存的文案。")
                else:
                    st.caption("勾選要刪除的句子；刪除前會再次確認。")
                    _delete_refs = []
                    _pcols = st.columns(2)
                    for _pi, _phrase in enumerate(_pool):
                        with _pcols[_pi % 2]:
                            if st.checkbox(
                                f"{_pi+1:02d}. {_phrase}",
                                key=f"v12_theme_pool_del_{_theme_i}_{_pi}",
                            ):
                                _delete_refs.append(_pi)
                    if _delete_refs:
                        st.warning(f"已選擇刪除 {len(_delete_refs)} 句。")
                        if st.button(
                            f"🗑️ 刪除勾選的 {len(_delete_refs)} 句",
                            key=f"v12_theme_pool_delete_{_theme_i}",
                            use_container_width=True,
                        ):
                            _remaining_pool = [
                                x for i, x in enumerate(_pool) if i not in _delete_refs
                            ]
                            st.session_state["v12_theme_pools"][_theme] = _remaining_pool
                            st.success(f"✅ 已刪除 {len(_delete_refs)} 句。")
                            st.rerun()

        st.caption(
            f"🎯 本次創作已選擇：{len(st.session_state['v12_selected_themes'])} / 3 個主題"
        )

    # ------------------------------------------------------------
    # ⑤ 從本次已選的最多 3 個主題中挑最終 8 句。
    # ------------------------------------------------------------
    st.markdown("#### 🎯 從已選主題用語池選擇 8 句")
    _active_selected_themes = [
        x for x in st.session_state.get("v12_selected_themes", [])
        if x in st.session_state["v12_theme_pools"]
    ][:3]
    if not _active_selected_themes:
        st.info("請先在上面的主題用語池勾選本次創作要使用的主題。")
    else:
        _final_refs = st.session_state.get("v12_theme_final_selected", [])
        if not isinstance(_final_refs, list):
            _final_refs = []
        _current_refs = []
        _final_new = []
        for _si, _theme in enumerate(_active_selected_themes):
            _pool = list(st.session_state["v12_theme_pools"].get(_theme, []))[:30]
            with st.expander(f"{_theme}｜{len(_pool)}/30 句", expanded=True):
                for _pi, _phrase in enumerate(_pool):
                    _ref = f"{_theme}\x1f{_pi}"
                    _current_refs.append(_ref)
                    _checked = _ref in _final_refs
                    if st.checkbox(
                        _phrase,
                        value=_checked,
                        key=f"v12_theme_final_{_si}_{_pi}",
                    ):
                        _final_new.append(_ref)
        st.session_state["v12_theme_final_selected"] = _final_new
        st.caption(f"目前已選 {_final_new.__len__()} / 8 句")
        if len(_final_new) > 8:
            st.warning("⚠️ 最多只能選 8 句，請取消多出的選項。")
        if st.button(
            "🎯 套用所選 8 句到 01～08",
            key="v12_ai_copy_apply_pool",
            use_container_width=True,
        ):
            if len(_final_new) != 8:
                st.warning("請從已選主題用語池中剛好選擇 8 句，再套用到 01～08。")
            else:
                _phrase_map = {}
                for _theme in _active_selected_themes:
                    for _pi, _phrase in enumerate(
                        st.session_state["v12_theme_pools"].get(_theme, [])[:30]
                    ):
                        _phrase_map[f"{_theme}\x1f{_pi}"] = _phrase
                _final_phrases = [
                    _phrase_map[_ref] for _ref in _final_new if _ref in _phrase_map
                ]
                if len(_final_phrases) == 8:
                    set_texts(_final_phrases)
                    st.session_state["v12_ai_copy_last_applied"] = True
                    # 01～08 是另一個局部區塊；套用完成時才需要更新整頁。
                    st.rerun()

    if st.session_state.get("v12_ai_copy_last_applied", False):
        st.caption("💡 已套用完成；你仍然可以手動修改 01～08 的任何一句。")
        st.session_state["v12_ai_copy_last_applied"] = False

def build_prompt(style, custom_style, selected_character, custom_character,
                 texts, transparent, color_segments=None, ai_copy_topic="", ai_copy_tone="", ai_copy_topics=None,
                 sticker_mode="text", expression_specs=None, action_prompts=None):
    p = [
        "請以我提供的人物照片作為主要人物參考。",
        "保留人物身份辨識特徵，不任意改變人物核心外觀。",
        "請一次生成一張清楚的4×2八格LINE貼圖總圖。",
        "第一排四格、第二排四格；依使用者輸入順序由左至右、由上至下對應。",
        "八格人物維持同一人物身份與主要視覺風格。",
        "每一格都是獨立貼圖，請讓人物、手勢、文字、道具與特效適合各自裁切。",
        "人物與整體動作可維持誇張、自然、有張力；不必為了留白而犧牲拳頭、手勢或情緒表現。",
        "相較於一般滿版貼圖，請將每格的人物與特效整體自然縮小約 10～15%，置中安排，讓四周保留約一成可供裁切的自然空白。",
        "遇到大動作時，請優先整體稍微縮小並保留完整姿勢，而不是截斷手腳、壓縮人物或讓動作變得僵硬。",
        "相鄰貼圖之間請自然保留乾淨的背景色或透明空間，避免相鄰人物彼此貼太近；不要畫格線、框線或分隔線。",
        "不要拉伸、變形或壓縮人物。",
        "非常重要：圖片中禁止出現01、02、03、04、05、06、07、08等編號。",
        "禁止加入格號、序號、位置標籤或數字標記。",
    ]
    sticker_mode = "expression" if sticker_mode == "expression" else "text"
    if sticker_mode == "expression":
        p.extend([
            "本次為「使用表情貼圖（無文字）」模式。",
            "八格都禁止出現任何漢字、英文字母、數字、標點、對話框文字或標籤。",
            "每格優先呈現人物臉部與肩膀以上的清楚特寫；眼神、眉毛、嘴型與姿態要能一眼辨識情緒。",
            "可以有少量手勢輔助情緒，但不要讓身體或背景搶走表情焦點。",
            "八格的主情緒、手勢、臉部表情與視覺特效必須明顯不同；禁止重複同一種比讚、捧臉、微笑、哭泣或驚訝構圖。",
        ])
    else:
        p.extend([
            "每格人物完整呈現，不要裁切頭部、臉部、身體或四肢。",
            "只有使用者指定的貼圖文字可以出現在圖片中。",
        ])
    if style != "↓ 請選擇風格":
        p.append(f"主要貼圖風格：{style}。")
    if custom_style.strip():
        p.append(f"使用者自定風格：{custom_style.strip()}。")
    if selected_character:
        p.append("人物與畫面特色：" + "、".join(selected_character) + "。")
    if custom_character.strip():
        p.append(f"使用者自定人物／場景要求：{custom_character.strip()}。")
    _topic_list = [str(x).strip() for x in (ai_copy_topics or []) if str(x).strip()]
    if not _topic_list and str(ai_copy_topic or "").strip():
        _topic_list = [str(ai_copy_topic).strip()]
    if _topic_list:
        p.append("AI 貼圖創作主題（本次最多三個主題共同參考）：" + "、".join(dict.fromkeys(_topic_list)) + "。")
        p.append("本次貼圖可同時採用上述多個主題；它們不是互相衝突的不同世界，而是同一套貼圖的不同主題切面。請依各格文字情境自然參考相關主題，並維持整組人物與視覺一致性。")
    if str(ai_copy_tone or "").strip():
        p.append(f"AI 貼圖文案語氣方向：{str(ai_copy_tone).strip()}。")
    if sticker_mode == "expression":
        for i, spec in enumerate(_v12_normalize_expression_specs(expression_specs)):
            emotion = (
                "由 AI 隨機安排一種強烈、清楚且容易辨識的情緒反應"
                if spec["emotion_random"]
                else spec["emotion"] or "自然、清楚且容易辨識的情緒反應"
            )
            p.append(
                f"第{i+1}格：表情／情緒為「{emotion}」；"
                f"視覺特效為「{spec['effect']}」。"
            )
            if spec["effect_action_prompt"]:
                p.append(
                    f"第{i+1}格的特效／動作補充方向："
                    f"「{spec['effect_action_prompt']}」。"
                )
    else:
        for i, t in enumerate(texts):
            p.append(f"第{i+1}格的指定貼圖文字為：「{t.strip() or '（此格未指定文字）'}」。")
        for i, action_prompt in enumerate(_v12_normalize_sticker_action_prompts(action_prompts)):
            if action_prompt:
                p.append(
                    f"第{i+1}格需搭配文字情境呈現以下人物動作／互動：「{action_prompt}」。"
                    "請讓動作清楚、有張力，且完整保留在該格可裁切範圍內。"
                )

    color_segments = [] if sticker_mode == "expression" else (color_segments or [])
    for item in color_segments:
        p.append(
            f"第{item['sticker_index']}格的文字片段「{item['text']}」"
            f"必須使用{item['color_name']}（{item['hex']}）。"
        )
    if color_segments:
        p.append(
            "局部文字上色規則：只對上述指定文字片段套用指定顏色；"
            "其他文字維持一般文字顏色。請不要自行增加其他彩色文字。"
        )
    if transparent:
        p.append("請使用透明背景PNG，背景保持真正透明，不要以白色或黑色填滿。")
    p.append("整體具有LINE貼圖的清楚、可讀、可愛與完整構圖感。")
    return "\n".join(p)


def _v12_set_style_reference_from_upload(uploaded_file):
    """Normalize a style-only reference to a compact image-edit input."""
    raw = uploaded_file.getvalue()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if fingerprint == st.session_state.get("v12_style_reference_fingerprint"):
        return

    image = Image.open(BytesIO(raw))
    image.load()
    image = image.convert("RGB")
    resampling = getattr(Image, "Resampling", Image)
    image.thumbnail((1600, 1600), resampling.LANCZOS)
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=90, optimize=True)

    st.session_state["v12_style_reference_image_bytes"] = buffer.getvalue()
    st.session_state["v12_style_reference_filename"] = (
        str(getattr(uploaded_file, "name", "") or "參考畫風圖片")[:120]
    )
    st.session_state["v12_style_reference_fingerprint"] = fingerprint


def _v12_style_reference_upload_changed():
    """Keep the active reference in sync with its upload control."""
    if st.session_state.get("v12_style_reference_upload") is not None:
        st.session_state["v12_style_source_mode"] = V12_STYLE_REFERENCE_MODE
        st.session_state["v10_style_mode"] = "↓ 請選擇風格"
        st.session_state.pop("v12_loaded_project_custom_style", None)
    else:
        # This callback is only invoked when the user clears the uploader.
        st.session_state["v12_style_reference_image_bytes"] = None
        st.session_state["v12_style_reference_filename"] = ""
        st.session_state["v12_style_reference_fingerprint"] = ""


def _v12_render_pricing_page():
    """Render the public-ready plan selection screen before ECPay checkout goes live."""
    products = [
        {
            "code": "basic_monthly",
            "name": "基本方案",
            "price": "NT$99",
            "period": "每月訂閱",
            "credits": "圖片生成 20 次／月",
            "copy": "AI幫想每日 15 次",
            "library": "雲端作品庫 20 組",
            "button": "選擇基本方案",
        },
        {
            "code": "standard_monthly",
            "name": "標準方案",
            "price": "NT$149",
            "period": "每月訂閱",
            "credits": "圖片生成 30 次／月",
            "copy": "AI幫想每日 20 次",
            "library": "雲端作品庫 30 組",
            "button": "選擇標準方案",
            "recommended": True,
        },
        {
            "code": "advanced_monthly",
            "name": "進階方案",
            "price": "NT$199",
            "period": "每月訂閱",
            "credits": "圖片生成 40 次／月",
            "copy": "AI幫想每日 25 次",
            "library": "雲端作品庫 40 組",
            "button": "選擇進階方案",
        },
        {
            "code": "credit_pack_7",
            "name": "7 次生成包",
            "price": "NT$49",
            "period": "單次購買・30 天效期",
            "credits": "圖片生成 7 次",
            "copy": "AI幫想每日 7 次",
            "library": "雲端作品庫 7 組",
            "button": "選擇生成包",
        },
    ]
    selected_code = str(st.session_state.get("v12_pending_purchase_product", "") or "")
    session = _beta_current_user() if _beta_config() else None
    current_code = ""
    current_label = ""
    if session:
        try:
            entitlement = _beta_effective_entitlement(session)
            current_code = str(entitlement.get("product_code", "") or "")
            current_label = _beta_entitlement_label(entitlement)
        except Exception:
            pass

    st.markdown(
        """
        <div class="v12-pricing-hero">
          <div class="v12-pricing-kicker">STICKER STUDIO NEXT</div>
          <h1>💳 購買方案</h1>
          <p>選擇適合你的創作額度。圖片生成次數、AI幫想額度與雲端作品庫會依方案立即提供。</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if current_label:
        st.success(f"📋 {current_label}")
    else:
        st.info("登入後即可選擇方案；付款完成後會自動更新圖片生成次數與會員權益。")

    plan_cols = st.columns(4, gap="medium")
    for col, product in zip(plan_cols, products):
        is_selected = selected_code == product["code"]
        is_current = current_code == product["code"]
        card_class = " v12-pricing-card-featured" if product.get("recommended") else ""
        with col:
            badge = '<div class="v12-pricing-badge">最受歡迎</div>' if product.get("recommended") else ""
            state = '<div class="v12-pricing-current">目前方案</div>' if is_current else ""
            st.markdown(
                f"""
                <div class="v12-pricing-card{card_class}">
                  {badge}
                  {state}
                  <div class="v12-pricing-plan-name">{product['name']}</div>
                  <div class="v12-pricing-price">{product['price']}<span>{product['period']}</span></div>
                  <div class="v12-pricing-benefit">🖼️ {product['credits']}</div>
                  <div class="v12-pricing-benefit">✨ {product['copy']}</div>
                  <div class="v12-pricing-benefit">☁️ {product['library']}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            if st.button(
                "✓ 已選擇" if is_selected else product["button"],
                key=f"v12_select_product_{product['code']}",
                type="primary" if product.get("recommended") else "secondary",
                use_container_width=True,
                disabled=is_current,
            ):
                st.session_state["v12_pending_purchase_product"] = product["code"]
                st.rerun()

    chosen = next((item for item in products if item["code"] == selected_code), None)
    if chosen:
        st.markdown("<div class='v12-pricing-selection'>", unsafe_allow_html=True)
        st.markdown(f"### 已選擇：{chosen['name']}｜{chosen['price']}")
        if session:
            st.info("綠界付款串接審核中。付款功能開通後，這裡會直接進入安全結帳頁面；目前不會扣款。")
            st.button(
                "⏳ 綠界付款審核中",
                key="v12_ecpay_checkout_pending",
                type="primary",
                use_container_width=True,
                disabled=True,
            )
        else:
            st.warning("請先登入 Google 帳號，付款開通後才能將方案權益正確加入你的帳戶。")
            if st.button("✨ 前往登入", key="v12_pricing_go_login", type="primary", use_container_width=True):
                st.session_state["v12_view"] = "create"
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("### 📌 方案使用說明")
    with st.expander("查看訂閱、生成次數與雲端作品庫規則", expanded=True):
        st.markdown(
            """
            - 每次成功生成 1 張 **4×2 原始貼圖總圖**，扣除 1 次圖片生成次數；生成失敗不扣次數。
            - AI幫想額度按台灣時間每日計算，與圖片生成次數分開計算。
            - 訂閱方案每月自付款成功日生效；未使用的訂閱生成次數可保留至下一個帳務週期結束，系統優先使用最早到期的次數。

            > **例子：**你在 5 月 10 日訂閱基本方案，取得 20 次圖片生成次數；到了 6 月 9 日還剩 5 次。6 月 10 日進入新一期後會再加入 20 次，因此共有 25 次可用；原本保留的 5 次最晚可用到 7 月 9 日，系統會優先使用這 5 次。

            - 單次生成包為一次性購買，不會自動續訂，購買後有效期為 30 天。
            - 取消訂閱後，當期權益可使用至帳務週期結束，下一期不再續扣。作品集保存總數超過新方案上限時，不會自動刪除，須自行刪減後才能新增保存。

            > **例子：**你的訂閱週期是 5 月 10 日至 6 月 9 日；若在 5 月 20 日取消，仍可使用當期的圖片生成次數、AI幫想額度與作品庫權益至 6 月 9 日。6 月 10 日起不會再扣款，也不會發放新一期的方案額度。

            - 每組作品最多保存 40 張已選貼圖；MAIN／TAB 規格圖不計入上限。ZIP 檔不會保存，可載回創作頁後重新下載。
            """
        )
    st.caption("服務提供 AI 輔助貼圖創作、單張／ZIP 下載與私人雲端作品保存。正式付款頁將揭示付款、續訂、取消與退款說明。")


# ------------------------------------------------------------
# UI
# ------------------------------------------------------------
st.markdown("""<style>
.v10-rainbow-title{padding:8px 14px;border-radius:12px;margin:8px 0 12px;font-weight:700;background:linear-gradient(90deg,#fff1f2,#fff7ed,#fefce8,#f0fdf4,#eff6ff,#f5f3ff);}
.v10-soft-box{padding:8px 12px;border-radius:10px;background:#fafafa;border:1px solid #e5e7eb;}

</style>""",unsafe_allow_html=True)


# ============================================================
# V12｜STEP 01A①
# 主導航：開始製作 / 我的作品
# ============================================================
st.markdown(
    """
    <style>
    .v12-nav-wrap{
      width:min(920px,100%);
      margin:0 auto 1.1rem;
      padding:.45rem;
      border:1px solid rgba(120,120,120,.16);
      border-radius:18px;
      background:color-mix(in srgb,#64748b 6%, transparent);
    }

    /* V12｜01B① UI 微調：主導航與 LINE 連結按鈕使用同一套紅色視覺。 */
    .st-key-v12_nav_create button,
    .st-key-v12_nav_pricing button,
    .st-key-v12_nav_library button,
    .st-key-v12_footer_library button{
      width:100% !important;
      min-height:52px !important;
      border-radius:10px !important;
      font-size:18px !important;
      font-weight:800 !important;
      background:#FF4B4B !important;
      background-color:#FF4B4B !important;
      color:#FFFFFF !important;
      border:1px solid #FF4B4B !important;
      box-shadow:none !important;
    }
    .st-key-v12_nav_create button:hover,
    .st-key-v12_nav_pricing button:hover,
    .st-key-v12_nav_library button:hover,
    .st-key-v12_footer_library button:hover{
      background:#FF3333 !important;
      background-color:#FF3333 !important;
      border-color:#FF3333 !important;
      color:#FFFFFF !important;
    }
    .st-key-v12_nav_create button p,
    .st-key-v12_nav_pricing button p,
    .st-key-v12_nav_library button p,
    .st-key-v12_footer_library button p{
      color:#FFFFFF !important;
      font-weight:800 !important;
    }
    </style>
    <div class="v12-nav-wrap"></div>
    """,
    unsafe_allow_html=True,
)

_v12_nav_cols = st.columns(3)
with _v12_nav_cols[0]:
    # V12 微調：創作頁上方改為「聯繫作者」；其他頁面保留「開始製作」。
    _v12_current_view = st.session_state.get("v12_view", "create")
    if _v12_current_view in {"library", "pricing"}:
        if st.button("✨ 開始製作", key="v12_nav_create", use_container_width=True):
            st.session_state["v12_view"] = "create"
            st.rerun()
    else:
        st.link_button(
            "💬 聯繫作者",
            "https://www.threads.com/@tuwillson",
            use_container_width=True,
        )
with _v12_nav_cols[1]:
    if st.button("💳 購買方案", key="v12_nav_pricing", use_container_width=True):
        _public02a_sync_style_bank_from_widgets()
        _save_v10_presets()
        st.session_state["v12_view"] = "pricing"
        st.rerun()
with _v12_nav_cols[2]:
    if st.button("📚 我的作品", key="v12_nav_library", use_container_width=True):
        # FIX11: leave-create navigation explicitly commits the dedicated style bank
        # before conditional editor widgets disappear.
        _public02a_sync_style_bank_from_widgets()
        _save_v10_presets()
        st.session_state["v12_view"] = "library"
        st.rerun()

st.session_state.setdefault("v12_view", "create")

if st.session_state["v12_view"] == "pricing":
    _v12_render_pricing_page()
    st.stop()

if st.session_state["v12_view"] == "library":
    st.markdown('<div class="v10-main-title">📚 我的作品</div>', unsafe_allow_html=True)
    st.caption("打包時勾選雲端保存的會員作品會保存在私人雲端作品庫；本機暫存只在這次瀏覽期間保留。")

    _cloud_session = _beta_current_user() if _beta_config() else None
    if _cloud_session:
        try:
            _cloud_status = _beta_library_status(_cloud_session)
            st.info(
                f"☁️ 雲端作品庫：已保存 {_cloud_status['saved_count']}／"
                f"{_cloud_status['library_capacity']} 組作品"
            )
            st.caption("作品集上限會依目前會員權益套用；訂閱與單次生成包容量不會累計。")
            _cloud_projects = _beta_list_library_projects(_cloud_session)
        except Exception:
            _cloud_projects = []
            st.warning("雲端作品庫暫時無法讀取；本機暫存不受影響。")

        if _cloud_projects:
            st.markdown("### ☁️ 雲端作品")
            for _cloud_project in _cloud_projects:
                if not isinstance(_cloud_project, dict) or not _cloud_project.get("id"):
                    continue
                _cloud_id = str(_cloud_project["id"])
                _cloud_meta = _cloud_project.get("metadata")
                if not isinstance(_cloud_meta, dict):
                    _cloud_meta = {}
                _cloud_title = str(_cloud_project.get("title") or _cloud_meta.get("project_name") or "未命名作品")
                _cloud_left, _cloud_right = st.columns([1, 2.4], vertical_alignment="top")
                with _cloud_left:
                    try:
                        st.image(
                            _beta_storage_download(_cloud_session, _cloud_project.get("preview_path", "")),
                            caption="雲端預覽圖",
                            use_container_width=True,
                        )
                    except Exception:
                        st.info("🖼️ 預覽圖暫時無法載入")
                with _cloud_right:
                    st.markdown(f"### 🏷️ {html.escape(_cloud_title)}")
                    st.caption(f"雲端保存：{_v12_format_display_time(_cloud_project.get('created_at', ''))}")
                    st.caption("雲端只保存已選成品與設定，不保存 ZIP 或 4×2 原圖；如需下載，請先載回創作頁後重新打包。")
                    _cloud_act1, _cloud_act2 = st.columns(2)
                    with _cloud_act1:
                        if st.button("↩️ 載回創作頁", key=f"cloud_load_{_cloud_id}", use_container_width=True):
                            try:
                                _cloud_record = _beta_load_library_project_record(
                                    _cloud_session, _cloud_project
                                )
                                _v12_load_history_record(_cloud_record)
                                st.session_state["v12_view"] = "create"
                                st.rerun()
                            except Exception:
                                st.error("作品暫時無法載回，請稍後再試。")
                    with _cloud_act2:
                        _delete_ack = st.checkbox(
                            "確認永久刪除",
                            key=f"cloud_delete_ack_{_cloud_id}",
                        )
                        if st.button("🗑️ 刪除雲端作品", key=f"cloud_delete_{_cloud_id}", use_container_width=True):
                            if not _delete_ack:
                                st.warning("請先勾選確認永久刪除。")
                            else:
                                try:
                                    _beta_delete_library_project(_cloud_session, _cloud_id)
                                    st.rerun()
                                except Exception:
                                    st.error("作品尚未刪除，請稍後再試。")
                st.divider()
        else:
            st.caption("☁️ 雲端作品庫尚未有作品。打包時勾選「同步保存」即可保存；空間滿時請先下載並刪除一組。")
    else:
        st.info("登入會員後，打包時可選擇保存到私人雲端作品庫。")

    _projects_all = list(st.session_state.get("v12_projects_local_index", []))
    _top_a, _top_b = st.columns([4, 1])
    with _top_a:
        _search_name = st.text_input(
            "🔎 搜尋作品名稱",
            placeholder="輸入作品名稱搜尋…",
            key="v12_project_search",
        )
    with _top_b:
        st.metric("暫存作品", len(_projects_all))

    _projects = list(_projects_all)
    if _search_name.strip():
        needle = _search_name.strip().lower()
        _projects = [p for p in _projects if needle in str(p.get("project_name", "")).lower()]

    if not _projects:
        st.info("📭 目前還沒有保存的作品")
        st.caption("完成「裁切＋製作貼圖檔案＋一鍵打包」後，作品會自動出現在這裡。")
    else:
        for _idx, _project in enumerate(_projects):
            _pid = str(_project.get("project_id", _idx))
            _name = str(_project.get("project_name") or _v12_default_project_name())

            _card_left, _card_right = st.columns([1, 2.4], vertical_alignment="top")

            with _card_left:
                if _project.get("main_bytes"):
                    st.image(_project["main_bytes"], caption=f"⭐ MAIN {_project.get('main_no') or '—'}", use_container_width=True)
                elif _project.get("grid_bytes"):
                    st.image(_project["grid_bytes"], caption="4×2 原始總圖", use_container_width=True)
                else:
                    st.info("🖼️ 尚無預覽圖")

            with _card_right:
                st.markdown(f"### 🏷️ {_name}")

                _meta_parts = []
                if _project.get("style_name"):
                    _meta_parts.append(f"🎨 {_project['style_name']}")
                if _project.get("font_name"):
                    _meta_parts.append(f"🔤 {_project['font_name']}")
                if _project.get("transparent_background"):
                    _meta_parts.append("🖼️ 透明背景 PNG")
                if _project.get("main_no"):
                    _meta_parts.append(f"⭐ MAIN={_project['main_no']}")
                if _project.get("tab_no"):
                    _meta_parts.append(f"🏷️ TAB={_project['tab_no']}")
                if _meta_parts:
                    st.caption("　｜　".join(_meta_parts))

                st.caption(f"最後打包：{_v12_format_display_time(_project.get('updated_at', ''))}")

                _act1, _act2, _act3 = st.columns(3)
                with _act1:
                    if _project.get("zip_bytes"):
                        st.download_button(
                            "⬇️ 重新下載 ZIP",
                            data=_project["zip_bytes"],
                            file_name=f"{_v12_safe_download_stem(_name)}.zip",
                            mime="application/zip",
                            use_container_width=True,
                            key=f"v12_redownload_{_pid}",
                        )
                with _act2:
                    if st.button("↩️ 載回創作頁", key=f"v12_load_{_pid}", use_container_width=True):
                        _v12_load_history_record(_project)
                        st.session_state["v12_view"] = "create"
                        st.rerun()
                with _act3:
                    if st.button("🗑️ 刪除", key=f"v12_delete_{_pid}", use_container_width=True):
                        _v12_delete_history_record(_pid)
                        st.rerun()

                with st.expander("🔍 查看這組作品詳細設定", expanded=False):
                    st.write(f"**作品名稱：** {_name}")
                    st.write(f"**生成方式：** {_project.get('generation_method') or '未記錄'}")
                    st.write(f"**文字風格：** {_project.get('text_style') or '未記錄'}")
                    st.write(f"**建立時間：** {_project.get('created_at') or '未記錄'}")

                    _texts = list(_project.get("sticker_texts") or [])
                    if any(str(x).strip() for x in _texts):
                        st.markdown("**8 張貼圖文字：**")
                        for _n, _txt in enumerate(_texts, start=1):
                            st.write(f"{_n:02d}. {_txt or '（未設定）'}")

                    if _project.get("grid_bytes"):
                        st.markdown("**4×2 原始總圖：**")
                        st.image(_project["grid_bytes"], use_container_width=True)

            st.divider()

    st.caption(f"本機暫存最多 {V12_HISTORY_LIMIT} 組；不保存 API Key，也不寫入網站每日 AI 額度資料。")
    st.stop()


st.markdown('<div id="v12-page-top"></div>', unsafe_allow_html=True)
st.markdown('<div class="v10-main-title">🎨 LINE 貼圖創作室</div>', unsafe_allow_html=True)

# OAuth redirects arrive with a short-lived code in the URL.  Handle it before
# creating the rest of the widgets so a successful login returns to a clean app.
if _beta_config():
    _beta_complete_oauth_callback()


# API 使用模式常數必須在所有 callback / widget 使用前先建立。
# Streamlit 會由上而下執行整個 script，因此不可放在 AI 文案區之後。
_ALLOWED_API_MODES = [BETA_API_MODE]

def _v12_sync_api_mode():
    """維持會員模式的 canonical Session state。"""
    st.session_state["v12_saved_api_mode"] = BETA_API_MODE
    st.session_state["v11_api_mode"] = BETA_API_MODE


# 會員生成統一透過站方 API 與點數帳本；不提供使用者 API Key 輸入。
_api_mode = BETA_API_MODE
st.session_state["v12_saved_api_mode"] = BETA_API_MODE
st.session_state["v11_api_mode"] = BETA_API_MODE

# 清除舊版瀏覽器 Session 欄位，確保後續流程只使用會員模式。
for _deprecated_key_state in (
    "v11_user_api_key",
    "v12_saved_user_api_key",
    "v11_show_api_key",
    "v12_api_mode_widget",
):
    st.session_state.pop(_deprecated_key_state, None)

_beta_session = _beta_render_account_panel()
_beta_render_admin_console(_beta_session)

if not _beta_session:
    st.warning("⚠️ 請先使用 Google 登入，才能使用會員 Beta 額度。")


st.markdown("""
<div class="v12-journey">
  <div class="v12-journey-heading"><span class="v12-journey-icon">🎯</span>照著創作路徑完成一組貼圖</div>
  <div class="v12-journey-steps">
    <div class="v12-journey-step" style="--journey-accent:#ff6b86"><span class="v12-journey-number">01</span><span class="v12-journey-label">人物照片</span></div>
    <div class="v12-journey-step" style="--journey-accent:#ffad54"><span class="v12-journey-number">02</span><span class="v12-journey-label">貼圖風格</span></div>
    <div class="v12-journey-step" style="--journey-accent:#53d68b"><span class="v12-journey-number">03</span><span class="v12-journey-label">人物特色</span></div>
    <div class="v12-journey-step" style="--journey-accent:#55b8ff"><span class="v12-journey-number">04</span><span class="v12-journey-label">貼圖內容</span></div>
    <div class="v12-journey-step" style="--journey-accent:#a78bfa"><span class="v12-journey-number">05</span><span class="v12-journey-label">背景設定</span></div>
    <div class="v12-journey-step" style="--journey-accent:#ffad54"><span class="v12-journey-number">06</span><span class="v12-journey-label">開始生成</span></div>
    <div class="v12-journey-step" style="--journey-accent:#5ad7d1"><span class="v12-journey-number">07</span><span class="v12-journey-label">確認成果</span></div>
    <div class="v12-journey-step" style="--journey-accent:#ff748f"><span class="v12-journey-number">08</span><span class="v12-journey-label">裁切下載</span></div>
  </div>
</div>
""", unsafe_allow_html=True)
st.markdown('<div class="v11-section-tip">💡 <b>小提醒：</b>依照 01～08 往下設定即可；只有選擇的模式會展開，不需要先閱讀全部功能。</div>', unsafe_allow_html=True)
st.divider()

v10_section("📷 ① 上傳人物照片", "#ff5c7a")
st.caption("📌 先上傳一張清楚的人物照片，後續所有貼圖都會以這張照片作為主要參考。")
with st.container(key="v12_person_photo_upload"):
    uploaded = st.file_uploader("選擇人物照片", type=["jpg","jpeg","png","webp"])
if uploaded:
    try:
        uploaded.seek(0)
        im = Image.open(uploaded)
        im.load()
        b = BytesIO()
        im.convert("RGBA").save(b, "PNG")
        st.session_state.uploaded_image_bytes = b.getvalue()
        _img_c1,_img_c2,_img_c3=st.columns([1,2,1])
        with _img_c2:
            st.image(im, caption="人物照片", width=420)
        st.success("✅ 人物照片已載入")
    except Exception as e:
        st.error("❌ 無法讀取人物照片")
        st.code(str(e))
elif st.session_state.get("uploaded_image_bytes"):
    # 載回作品時，瀏覽器安全機制不允許替 file_uploader 回填原始檔名。
    # 但已保存的照片仍存在 Session，必須明確顯示，避免誤以為人物照遺失。
    try:
        restored_photo = Image.open(
            BytesIO(st.session_state["uploaded_image_bytes"])
        )
        restored_photo.load()
        _img_c1, _img_c2, _img_c3 = st.columns([1, 2, 1])
        with _img_c2:
            st.image(restored_photo, caption="已載回的人物照片", width=420)
        st.info("📦 已載回已保存的人物照片；雖然上傳欄不會顯示原始檔名，仍可直接繼續生成。")
    except Exception:
        st.warning("已載回人物照片資料，但預覽暫時無法顯示；可重新上傳同一張照片後繼續。")

st.divider()
V8_STYLE_CUSTOM_OPTION = "⭐ 自定義風格"

v10_section("🎨 ② 貼圖風格", "#ff9f43")
st.caption("先選擇一種風格設定方式，再展開對應的設定內容；兩種方式不會同時套用。")
st.session_state.setdefault("v12_style_source_mode", V12_STYLE_PRESET_MODE)

def _v12_set_style_source_mode(mode):
    st.session_state["v12_style_source_mode"] = mode
    if mode == V12_STYLE_REFERENCE_MODE:
        st.session_state["v10_style_mode"] = "↓ 請選擇風格"
        st.session_state.pop("v12_loaded_project_custom_style", None)


_style_source_mode = str(st.session_state.get(
    "v12_style_source_mode", V12_STYLE_PRESET_MODE
))
if _style_source_mode not in {V12_STYLE_PRESET_MODE, V12_STYLE_REFERENCE_MODE}:
    _style_source_mode = V12_STYLE_PRESET_MODE
    st.session_state["v12_style_source_mode"] = _style_source_mode

_style_preset_col, _style_reference_col = st.columns(2)
with _style_preset_col:
    st.button(
        ("✓ " if _style_source_mode == V12_STYLE_PRESET_MODE else "")
        + V12_STYLE_PRESET_MODE,
        key="v12_select_style_preset_mode",
        use_container_width=True,
        type="primary" if _style_source_mode == V12_STYLE_PRESET_MODE else "secondary",
        on_click=_v12_set_style_source_mode,
        args=(V12_STYLE_PRESET_MODE,),
    )
with _style_reference_col:
    st.button(
        ("✓ " if _style_source_mode == V12_STYLE_REFERENCE_MODE else "")
        + V12_STYLE_REFERENCE_MODE,
        key="v12_select_style_reference_mode",
        use_container_width=True,
        type="primary" if _style_source_mode == V12_STYLE_REFERENCE_MODE else "secondary",
        on_click=_v12_set_style_source_mode,
        args=(V12_STYLE_REFERENCE_MODE,),
    )

# 自定義風格放在第 2 個位置，方便快速選擇。
_v8_style_items = [x for x in V8_STYLES if not x.startswith("⭐ 自訂")]
_style_options = ["↓ 請選擇風格", "⭐ 自定義風格"] + _v8_style_items
style_mode = str(st.session_state.get("v10_style_mode", "↓ 請選擇風格"))
style = style_mode
custom_style = ""
_style_reference_active = False

if _style_source_mode == V12_STYLE_PRESET_MODE:
    st.caption("🌈 選擇預設風格，或改用你已保存的自定義風格。")
    style_mode = st.selectbox(
        "🌈 貼圖風格",
        _style_options,
        key="v10_style_mode",
    )

    if style_mode == "⭐ 自定義風格":
        style = "↓ 請選擇風格"
        st.success("✨ 已切換到「自定義風格」模式")
        _style_bank = _public02a_get_style_bank()
        _custom_style_slots = [
            (_i, str(_style_bank[str(_i)]["name"]).strip() or f"使用者自定{_i}",
             str(_style_bank[str(_i)]["prompt"]).strip())
            for _i in range(1, 11)
            if str(_style_bank[str(_i)]["prompt"]).strip()
        ]
        _saved_labels = ["↓ 請選擇已儲存風格"] + [
            f"{i:02d}｜{name}" for i, name, _ in _custom_style_slots
        ]
        _pending_saved_style_choice = st.session_state.pop(
            "v12_pending_saved_custom_style_choice", ""
        )
        _show_saved_style_notice = bool(
            st.session_state.pop("v12_show_saved_style_notice", False)
        )
        if _pending_saved_style_choice in _saved_labels:
            # 必須在 selectbox 建立前更新，才能安全地同步新名稱與選取狀態。
            st.session_state["v12_saved_custom_style_choice"] = _pending_saved_style_choice
        _saved_choice = st.selectbox(
            "💾 選擇已儲存的自定義風格",
            _saved_labels,
            key="v12_saved_custom_style_choice",
        )
        if _show_saved_style_notice:
            st.success("✅ 此風格已儲存，並已同步更新到上方選單。")
        if _saved_choice != "↓ 請選擇已儲存風格":
            st.session_state.pop("v12_loaded_project_custom_style", None)
            _pos = _saved_labels.index(_saved_choice) - 1
            custom_style = _custom_style_slots[_pos][2]
            st.info(f"💾 已套用：{_custom_style_slots[_pos][0]:02d}｜{_custom_style_slots[_pos][1]}")
        else:
            _loaded_project_style = st.session_state.get("v12_loaded_project_custom_style")
            _draft_style_prompt = str(st.session_state.get("v12_style_editor_prompt", "") or "").strip()
            if isinstance(_loaded_project_style, dict) and str(_loaded_project_style.get("prompt", "") or "").strip():
                custom_style = str(_loaded_project_style.get("prompt", "") or "").strip()
                _loaded_style_name = str(_loaded_project_style.get("name", "") or "").strip()
                st.info("📦 已載入作品當時的自定義風格" + (f"：{_loaded_style_name}" if _loaded_style_name else ""))
            elif _draft_style_prompt:
                custom_style = _draft_style_prompt
                st.info("✏️ 正在使用尚未儲存的自定義風格草稿。")
            else:
                st.info("👆 請選擇一個已儲存風格；或先在下方建立新的風格。")

        with st.expander("✏️ 管理我的自定義風格", expanded=False):
            st.caption("選擇 01～10 的一個位置進行編輯。輸入時不會重新讀取頁面，完成後再按「儲存此風格」。")
            st.session_state.setdefault("v12_style_editor_slot", 1)
            if st.session_state.pop("v12_style_editor_clear_pending", False):
                _v12_load_style_editor_from_slot()
                st.success("🧹 此風格已清空")
            if "v12_style_editor_name" not in st.session_state or "v12_style_editor_prompt" not in st.session_state:
                _v12_load_style_editor_from_slot()
            st.selectbox(
                "📁 編輯哪一組風格",
                list(range(1, 11)),
                format_func=lambda x: f"{x:02d}｜{str(_public02a_get_style_bank()[str(x)]['name']).strip() or f'使用者自定{x}'}",
                key="v12_style_editor_slot",
                on_change=_v12_load_style_editor_from_slot,
            )
            with st.form("v12_style_editor_form", border=False):
                st.text_input("🏷️ 風格名稱", key="v12_style_editor_name", placeholder="例如：童趣手繪漫畫")
                st.text_area("🎨 自定義風格 Prompt", key="v12_style_editor_prompt", height=180, placeholder="在這裡輸入完整的風格 Prompt…")
                _ea, _eb = st.columns(2)
                with _ea:
                    _save_style_editor = st.form_submit_button("💾 儲存此風格", use_container_width=True)
                with _eb:
                    _clear_style_editor = st.form_submit_button("🧹 清空此風格", use_container_width=True)
            if _save_style_editor:
                try:
                    _saved_slot = int(st.session_state.get("v12_style_editor_slot", 1))
                except Exception:
                    _saved_slot = 1
                _saved_slot = max(1, min(10, _saved_slot))
                _saved_name = (
                    str(st.session_state.get("v12_style_editor_name", "") or "").strip()
                    or f"使用者自定{_saved_slot}"
                )
                _v12_save_style_editor()
                st.session_state["v12_pending_saved_custom_style_choice"] = (
                    f"{_saved_slot:02d}｜{_saved_name}"
                )
                st.session_state["v12_show_saved_style_notice"] = True
                st.rerun()
            if _clear_style_editor:
                try:
                    _slot = int(st.session_state.get("v12_style_editor_slot", 1))
                except Exception:
                    _slot = 1
                _bank = _public02a_get_style_bank()
                _bank[str(_slot)] = {"name": f"使用者自定{_slot}", "prompt": ""}
                _public02a_set_style_bank(_bank)
                _save_v10_presets()
                st.session_state["v12_style_editor_clear_pending"] = True
                st.rerun()
        st.warning("ℹ️ 目前只使用「自定義風格」，不會同時套用其他預設風格。")
    else:
        style = style_mode
        st.session_state.pop("v12_loaded_project_custom_style", None)
    with st.expander("📚 查看全部預設風格", expanded=False):
        st.write("、".join(_style_options[2:]))
else:
    style_mode = "↓ 請選擇風格"
    style = style_mode
    custom_style = ""
    st.markdown("<div style='text-align:center;margin:16px 0 6px;font-size:19px;font-weight:800;'>🖼️ 畫風參考</div>", unsafe_allow_html=True)
    st.caption("上傳圖檔後，本次生成會以它的線條、比例、配色與視覺語言為主，不會套用貼圖風格快選。")
    _style_reference_left, _style_reference_center, _style_reference_right = st.columns([1, 2, 1])
    with _style_reference_center:
        _style_reference_upload = st.file_uploader(
            "上傳參考畫風圖檔",
            type=["jpg", "jpeg", "png", "webp"],
            key="v12_style_reference_upload",
            on_change=_v12_style_reference_upload_changed,
            help="這張圖只會作為本次生成的畫風、線條、色彩與氛圍參考。",
        )
    if _style_reference_upload is not None:
        try:
            _v12_set_style_reference_from_upload(_style_reference_upload)
        except Exception:
            st.session_state["v12_style_reference_image_bytes"] = None
            st.session_state["v12_style_reference_filename"] = ""
            st.session_state["v12_style_reference_fingerprint"] = ""
            st.error("❌ 無法讀取這張參考畫風圖，請改用 JPG、PNG 或 WEBP。")
    _style_reference_active = bool(st.session_state.get("v12_style_reference_image_bytes"))
    if _style_reference_active:
        _reference_preview_left, _reference_preview_center, _reference_preview_right = st.columns([1, 2, 1])
        with _reference_preview_center:
            st.image(
                st.session_state["v12_style_reference_image_bytes"],
                caption="畫風參考：" + str(st.session_state.get("v12_style_reference_filename", "參考圖片")),
                width=240,
            )
        st.success("✅ 已啟用畫風參考：本次生成會優先參考這張圖，不會套用貼圖風格快選。")
        st.caption("調整下列設定時不會重新讀取頁面；完成後再按「套用畫風設定」。")
        with st.form("v12_style_reference_details_form", border=False):
            st.radio(
                "畫風融入程度",
                ["柔和融入", "明顯融入", "強烈融入"],
                horizontal=True,
                key="v12_style_reference_strength",
                help="只改變新作品的線條、比例、配色、陰影與表情語言；不會複製參考圖中的角色、文字或單張貼圖。",
            )
            st.text_area(
                "自訂代表性元素（選填）",
                key="v12_style_reference_custom_elements",
                placeholder="例如：漫畫名稱風格、強調圖片上的某些特徵、保留圖片人物的神情",
                max_chars=1200,
            )
            with st.container(key="v12_style_reference_save_button"):
                _apply_style_reference_details = st.form_submit_button(
                    "自訂代表性元素(輸入完請點選套用)",
                    type="primary",
                    use_container_width=True,
                )
        if _apply_style_reference_details:
            st.success("✅ 畫風參考設定已套用")
        st.caption("💡 這段說明會直接補充給 AI，協助它把你指定的特色融入新人物；不會複製參考圖中的角色、文字或版面。")
        st.caption("若打包時同步保存並勾選「保存私人創作素材」，這張參考圖會一併保存，供日後載回續作。")

v10_section("👤 ③ 人物與畫面特色", "#2ecc71")
st.caption("可複選；自定義人物／場景需求只有打勾才會啟用。調整欄位時不會重新讀取頁面，完成後再儲存。")
with st.form("v12_character_settings_form", border=False):
    selected_character=st.multiselect("🎯 人物／畫面特色（可複選）",CHARACTER_OPTIONS,key="v10_character_options")
    _c1,_c2=st.columns([1,8])
    with _c1:
        st.checkbox("啟用", key="v10_character_enabled_1")
    with _c2:
        st.text_area(
            "自定義人物／場景需求",
            key="v10_character_custom_1",
            height=70,
        )
    _save_character_settings = st.form_submit_button("💾 儲存人物／場景設定", use_container_width=True)
custom_character = (
    str(st.session_state.get("v10_character_custom_1", "") or "").strip()
    if st.session_state.get("v10_character_enabled_1", False)
    else ""
)
if _save_character_settings:
    if _save_v10_presets():
        st.success("✅ 人物／場景設定已儲存")
    else:
        st.error("❌ 儲存失敗")

st.session_state.setdefault("v12_sticker_content_mode", V12_STICKER_TEXT_MODE)
v10_section("💬 ④ 貼圖類型", "#3498db")
st.caption("選擇這組貼圖要使用文字，或以人物表情特寫呈現。")

def _v12_set_sticker_content_mode(mode):
    # 切換模式前後都以獨立草稿保存／回填，不依賴條件式 widget state。
    if mode == V12_STICKER_EXPRESSION_MODE:
        _v12_capture_sticker_action_draft()
    elif mode == V12_STICKER_TEXT_MODE:
        _v12_restore_sticker_action_widgets()
    st.session_state["v12_sticker_content_mode"] = mode


_sticker_content_choice = str(st.session_state.get(
    "v12_sticker_content_mode", V12_STICKER_TEXT_MODE
))
_mode_text_col, _mode_expression_col = st.columns(2)
with _mode_text_col:
    st.button(
        ("✓ " if _sticker_content_choice == V12_STICKER_TEXT_MODE else "")
        + V12_STICKER_TEXT_MODE,
        key="v12_select_text_sticker_mode",
        use_container_width=True,
        type="primary" if _sticker_content_choice == V12_STICKER_TEXT_MODE else "secondary",
        on_click=_v12_set_sticker_content_mode,
        args=(V12_STICKER_TEXT_MODE,),
    )
with _mode_expression_col:
    st.button(
        ("✓ " if _sticker_content_choice == V12_STICKER_EXPRESSION_MODE else "")
        + V12_STICKER_EXPRESSION_MODE,
        key="v12_select_expression_sticker_mode",
        use_container_width=True,
        type="primary" if _sticker_content_choice == V12_STICKER_EXPRESSION_MODE else "secondary",
        on_click=_v12_set_sticker_content_mode,
        args=(V12_STICKER_EXPRESSION_MODE,),
    )
_sticker_mode = _v12_sticker_mode_value(_sticker_content_choice)
if _sticker_mode == "text":
    v12_mode_panel_heading('使用「✨ 主題 AI 幫想」或「🎲 隨機用語」', '#3498db')
    st.caption("先選擇你想使用的文字來源；01～08 文字格會一直保留顯示。")
else:
    v12_mode_panel_heading("😶 使用表情貼圖（無文字）", "#3498db")
    st.caption("每格都不會出現文字；AI 會優先呈現臉部與上半身特寫，讓情緒一眼可辨。")

# V12｜文字來源面板：只有使用者主動點選後，才展開對應功能，
# 避免 AI 文案與隨機用語區塊同時堆在畫面上。
st.session_state.setdefault("v12_text_source_panel", "")

def _v12_toggle_text_source(_panel):
    if st.session_state.get("v12_text_source_panel", "") == _panel:
        st.session_state["v12_text_source_panel"] = ""
    else:
        st.session_state["v12_text_source_panel"] = _panel

if _sticker_mode == "text":
    _src_a, _src_b = st.columns(2)
    with _src_a:
        st.button(
            "✨ AI 幫想 8 句貼圖文字",
            key="v12_open_ai_copy_panel",
            use_container_width=True,
            type="primary" if st.session_state.get("v12_text_source_panel") == "ai" else "secondary",
            on_click=_v12_toggle_text_source,
            args=("ai",),
        )
    with _src_b:
        st.button(
            "🎲 隨機用語與自定義語詞池",
            key="v12_open_random_panel",
            use_container_width=True,
            type="primary" if st.session_state.get("v12_text_source_panel") == "random" else "secondary",
            on_click=_v12_toggle_text_source,
            args=("random",),
        )

# V12｜AI 貼圖文案助手 V1：只負責文字，不碰原本圖片生成流程。
#
# AI 圖片與文案皆統一使用已登入會員的站方 API 與用量帳本。
_v12_current_api_mode = BETA_API_MODE
st.session_state["v12_saved_api_mode"] = BETA_API_MODE
st.session_state["v11_api_mode"] = BETA_API_MODE

def _v12_render_text_slot(i):
    _widget_key = f"v12_sticker_text_widget_{i}"
    # 以獨立 widget key 保留輸入內容；canonical sticker_text_* 由 callback 同步。
    st.session_state.setdefault(
        _widget_key,
        str(st.session_state.get(f"sticker_text_{i}", "") or ""),
    )
    st.text_input(
        f"{i+1:02d}",
        key=_widget_key,
        on_change=_v12_sync_sticker_text,
        args=(i,),
        placeholder="輸入想要的文字",
    )
    # 本輪 widget 可能剛被渲染；canonical state 直接取目前 widget 值，
    # 確保後續局部上色 rerun 仍保有最新的 01～08 文字。
    st.session_state[f"sticker_text_{i}"] = str(
        st.session_state.get(_widget_key, "") or ""
    )

    _action_enabled_widget_key = f"v12_sticker_action_enabled_widget_{i}"
    _action_draft_item = _v12_action_draft()[i]
    st.session_state.setdefault(
        _action_enabled_widget_key,
        bool(_action_draft_item["enabled"]),
    )
    _action_enabled = st.checkbox(
        "🎬 啟用動作指令",
        key=_action_enabled_widget_key,
        on_change=_v12_sync_sticker_action_enabled,
        args=(i,),
    )
    _v12_write_sticker_action_draft_slot(i, enabled=_action_enabled)
    if _action_enabled:
        _action_prompt_widget_key = f"v12_sticker_action_prompt_widget_{i}"
        st.session_state.setdefault(
            _action_prompt_widget_key,
            str(_action_draft_item["prompt"] or ""),
        )
        st.text_input(
            "輸入希望動作指令",
            key=_action_prompt_widget_key,
            on_change=_v12_sync_sticker_action_prompt,
            args=(i,),
            placeholder="例如：雙手抱頭後退、指著文字大笑、比出勝利手勢",
            max_chars=500,
        )
        _v12_write_sticker_action_draft_slot(
            i,
            prompt=st.session_state.get(_action_prompt_widget_key, ""),
        )

    _color_enabled_widget_key = f"v12_text_color_enabled_widget_{i}"
    _color_enabled = st.checkbox(
        "🎨 啟用局部文字上色",
        key=_color_enabled_widget_key,
        on_change=_v12_sync_text_color_enabled,
        args=(i,),
    )
    # checkbox 本身使用 widget key；canonical state 只作為穩定資料來源。
    st.session_state[f"v12_text_color_enabled_{i}"] = bool(_color_enabled)

    if _color_enabled:
        count = int(st.session_state.get(f"v12_text_color_count_{i}", 0))
        if count < 1:
            count = 1
            st.session_state[f"v12_text_color_count_{i}"] = 1

        st.caption("💡 輸入要變色的文字片段，再點選色卡；每格最多 3 段。")

        for j in range(count):
            segs = _v12_get_color_segments(i)
            current = segs[j]
            _a, _b = st.columns([1.35, 1])

            with _a:
                st.session_state.setdefault(
                    f"v12_text_color_segment_{i}_{j}",
                    current.get("text", ""),
                )
                # 讓使用者在文字片段旁邊直接看到目前套用的顏色。
                if current.get("color_name") and current.get("hex"):
                    _chip_name = str(current["color_name"])
                    _chip_hex = str(current["hex"])
                    _chip_border = "#777777" if _chip_hex.upper() == "#FFFFFF" else _chip_hex
                    st.markdown(
                        f"""
                        <div style="display:flex;align-items:center;gap:8px;
                                    margin:0 0 6px 0;font-weight:700;">
                            <span>文字片段 {j+1}</span>
                            <span style="display:inline-flex;align-items:center;gap:5px;
                                         padding:2px 8px 2px 6px;border-radius:999px;
                                         background:rgba(127,127,127,.10);
                                         font-size:13px;font-weight:600;">
                                <span style="width:14px;height:14px;border-radius:50%;
                                             background:{_chip_hex};
                                             border:2px solid {_chip_border};
                                             display:inline-block;"></span>
                                {_chip_name}
                            </span>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div style="margin:0 0 6px 0;font-weight:700;">文字片段 {j+1}</div>',
                        unsafe_allow_html=True,
                    )
                st.button(
                    "自動帶入文字",
                    key=f"v12_text_color_segment_paste_{i}_{j}",
                    on_click=_v12_paste_sticker_text_to_color_segment,
                    args=(i, j),
                    type="primary",
                    use_container_width=True,
                )
                st.text_input(
                    f"文字片段 {j+1}",
                    key=f"v12_text_color_segment_{i}_{j}",
                    placeholder="例如：你還有",
                    label_visibility="collapsed",
                )
                segs[j]["text"] = str(
                    st.session_state.get(f"v12_text_color_segment_{i}_{j}", "")
                )

            with _b:
                st.markdown("**🎨 選擇顏色**")
                color_cols = st.columns(4)
                for k, (name, hex_code, icon) in enumerate(V12_TEXT_COLOR_PALETTE):
                    with color_cols[k % 4]:
                        is_selected = current.get("hex") == hex_code
                        label = f"✓ {name}" if is_selected else f"{icon} {name}"
                        if is_selected:
                            wrapper_key = (
                                f"v12_color_selected_white_{i}_{j}_{k}"
                                if k == 0
                                else f"v12_color_selected_{i}_{j}_{k}"
                            )
                            with st.container(key=wrapper_key):
                                st.button(
                                    label,
                                    key=f"v12_text_color_pick_{i}_{j}_{k}",
                                    use_container_width=True,
                                    on_click=_v12_pick_segment_color,
                                    args=(i, j, name, hex_code),
                                )
                        else:
                            st.button(
                                label,
                                key=f"v12_text_color_pick_{i}_{j}_{k}",
                                use_container_width=True,
                                on_click=_v12_pick_segment_color,
                                args=(i, j, name, hex_code),
                            )


        if count < 3:
            st.button(
                "＋ 新增一段文字顏色",
                key=f"v12_text_color_add_{i}",
                use_container_width=True,
                on_click=_v12_add_text_color_segment,
                args=(i,),
            )

        # 精簡預覽：直接把整句文字中已設定的片段顯示成實際顏色。
        # 不再顯示原本的綠色「已設定」說明框，避免手機版過度拉長。
        source_text = str(st.session_state.get(f"sticker_text_{i}", "") or "")
        if source_text.strip():
            import html as _html
            matches = []
            search_from = 0
            for seg in _v12_get_color_segments(i):
                part = str(seg.get("text", "") or "").strip()
                color_hex = str(seg.get("hex", "") or "").strip()
                if not part or not color_hex:
                    continue
                pos = source_text.find(part, search_from)
                if pos < 0:
                    pos = source_text.find(part)
                if pos >= 0:
                    end = pos + len(part)
                    if not any(pos < e and end > st_ for st_, e, _, _ in matches):
                        matches.append((pos, end, part, color_hex))
                        search_from = end

            if matches:
                matches.sort(key=lambda x: x[0])
                parts = []
                cursor = 0
                for start, end, part, color_hex in matches:
                    if start > cursor:
                        parts.append(_html.escape(source_text[cursor:start]))
                    border = "#777777" if color_hex.upper() == "#FFFFFF" else color_hex
                    parts.append(
                        f'<span style="color:{_html.escape(color_hex)};font-weight:800;'
                        f'border-bottom:2px solid {border};">{_html.escape(part)}</span>'
                    )
                    cursor = end
                if cursor < len(source_text):
                    parts.append(_html.escape(source_text[cursor:]))
                preview_html = "".join(parts)
                st.markdown(
                    f'<div style="margin:8px 0 2px 0;padding:8px 10px;'
                    f'border-radius:10px;background:rgba(127,127,127,.08);'
                    f'font-size:18px;font-weight:700;line-height:1.5;">{preview_html}</div>',
                    unsafe_allow_html=True,
                )


@st.fragment
def _v12_render_text_slots():
    """Keep ordinary text and colour edits local to the text editor."""
    cols = st.columns(4)
    for i, col in enumerate(cols):
        with col:
            _v12_render_text_slot(i)

    cols = st.columns(4)
    for i, col in enumerate(cols, start=4):
        with col:
            _v12_render_text_slot(i)

    texts = get_texts()
    filled = sum(bool(x.strip()) for x in texts)
    st.info(f"已填寫 {filled} / 8 格")


@st.fragment
def _v12_render_expression_sticker_slots():
    """Keep expression-only edits local, like the text editor fragment."""
    st.caption(
        "表情欄位可自由輸入，例如：慌張、驚恐、傻眼、得意、委屈。"
        "勾選「隨機」時，AI 會自由安排表情、動作與鏡頭；同一批 8 張會刻意避開相同表情、手勢與構圖。"
        "除非你自行指定，系統不會自動塞入大面積背景或搶眼特效。"
    )
    for row_start in (0, 4):
        cols = st.columns(4)
        for offset, col in enumerate(cols):
            i = row_start + offset
            with col:
                st.markdown(f"**圖片 {i + 1:02d}**")
                st.session_state.setdefault(f"v12_expression_emotion_{i}", "")
                st.session_state.setdefault(
                    f"v12_expression_emotion_random_{i}", False
                )
                st.session_state.setdefault(
                    f"v12_expression_effect_{i}", V12_EXPRESSION_EFFECTS[0]
                )
                emotion_random = st.checkbox(
                    "隨機",
                    key=f"v12_expression_emotion_random_{i}",
                )
                st.text_input(
                    "表情欄位",
                    key=f"v12_expression_emotion_{i}",
                    placeholder="例如：慌張",
                    disabled=emotion_random,
                )
                st.selectbox(
                    "想帶入什麼特效？",
                    V12_EXPRESSION_EFFECTS,
                    key=f"v12_expression_effect_{i}",
                )
                st.session_state.setdefault(
                    f"v12_expression_effect_action_prompt_{i}", ""
                )
                st.text_input(
                    "特效或動作提示詞",
                    key=f"v12_expression_effect_action_prompt_{i}",
                    placeholder="例如：大動作、荒謬、搶眼特效",
                )
    configured = sum(
        bool(
            spec["emotion"]
            or spec["emotion_random"]
            or spec["effect"] != V12_EXPRESSION_EFFECTS[0]
            or spec["effect_action_prompt"]
        )
        for spec in _v12_get_expression_specs()
    )
    st.info(f"已設定 {configured} / 8 格的表情方向")


if _sticker_mode == "text" and st.session_state.get("v12_text_source_panel") == "ai":
    st.caption("📌 每次會先產生 16 句候選，再由你挑選、儲存到主題用語池；會員模式會使用每日 AI幫想額度，不會扣除圖片生成次數。")
    _v12_render_ai_copy_assistant()


@st.fragment
def _v12_render_random_phrase_panel():
    _pool_names=list(V8_RANDOM_POOLS.keys())
    _pool_choice=st.selectbox(
        "🎲 隨機用語池",
        ["↓ 請選擇語詞池", "⭐ 我的自定義語詞池", "全部內建語詞"] + _pool_names,
        key="v10_phrase_pool_choice",
    )

    if _pool_choice=="⭐ 我的自定義語詞池":
        with st.expander("💾 我的自定義語詞池",expanded=True):
            _new_phrase=st.text_input(
                "➕ 新增一句語詞",
                key="v10_new_phrase",
                placeholder="例如：今天也要加油！",
            )
            _pa,_pb,_pc=st.columns(3)
            with _pa:
                if st.button("➕ 加入語詞池",key="v10_add_phrase",use_container_width=True):
                    if _new_phrase.strip():
                        V10_CUSTOM_PHRASE_POOL.append(_new_phrase.strip())
                        V10_CUSTOM_PHRASE_POOL[:]=list(dict.fromkeys(V10_CUSTOM_PHRASE_POOL))
                        if _save_v10_phrase_pool(V10_CUSTOM_PHRASE_POOL):
                            st.success("✅ 已加入並儲存")
                            st.rerun()
                        else:
                            st.error("❌ 儲存失敗")
            with _pb:
                if st.button("🎲 從我的池子抽 8 句",key="v10_random_my_pool",use_container_width=True):
                    if V10_CUSTOM_PHRASE_POOL:
                        vals=random.sample(V10_CUSTOM_PHRASE_POOL,min(8,len(V10_CUSTOM_PHRASE_POOL)))
                        while len(vals)<8:
                            vals.append(random.choice(V10_CUSTOM_PHRASE_POOL))
                        random.shuffle(vals)
                        set_texts(vals)
                        st.rerun()
                    else:
                        st.warning("目前自定義語詞池是空的。")
            with _pc:
                if st.button("🗑️ 清空我的池子",key="v10_clear_my_pool",use_container_width=True):
                    V10_CUSTOM_PHRASE_POOL.clear()
                    if _save_v10_phrase_pool(V10_CUSTOM_PHRASE_POOL):
                        st.success("✅ 已清空")
                        st.rerun()

            if V10_CUSTOM_PHRASE_POOL:
                st.caption(f"目前共有 {len(V10_CUSTOM_PHRASE_POOL)} 句")
                st.write("、".join(V10_CUSTOM_PHRASE_POOL))
            else:
                st.info("尚未建立自定義語詞。")

        _active_pool=V10_CUSTOM_PHRASE_POOL
    else:
        if _pool_choice=="↓ 請選擇語詞池":
            _active_pool=[]
            st.info("👆 請先選擇一個語詞池。")
        elif _pool_choice=="全部內建語詞":
            _active_pool=[x for vals in V8_RANDOM_POOLS.values() for x in vals]
        else:
            _active_pool=V8_RANDOM_POOLS.get(_pool_choice,[])

        a,b,c=st.columns(3)
        with a:
            if st.button("🎲 隨機填入 8 格",use_container_width=True,key="v10_random_v8"):
                if _active_pool:
                    vals=random.sample(_active_pool,min(8,len(_active_pool)))
                    while len(vals)<8:
                        vals.append(random.choice(_active_pool))
                    random.shuffle(vals)
                    set_texts(vals)
                    st.rerun()
        with b:
            st.write(f"目前語詞池：{len(_active_pool)} 句")
        with c:
            st.write("內建語詞分類")

    # 內建的「分類→語句→指定格」功能保留。
    if _pool_choice not in ("⭐ 我的自定義語詞池", "↓ 請選擇語詞池"):
        p1,p2,p3=st.columns([1.2,2.4,0.8])
        with p1:
            _common_cat=st.selectbox("常用語分類",_pool_names,key="v8_common_cat")
        with p2:
            _common_phrase=st.selectbox("常用語參考",V8_RANDOM_POOLS.get(_common_cat,[]),key="v8_common_phrase")
        with p3:
            _target_slot=st.selectbox("放入第",[f"{i:02d}" for i in range(1,9)],key="v8_target_slot")
        if st.button("➕ 放入選定格",key="v8_insert_phrase"):
            set_texts([
                _common_phrase if i==int(_target_slot)-1 else st.session_state.get(f"sticker_text_{i}","")
                for i in range(8)
            ])
            st.rerun()


if _sticker_mode == "text" and st.session_state.get("v12_text_source_panel") == "random":
    _v12_render_random_phrase_panel()

if _sticker_mode == "text":
    _v12_render_text_slots()
else:
    _v12_render_expression_sticker_slots()
texts = get_texts()
expression_specs = _v12_get_expression_specs()
_prepared_expression_specs = st.session_state.get("v12_expression_generation_specs")
_prompt_expression_specs = (
    _v12_normalize_expression_specs(_prepared_expression_specs)
    if _sticker_mode == "expression" and isinstance(_prepared_expression_specs, list)
    else expression_specs
)

if _sticker_mode == "text":
    with st.expander("📚 查看全部語詞分類與內容",expanded=False):
        for cat,vals in V8_RANDOM_POOLS.items():
            st.markdown(f"**{cat}**")
            st.write("、".join(vals))

if _sticker_mode == "text":
    st.divider()
    v10_section("🔤 貼圖字型＋125 種帶圖字型", "#8e67d8")
    v10_subsection("🧩 文字外框／形狀效果", "#8e67d8")
    st.caption("125 種帶圖字型決定字本身的筆畫與材質；這裡只決定文字外圍的描邊、對話框或裝飾形狀，兩者可以同時使用。")
    _saved_text_style = str(st.session_state.get("v8_text_style", "") or "")
    if _saved_text_style not in V8_TEXT_STYLE_OPTIONS:
        st.session_state["v8_text_style"] = V8_TEXT_STYLE_LEGACY_MAP.get(
            _saved_text_style, "不使用外框"
        )
    text_style = st.selectbox(
        "文字外框／形狀效果",
        V8_TEXT_STYLE_OPTIONS,
        index=0,
        key="v8_text_style",
    )
else:
    text_style = ""

_selected_font = st.session_state.get("v8_selected_font", None)
if _sticker_mode == "text":
    v10_subsection("🔤 125 種帶圖字型", "#8e67d8")

# 125 種帶圖字型｜快速選擇＋分頁圖卡總覽
# 手機不再一次渲染 125 張大圖，降低滑動成本。
# ------------------------------------------------------------
_font_labels = [
    f"{_i:03d}｜{V8_TEXT_EFFECT_CATALOG[_i]}"
    for _i in range(1, 126)
]

if "v12_font_quick_pick" not in st.session_state:
    st.session_state["v12_font_quick_pick"] = "— 快速選擇字型｜可直接輸入數字 —"

_quick_font_choice = "— 快速選擇字型｜可直接輸入數字 —"
if _sticker_mode == "text":
    st.caption("選擇字型後會立即套用並更新下方預覽。")
    st.markdown('<div id="v10-font-result-anchor" style="scroll-margin-top: 72px;"></div>', unsafe_allow_html=True)
    _quick_font_choice = st.selectbox(
        "⚡ 快速選擇 125 種字型",
        ["— 快速選擇字型｜可直接輸入數字 —"] + _font_labels,
        key="v12_font_quick_pick",
    )
    if _quick_font_choice != "— 快速選擇字型｜可直接輸入數字 —":
        _quick_font_no = int(_quick_font_choice.split("｜", 1)[0])
        if st.session_state.get("v8_selected_font") != _quick_font_no:
            st.session_state["v8_selected_font"] = _quick_font_no
            st.session_state["v10_font_gallery_open"] = False
        _selected_font = _quick_font_no
    if _selected_font:
        st.success(f"🎨 已選擇 125 字型：{_selected_font:03d}｜{V8_TEXT_EFFECT_CATALOG[_selected_font]}")
    else:
        st.info("尚未指定 125 種字型；可從下方總覽選用。")

# 選字型圖卡後回到快速選單與目前選擇提示的位置。
if _sticker_mode == "text" and st.session_state.pop("v10_scroll_to_font_result", False):
    import streamlit.components.v2 as components
    _v10_font_scroll = components.component(
        "v10_font_scroll",
        html="<span aria-hidden='true'></span>",
        js="""
        export default function(component) {
            const anchor = document.querySelector("#v10-font-result-anchor");
            if (!anchor) return;
            requestAnimationFrame(() => {
                requestAnimationFrame(() => {
                    anchor.scrollIntoView({behavior: "instant", block: "start"});
                });
            });
        }
        """,
        isolate_styles=False,
    )
    _v10_font_scroll(key="font_scroll_once")

if "v10_font_gallery_open" not in st.session_state:
    st.session_state.v10_font_gallery_open = False
if "v12_font_gallery_page" not in st.session_state:
    st.session_state.v12_font_gallery_page = 1

if _sticker_mode == "text" and st.button(
    "📚 開啟 125 種帶圖字型總覽" if not st.session_state.v10_font_gallery_open
    else "📖 關閉 125 種帶圖字型總覽",
    key="v10_font_gallery_toggle",
    use_container_width=True,
):
    st.session_state.v10_font_gallery_open = not st.session_state.v10_font_gallery_open
    st.rerun()

if _sticker_mode == "text" and st.session_state.v10_font_gallery_open:
    st.markdown(
        '<div class="v12-font-gallery-note">📱 手機版已改為分頁瀏覽，每頁 12 種；點選後會立即關閉總覽並回到已選結果。</div>',
        unsafe_allow_html=True,
    )

    _font_per_page = 12
    _font_total_pages = (125 + _font_per_page - 1) // _font_per_page
    _page_options = list(range(1, _font_total_pages + 1))

    # 使用單一 Session State key 管理頁數，避免 widget 建立後
    # 再直接寫入另一個同輪 widget key 而觸發 StreamlitAPIException。
    if "v12_font_gallery_page_select" not in st.session_state:
        st.session_state["v12_font_gallery_page_select"] = 1

    _current_page = st.selectbox(
        "📄 瀏覽頁數",
        _page_options,
        key="v12_font_gallery_page_select",
    )
    st.session_state.v12_font_gallery_page = _current_page

    _start_no = (_current_page - 1) * _font_per_page + 1
    _end_no = min(125, _start_no + _font_per_page - 1)
    st.caption(f"目前顯示 {_start_no:03d}～{_end_no:03d} ／ 125")

    # --------------------------------------------------------
    # V12｜STEP 01B③
    # 響應式字型圖卡牆：
    # 桌機 4 欄、平板 3 欄、手機固定 2 欄。
    #
    # 不再使用 st.columns() 直接承載圖片，避免手機版把 4 欄
    # 全部垂直堆成超長單欄。
    # 圖卡改由 HTML/CSS Grid 顯示；選擇動作集中到下方數字輸入，
    # 讓手機使用者可以「快速瀏覽 → 輸入編號 → 立即選用」。
    # --------------------------------------------------------
    # --------------------------------------------------------
    # V12｜STEP 01B③-2
    # 可直接點選的原生 Streamlit 字型圖卡。
    # 圖片維持原生 st.image，編號就是選取按鈕。
    # --------------------------------------------------------
    # 用具名 container 包住原生 Streamlit 欄位，讓手機 CSS 能精準覆寫成雙欄。
    with st.container(key="v12_font_card_grid"):
        _font_cols = st.columns(4)

        for _i in range(_start_no, _end_no + 1):
            _p = V8_FONT_PREVIEW_DIR / f"{_i:03d}.jpg"
            if not _p.exists():
                continue

            with _font_cols[(_i - _start_no) % 4]:
                st.image(str(_p), use_container_width=True)

                # 直接點選編號即可選取該字型。
                # callback 先在 widget 建立前更新狀態，避免 Streamlit
                # 禁止在同一次 rerun 中直接修改已建立的 selectbox key。
                def _v12_pick_font_from_card(font_no=_i):
                    st.session_state["v8_selected_font"] = font_no
                    st.session_state["v10_scroll_to_font_result"] = True
                    st.session_state["v10_font_gallery_open"] = False

                    # v12_font_quick_pick 是上方已建立的 widget key。
                    # 不直接寫入它，改由後續初始化／同步邏輯依 v8_selected_font
                    # 自動顯示目前選擇的字型，避免 StreamlitAPIException。

                st.button(
                    f"{_i:03d}",
                    key=f"font_card_pick_{_i}",
                    use_container_width=True,
                    on_click=_v12_pick_font_from_card,
                )

                st.caption(V8_TEXT_EFFECT_CATALOG[_i])
    # callback 會在 Streamlit 下一次完整執行前更新頁數，
    # 不會在 widget 已實例化後直接修改其 key。
    def _v12_font_change_page(delta):
        current = int(st.session_state.get("v12_font_gallery_page_select", 1))
        target = max(1, min(_font_total_pages, current + delta))
        st.session_state["v12_font_gallery_page_select"] = target
        st.session_state["v12_font_gallery_page"] = target

    _prev_col, _page_col, _next_col = st.columns([1, 1.2, 1])
    with _prev_col:
        st.button(
            "⬅️ 上一頁",
            key="v12_font_prev",
            use_container_width=True,
            disabled=_current_page <= 1,
            on_click=_v12_font_change_page,
            args=(-1,),
        )
    with _page_col:
        st.markdown(
            f'<div style="text-align:center;padding:.55rem 0;font-weight:800;">第 {_current_page} / {_font_total_pages} 頁</div>',
            unsafe_allow_html=True,
        )
    with _next_col:
        st.button(
            "下一頁 ➡️",
            key="v12_font_next",
            use_container_width=True,
            disabled=_current_page >= _font_total_pages,
            on_click=_v12_font_change_page,
            args=(1,),
        )


if _sticker_mode == "text":
    with st.expander("🔎 已選字型大圖", expanded=False):
        if _selected_font:
            _p = V8_FONT_PREVIEW_DIR / f"{_selected_font:03d}.jpg"
            if _p.exists():
                # 預覽維持置中，避免原圖在桌機與手機上佔滿整個區塊。
                _font_preview_left, _font_preview_center, _font_preview_right = st.columns([1, 2, 1])
                with _font_preview_center:
                    st.image(
                        str(_p),
                        caption=f"{_selected_font:03d}｜{V8_TEXT_EFFECT_CATALOG[_selected_font]}",
                        use_container_width=True,
                    )
                st.caption("以上為參考效果；實際生成會由 AI 依人物、文字與整體風格重新詮釋。")
        else:
            st.write("尚未選擇。")

st.divider()
v10_section("🌈 ⑤ 背景設定", "#16a085")
st.markdown('<div class="v11-transparent-title">🖼️ 透明背景</div>', unsafe_allow_html=True)
with st.container(key="transparent_png_option"):
    transparent = st.checkbox("使用透明背景 PNG", value=False)

v10_subsection("🌈 貼圖設定查看", "#ff9f43")
style_mode = st.session_state.get("v10_style_mode", "↓ 請選擇風格")

_prompt_color_segments = _v12_valid_color_segments(texts)
_prompt_sticker_action_prompts = _v12_get_sticker_action_prompts()
_prompt_character_features = list(selected_character)
if _style_reference_active:
    # 「五官比例保留」這類照片導向提示會和畫風轉譯互相拉扯。
    # 仍保留人物身份，但把比例、材質與筆觸交由畫風參考主導。
    _prompt_character_features = [
        item for item in _prompt_character_features if item != "五官比例保留"
    ]
prompt = build_prompt(
    style,
    custom_style,
    _prompt_character_features,
    custom_character,
    texts,
    transparent,
    color_segments=_prompt_color_segments,
    ai_copy_topic=st.session_state.get("v12_ai_copy_topic_used", st.session_state.get("v12_ai_copy_topic", "")),
    ai_copy_tone=st.session_state.get("v12_ai_copy_tone_used", st.session_state.get("v12_ai_copy_tone", "")),
    ai_copy_topics=st.session_state.get("v12_selected_themes", []),
    sticker_mode=_sticker_mode,
    expression_specs=_prompt_expression_specs,
    action_prompts=_prompt_sticker_action_prompts,
)

if _style_reference_active:
    _style_reference_strength = str(
        st.session_state.get("v12_style_reference_strength", "明顯融入")
    )
    _style_reference_strength_instruction = {
        "柔和融入": "將人物畫成插畫角色，輕度採用參考圖的配色與筆觸；不保留照片或原物的真實材質。",
        "明顯融入": "第二張圖是本次的主要美術指導：人物必須明確轉換成它的線條、造型簡化、色塊、陰影、比例與誇張表情節奏；第一張圖只提供人物身份辨識，不可保留照片、石材、3D 渲染或寫實材質。",
        "強烈融入": "第二張圖的高階視覺語言完全優先於第一張圖的寫實材質：將人物重新繪製成參考圖的插畫世界，強烈採用其線條、筆觸、比例、平面色塊、留白與荒謬表情節奏；禁止照片感、石材感、立體雕塑感、3D 渲染與通用 LINE 貼圖畫風（除非第二張圖本身具有那些特徵）。",
    }.get(_style_reference_strength, "第二張圖是本次的主要視覺語言。")
    prompt += (
        "\n【畫風參考模式】本次輸入的第二張圖片是畫風參考圖。"
        + _style_reference_strength_instruction
        + "請先辨識參考圖的高階視覺特徵，包括線條與筆觸、角色比例、配色、"
        "陰影方式、畫面留白、表情誇張程度與整體喜劇／情緒節奏，並把這些特徵套用到全新貼圖。"
        "第一張人物照片只用於保留人物身份辨識特徵，請將人物完整重新繪製並轉譯進參考圖的視覺語言，而不是把參考圖僅當成背景配色或裝飾。"
        "請創作全新的八格貼圖；"
        "不要直接複製參考圖中的既有角色、文字、標誌、版面或任何單一貼圖內容。"
        "不要套用其他預設或自定義貼圖風格。"
    )
    _style_reference_custom_elements = str(
        st.session_state.get("v12_style_reference_custom_elements", "") or ""
    ).strip()
    if _style_reference_custom_elements:
        prompt += (
            "\n【畫風參考補充】除了整體畫風外，使用者特別希望保留或轉譯以下特色：「"
            + _style_reference_custom_elements
            + "」。請將其明確融入全新人物的設計、表情或局部視覺語言；"
            "不要只當成背景裝飾，也不要直接複製參考圖中的既有角色、文字或版面。"
        )
elif style_mode == V8_STYLE_CUSTOM_OPTION:
    prompt += (
        "\n【V10 風格模式】目前使用「自定義風格」模式。"
        "請不要另外套用任何 預設風格，只依照使用者自定義風格描述生成。"
    )
else:
    prompt += (
        f"\n【V10 風格模式】目前使用預設風格：「{style_mode}」。"
        "不要把未選取的自定義風格加入生成。"
    )

_selected_font_for_prompt = st.session_state.get("v8_selected_font")
if _sticker_mode == "text" and text_style and text_style != "不使用外框":
    prompt += (
        f"\n文字外框／形狀效果：{text_style}。"
        "此設定只影響每一句文字周圍的描邊、貼紙輪廓、對話框或裝飾形狀；"
        "不要取代文字本身的字型筆畫與材質。"
    )
if _sticker_mode == "text" and _selected_font_for_prompt:
    prompt += (
        f"\n125種帶圖字型參考：{_selected_font_for_prompt:03d}｜"
        f"{V8_TEXT_EFFECT_CATALOG[_selected_font_for_prompt]}。"
        "\n請把此字型當作文字本身的筆畫、材質與視覺參考；"
        "若同時指定文字外框／形狀效果，兩者必須融合呈現、不可互相覆蓋。"
        "不要照搬參考圖中的人物或其他內容。"
    )
with st.expander("🔍 點選查看貼圖設定"):
    st.markdown(
        """
        <div style="
            background:linear-gradient(135deg,#171c27,#202735);
            border:1px solid #394457;
            border-left:6px solid #58a6ff;
            border-radius:12px;
            padding:18px 22px;
            margin:8px 0 14px;
        ">
            <div style="font-size:24px;font-weight:800;color:#ffffff;margin-bottom:8px;">
                📝 AI 實際生成 Prompt（完整內容）
            </div>
            <div style="font-size:15px;color:#b9c4d4;line-height:1.6;">
                以下保留實際送給 AI 的完整 Prompt；只改善閱讀呈現，不修改生成內容。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    _prompt_text = str(prompt or "").strip()

    if _prompt_text:
        # 以「內容主題」分段，而不是只替少數標題上色。
        # 每個邏輯區塊都有自己的標題色、背景與分隔線。
        _lines = [x.strip() for x in _prompt_text.splitlines() if x.strip()]

        _groups = []
        _current = []
        _section_keywords = (
            "使用者自定義風格",
            "風格：", "角色：", "人物：", "構圖：", "色彩：",
            "整體氛圍：", "避免：", "人物與畫面特色：",
            "文字外框／形狀效果：", "125種帶圖字型參考：",
            "請使用透明背景PNG", "整體具有LINE貼圖",
            "第1格的指定貼圖文字", "第2格的指定貼圖文字",
            "第3格的指定貼圖文字", "第4格的指定貼圖文字",
            "第5格的指定貼圖文字", "第6格的指定貼圖文字",
            "第7格的指定貼圖文字", "第8格的指定貼圖文字",
            "目前使用",
        )

        for _line in _lines:
            # 只有真正的「主題開頭」才建立新區塊。
            # 第1～8格指定貼圖文字視為同一個「01～08 貼圖文字」區塊。
            _is_text_slot = bool(re.match(r"^第[1-8]格的指定貼圖文字", _line))
            _is_section = any(_line.startswith(k) for k in _section_keywords)

            if _is_text_slot:
                if _current and not any(
                    bool(re.match(r"^第[1-8]格的指定貼圖文字", x))
                    for x in _current
                ):
                    _groups.append(_current)
                    _current = []
                _current.append(_line)
                continue

            if _is_section and _current:
                _groups.append(_current)
                _current = []
            _current.append(_line)

        if _current:
            _groups.append(_current)

        # 若 Prompt 沒有明確換行，至少把常見主題標籤切出來。
        if len(_groups) == 1 and len(_lines) > 5:
            _raw = _lines[0]
            for _kw in _section_keywords:
                if _kw in _raw:
                    _parts = re.split(
                        r'(?=' + re.escape(_kw) + r')',
                        _raw
                    )
                    if len(_parts) > 1:
                        _groups = [[p.strip()] for p in _parts if p.strip()]
                        break

        _palette = [
            ("#67e8f9", "#10252d", "#1e5361"),  # 青
            ("#86efac", "#10281e", "#27623d"),  # 綠
            ("#f9a8d4", "#2d1726", "#70405a"),  # 粉
            ("#c4b5fd", "#211a35", "#51427c"),  # 紫
            ("#fde68a", "#2b2510", "#6b5b1e"),  # 黃
            ("#fdba74", "#2d1d11", "#704622"),  # 橘
            ("#93c5fd", "#13243a", "#365f91"),  # 藍
        ]

        _html = [
            '<div style="background:#0f141d;border:1px solid #303a4a;'
            'border-radius:14px;padding:10px 14px;'
            'font-family:Arial,Microsoft JhengHei,sans-serif;">'
        ]

        for _idx, _group in enumerate(_groups):
            _accent, _bg, _border = _palette[_idx % len(_palette)]
            _title = _group[0]

            # 01～08 指定貼圖文字統一使用一個彩色區塊。
            _is_slot_group = bool(
                re.match(r"^第[1-8]格的指定貼圖文字", _title)
            )
            if _is_slot_group:
                _heading = "01～08 指定貼圖文字"
                _rest = ""
                _title_is_custom = True
            else:
                _title_is_custom = False

            # 將「主題：內容」拆成彩色標題＋內容。
            _m = re.match(r'^([^：:]{1,24}[：:])(.*)$', _title)
            if not _title_is_custom:
                if _m:
                    _heading = _m.group(1)
                    _rest = _m.group(2).strip()
                else:
                    _heading = _title if len(_title) <= 28 else "Prompt 內容"
                    _rest = "" if _heading == _title else _title

            _safe_heading = (
                _heading.replace("&","&amp;")
                        .replace("<","&lt;")
                        .replace(">","&gt;")
            )

            _html.append(
                f'<div style="margin:10px 0 14px;background:{_bg};'
                f'border:1px solid {_border};border-left:5px solid {_accent};'
                f'border-radius:10px;overflow:hidden;">'
                f'<div style="padding:9px 14px;font-size:18px;font-weight:800;'
                f'color:{_accent};border-bottom:1px solid {_border};">'
                f'{_safe_heading}</div>'
                f'<div style="padding:9px 14px 11px;">'
            )

            if _rest:
                _safe = (
                    _rest.replace("&","&amp;")
                         .replace("<","&lt;")
                         .replace(">","&gt;")
                )
                _html.append(
                    f'<div style="font-size:16px;line-height:1.85;'
                    f'color:#f1f5f9;padding:2px 0 5px;">{_safe}</div>'
                )

            for _line in _group[1:]:
                _safe = (
                    _line.replace("&","&amp;")
                         .replace("<","&lt;")
                         .replace(">","&gt;")
                )
                _html.append(
                    f'<div style="font-size:16px;line-height:1.85;'
                    f'color:#e7edf5;padding:3px 0;">{_safe}</div>'
                )

            _html.append("</div></div>")

        _html.append("</div>")

        st.markdown(
            "".join(_html),
            unsafe_allow_html=True,
        )
    else:
        st.info("目前尚未產生 AI Prompt。")



v10_section("✨ ⑥ 生成 4×2 原始總圖", "#e67e22")
st.markdown('<div class="v11-section-tip">📌 <b>準備完成後：</b>按一次生成即可。生成期間按鈕會自動鎖定，請耐心等待。</div>', unsafe_allow_html=True)

# ============================================================
# V11｜STEP 02C-2｜兩階段生成鎖定（正式版）
#
# 核心設計：
# ① 使用者第一次按下生成：只設定 pending lock，立即 st.rerun()
#    → 本次不呼叫 AI。
# ② 下一次 rerun：先把真正的 st.button 以 disabled=True 畫出來，
#    → 此時按鈕已經是不可點擊狀態，再開始 AI 生成。
# ③ 成功／失敗後解除 lock。
#
# 不依賴 JavaScript，不嘗試修改 Streamlit DOM。
# 後端 Session Lock + Supabase quota / RPC / RLS 仍是安全主防線。
# ============================================================
st.session_state.setdefault("v11_generation_pending", False)
st.session_state.setdefault("v11_generation_in_progress", False)

_generation_pending = bool(st.session_state.get("v11_generation_pending", False))
_generation_in_progress = bool(st.session_state.get("v11_generation_in_progress", False))
_generation_locked = _generation_pending or _generation_in_progress

# ------------------------------------------------------------
# 第一階段：使用者按下按鈕
# 只做必要的輸入檢查，設定 pending lock，立即 rerun。
# 這樣下一次畫面重繪時，按鈕會先以 disabled=True 呈現，
# 然後才進入長時間 AI 工作。
# ------------------------------------------------------------
if st.button(
    "🔄 AI 正在生成，請稍候……" if _generation_locked else "✨ 生成 4×2 八格總圖",
    type="primary",
    use_container_width=True,
    disabled=_generation_locked,
    key="v11_generate_4x2",
):
    if not st.session_state.uploaded_image_bytes:
        st.warning("請先上傳人物照片。")
        st.stop()

    # 文字模式至少需要一格文字；無文字表情模式則由每格的表情／特效方向生成，
    # 不應共用「必須填貼圖文字」的驗證。
    if _sticker_mode == "text" and not any(str(text or "").strip() for text in texts):
        st.warning("請至少輸入一格貼圖文字。")
        st.stop()

    _click_beta_session = _beta_current_user()
    if not _click_beta_session:
        st.error("❌ 請先使用 Google 登入會員 Beta。")
        st.stop()
    if not _beta_site_openai_key():
        st.error("❌ 會員 Beta 的站方生成服務尚未完成設定，請稍後再試。")
        st.stop()
    try:
        _reservation = _beta_reserve_generation(_click_beta_session)
    except Exception:
        st.error("❌ 目前無法確認會員可用生成次數，請稍後再試。")
        st.stop()
    if not _reservation["granted"]:
        if _reservation["job_id"]:
            st.warning("此帳號已有一份圖片正在生成，請等完成後再試。")
        elif _reservation["balance"] > 0:
            st.warning("剛完成生成，請等候 60 秒後再使用下一次體驗額度。")
        else:
            st.warning("體驗額度已使用完畢。")
        st.stop()
    if not _reservation["job_id"]:
        st.error("❌ 無法建立本次生成作業，請稍後再試。")
        st.stop()
    st.session_state["beta_generation_job_id"] = _reservation["job_id"]

    if _sticker_mode == "expression":
        _resolved_specs, _recipe_labels = _v12_prepare_random_expression_specs(
            expression_specs
        )
        st.session_state["v12_expression_generation_specs"] = _resolved_specs
        st.session_state["v12_expression_generation_recipe_labels"] = _recipe_labels
        st.session_state.pop("v12_expression_generation_new_cycle_start", None)
    else:
        for _expression_job_key in (
            "v12_expression_generation_specs",
            "v12_expression_generation_recipe_labels",
            "v12_expression_generation_new_cycle_start",
        ):
            st.session_state.pop(_expression_job_key, None)

    # 先鎖定，再立即重新繪製畫面；本次不呼叫 AI。
    st.session_state["v11_generation_pending"] = True
    st.session_state["v11_generation_started_at"] = __import__("time").time()
    st.rerun()

# ------------------------------------------------------------
# 第二階段：只有 pending lock 存在時才進入真正生成。
# 此時上面的 st.button 已經先以 disabled=True 呈現。
# 因此使用者看到的是「不可按」的生成中按鈕，再開始 AI 工作。
# ------------------------------------------------------------
if st.session_state.get("v11_generation_pending", False):
    st.session_state["v11_generation_pending"] = False
    st.session_state["v11_generation_in_progress"] = True
    _generation_locked = True

    st.info("🔒 AI 生成進行中，本次操作已鎖定，請稍候……")

    _using_beta_credit = True
    _beta_job_id = str(st.session_state.get("beta_generation_job_id", "") or "")
    _beta_generation_returned = False
    _beta_live_session = None

    try:
        _beta_live_session = _beta_current_user()
        if not _beta_live_session or not _beta_job_id:
            raise RuntimeError("missing_beta_reservation")
        _server_openai_key = _beta_site_openai_key()
        if not _server_openai_key:
            raise RuntimeError("beta_generation_not_enabled")
        _generation_client = OpenAI(api_key=_server_openai_key)

        with st.spinner("AI 正在生成 4×2 原始總圖……"):
            ib = BytesIO(st.session_state.uploaded_image_bytes)
            # V11｜STEP 02C-4②
            # 等待提示是「等待體驗」，不代表 OpenAI 真實完成百分比。
            # 180 秒後逾時；不自動 retry，沿用原本的例外／退款路徑。
            _progress = st.progress(0)
            _status = st.empty()
            _started_at = time.monotonic()
            _progress.progress(0.05)
            _status.info("🎨 正在分析你的照片……")

            try:
                _progress.progress(0.18)
                _status.info("🖌️ 正在準備貼圖構圖……")

                # OpenAI SDK timeout：最長等待 180 秒。
                # V12｜透明背景連動測試版
                # 只有使用者勾選「使用透明背景 PNG」時，才送出 transparent 參數
                # 與透明 Alpha 強制提示；未勾選時完全不傳 background。
                _generation_prompt = prompt
                _image_edit_kwargs = {
                    # V12｜模型分流：一般背景沿用 gpt-image-2；
                    # 原生透明 PNG 使用已實測支援 Alpha 的 gpt-image-2.5-sunburst。
                    "model": "gpt-image-2.5-sunburst" if transparent else "gpt-image-2",
                    "image": ("person.png", ib, "image/png"),
                    "size": "1536x1024",
                    "output_format": "png",
                }

                if _style_reference_active:
                    _style_reference_bytes = bytes(
                        st.session_state.get("v12_style_reference_image_bytes") or b""
                    )
                    if not _style_reference_bytes:
                        raise RuntimeError("畫風參考圖已失效，請重新上傳後再生成。")
                    _image_edit_kwargs["image"] = [
                        ("person.png", ib, "image/png"),
                        ("style-reference.jpg", BytesIO(_style_reference_bytes), "image/jpeg"),
                    ]
                    _status.info("🖼️ 正在分析人物照片與畫風參考圖……")

                if transparent:
                    _generation_prompt += (
                        "\n\n【透明背景強制要求】"
                        "\n輸出必須是真正的透明 PNG Alpha 背景。"
                        "\n背景區域必須為 Alpha=0，不得繪製白色、灰色或任何棋盤格圖案。"
                        "\n絕對不要用棋盤格、灰白方格或任何圖案來模擬透明背景。"
                        "\n人物、物件與文字保留正常不透明像素，只有背景透明。"
                    )
                    _image_edit_kwargs["background"] = "transparent"

                _image_edit_kwargs["prompt"] = _generation_prompt
                result = _generation_client.with_options(timeout=180).images.edit(
                    **_image_edit_kwargs
                )

                # V11｜02C-4④｜異常回應資料防護
                # API 呼叫成功不代表一定取得有效圖片；空 data / 缺少 b64_json
                # 必須視為生成失敗，交回原本的 exception / refund 流程。
                if not getattr(result, "data", None):
                    raise RuntimeError("AI 回應成功，但沒有取得有效圖片資料，請重新操作。")
                
                _image_item = result.data[0]
                _b64_json = getattr(_image_item, "b64_json", None)
                if not _b64_json:
                    raise RuntimeError("AI 回應成功，但圖片資料不完整，請重新操作。")

                # From this point an image has been created successfully.  A
                # transient database acknowledgement failure must never turn
                # into an automatic refund for an image the member received.
                _beta_generation_returned = _using_beta_credit

                _progress.progress(0.88)
                _status.info("🖼️ 原始總圖處理中……")
                _progress.progress(0.96)
                _status.info("✂️ 正在準備 8 張貼圖……")
                _progress.progress(1.0)
                _status.success("🎉 AI 生成完成，正在進行最後圖片處理……")
            except Exception as _gen_exc:
                _elapsed = time.monotonic() - _started_at

                # 診斷修正版：只有真正等待達 180 秒以上，才顯示逾時。
                # 其他 OpenAI / SDK / 參數 / 權限 / 網路錯誤保留原始例外，
                # 避免所有錯誤都被誤包裝成 TimeoutError。
                if _elapsed >= 180:
                    _v11_clear_generation_state()
                    raise TimeoutError(
                        "AI 生成逾時（超過 3 分鐘），請重新操作。"
                    ) from _gen_exc

                # 在 Streamlit 畫面顯示診斷資訊，方便定位真正原因。
                st.error(
                    f"❌ AI 生成失敗｜{type(_gen_exc).__name__}: {_gen_exc}"
                )
                st.caption(f"診斷資訊：失敗發生於 {_elapsed:.1f} 秒；這不是 3 分鐘逾時。")
                raise
            raw = base64.b64decode(result.data[0].b64_json)
            img = Image.open(BytesIO(raw)).convert("RGBA")
            img, _grid_crop_room_applied = _v12_add_grid_breathing_room(
                img, shrink_ratio=0.88
            )
            st.session_state["v12_grid_breathing_room_applied"] = bool(
                _grid_crop_room_applied
            )
            if _grid_crop_room_applied:
                _status.info("✂️ 正在為每張貼圖保留裁切留白……")
            out = BytesIO()
            img.save(out, "PNG")
            st.session_state.generated_4x2_bytes = out.getvalue()
            st.session_state.crop_boxes = None
            if _sticker_mode == "expression":
                _v12_commit_random_expression_recipes()
            if _using_beta_credit:
                try:
                    _beta_finish_generation(_beta_live_session, _beta_job_id)
                    st.session_state.pop("beta_generation_job_id", None)
                except Exception:
                    st.warning("圖片已生成；生成次數完成紀錄正在同步，請勿重複按下生成。")
            if _grid_crop_room_applied:
                st.success("🎉 4×2 原始總圖生成成功！已自動保留每格裁切留白。")
            else:
                st.success("🎉 4×2 原始總圖生成成功！")

    except Exception as e:
        if _using_beta_credit and _beta_job_id and not _beta_generation_returned:
            try:
                if _beta_live_session:
                    _beta_finish_generation(
                        _beta_live_session, _beta_job_id, type(e).__name__
                    )
                st.session_state.pop("beta_generation_job_id", None)
            except Exception:
                st.warning("生成失敗，退點紀錄正在同步；請先不要重複嘗試。")

        st.error("❌ 生成失敗")
        st.caption(f"錯誤類型：{type(e).__name__}")

    finally:
        # 成功、失敗、額度不足、例外都解除鎖定。
        # 下一次正常 rerun 時，生成按鈕恢復可用。
        st.session_state["v11_generation_in_progress"] = False
        st.session_state["v11_generation_pending"] = False
        st.session_state["v11_generation_finished_at"] = __import__("time").time()
        for _expression_job_key in (
            "v12_expression_generation_specs",
            "v12_expression_generation_recipe_labels",
            "v12_expression_generation_new_cycle_start",
        ):
            st.session_state.pop(_expression_job_key, None)

# ------------------------------------------------------------
# STEP 10C native component
# ------------------------------------------------------------
# ------------------------------------------------------------
if (
    not st.session_state.generated_4x2_bytes
    and _v12_get_sticker_basket()
):
    st.divider()
    v10_section("🧺 STEP 11B｜已載回的精選作品", "#9b59b6")
    st.caption("這些是從「我的作品」載回的已選成品；可直接重新指定 MAIN／TAB 並打包，不會重新生成或扣除生成次數。")
    _v12_render_sticker_basket_workspace()


if st.session_state.generated_4x2_bytes:
    st.divider()
    v10_section("🖼️ ⑦ 檢視生成結果", "#32c6c0")
    st.caption("確認這張 4×2 總圖後，再往下微調每一格的裁切範圍並加入暫存作品籃。")
    st.divider()
    v10_section("✂️ ⑧ 直接用滑鼠調整 8 個裁切框", "#e74c3c")

    src = Image.open(BytesIO(st.session_state.generated_4x2_bytes)).convert("RGBA")
    w, h = src.size

    if st.session_state.crop_boxes is None:
        st.session_state.crop_boxes = base_boxes(w, h)

    st.info(
        "直接拖曳：移動裁切框。拖曳四角：改變大小。"
        "拖曳四邊：單方向調整。8 個框同時顯示，不需要逐張打開。"
    )

    # --------------------------------------------------------
    # 明確顯示原始 4×2 圖
    # 不依賴 Canvas 是否成功載入圖片。
    # 固定預覽寬度，避免隨網頁容器無限放大。
    # --------------------------------------------------------
    v10_subsection("🖼️ 原始 4×2 圖片", "#e74c3c")
    _orig_left, _orig_center, _orig_right = st.columns([1, 5, 1])
    with _orig_center:
        st.image(
            st.session_state.generated_4x2_bytes,
            caption=f"原始生成圖：{w} × {h} px",
            use_container_width=True,
        )

    st.caption(
        "上方是原始圖片預覽；下方 Canvas 才是可直接拖曳的裁切操作區。"
    )
    if st.session_state.get("v12_grid_breathing_room_applied", False):
        st.caption("✂️ 本批已自動將每格內容縮小 12% 並置中，保留較寬的裁切空間。")

    _CROP_HTML_TEMPLATE = '<!doctype html>\n<html><head><meta charset="utf-8">\n<style>\n*{box-sizing:border-box}\nbody{margin:0;font-family:Arial,"Microsoft JhengHei",sans-serif;color:#333}\n.help{font-size:14px;line-height:1.6;background:#f5f7fa;padding:10px;border-radius:8px}\n.viewer{position:relative;width:720px;max-width:100%;margin:10px auto 0;border:1px solid #bbb;border-radius:8px;overflow:hidden;background:#eee}\n#cv{display:block;width:720px;max-width:100%;height:auto;touch-action:none;user-select:none}\n.status{font-size:28px;line-height:1.45;font-weight:700;padding:12px 0;color:#fff;text-align:center}\n.actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}\nbutton{border:0;border-radius:8px;padding:9px 13px;cursor:pointer}\nbutton.primary{background:#222;color:#fff}\nbutton.lock{background:#333;color:#fff}\nbutton:disabled{opacity:.45;cursor:not-allowed}\n.coords{font:20px/1.6 Consolas,monospace;white-space:pre-wrap;background:#1b2028;color:#fff;padding:14px;border-radius:10px;text-align:center}\n.legend{font-size:26px;line-height:1.5;font-weight:700;padding:10px 0;color:#fff;text-align:center}\n</style></head><body>\n<div class="help">\n<b>📍 定位點裁切</b><br>\n3 條上半部＋3 條下半部垂直定位線＋1 條水平定位線。\n上下 6 條藍色垂直線可各自獨立拖曳；按「🔒 鎖定定位」後產生 01～08 裁切框。\n</div>\n<div class="viewer"><canvas id="cv"></canvas></div>\n<div class="status" id="status">正在建立定位線……</div>\n<div class="legend">🔴 固定外框\u3000🔵 上下 6 條獨立垂直分隔線\u3000🟠 水平分隔線\u3000｜\u3000🔒 鎖定後可進行裁切</div>\n<div class="actions">\n<button onclick="resetGuides()">↩️ 恢復分隔線位置</button>\n<button id="lockBtn" class="lock" onclick="toggleLock()">🔒 鎖定定位</button>\n<button id="downloadBtn" class="primary" onclick="downloadAll()" disabled>✂️ 裁切並依序下載01～08(手機下載)</button>\n</div>\n\n<script>\nconst B64="__IMAGE_B64__", IW=__IW__, IH=__IH__;\nconst cv=document.getElementById("cv"),ctx=cv.getContext("2d");\nconst statusEl=document.getElementById("status"),coordsEl=document.getElementById("coords");\nconst lockBtn=document.getElementById("lockBtn"),downloadBtn=document.getElementById("downloadBtn");\nconst colors=["#ff4040","#3987ff","#32ad61","#ff922e"];\nlet scale=1,locked=false,active=-1,startX=0,startY=0,snap=null;\n\nfunction clamp(v,a,b){return Math.max(a,Math.min(b,v))}\nfunction guidesDefault(){\n  return {\n    xTop:[IW/4,IW/2,3*IW/4],\n    xBottom:[IW/4,IW/2,3*IW/4],\n    y:[0,IH/2,IH]\n  };\n}\nlet g=guidesDefault();\n\nconst image=new Image();\nimage.src="data:image/png;base64,"+B64;\n\nfunction resize(){\n  const viewer=cv.parentElement;\n  const mw=Math.min(720,Math.max(320,viewer.clientWidth||720));\n  cv.width=Math.round(mw);\n  cv.height=Math.round(mw*IH/IW);\n  scale=cv.width/IW;\n  draw();\n}\n\nfunction boxes(){\n  const out=[];\n  const xt=[0,g.xTop[0],g.xTop[1],g.xTop[2],IW];\n  const xb=[0,g.xBottom[0],g.xBottom[1],g.xBottom[2],IW];\n  for(let c=0;c<4;c++) out.push([xt[c],g.y[0],xt[c+1],g.y[1]]);\n  for(let c=0;c<4;c++) out.push([xb[c],g.y[1],xb[c+1],g.y[2]]);\n  return out;\n}\n\nfunction draw(){\n  ctx.clearRect(0,0,cv.width,cv.height);\n  ctx.drawImage(image,0,0,cv.width,cv.height);\n\n  // Slight guide shading.\n  ctx.save();\n  ctx.fillStyle="rgba(255,255,255,.08)";\n  ctx.fillRect(0,0,cv.width,cv.height);\n  ctx.restore();\n\n  // 上下各 3 條：六條垂直線完全獨立。\n  for(let i=0;i<3;i++){\n    const xt=g.xTop[i]*scale;\n    const xb=g.xBottom[i]*scale;\n    ctx.save();\n    ctx.strokeStyle="#3987ff";\n    ctx.lineWidth=2.5;\n    ctx.setLineDash([10,7]);\n    ctx.beginPath();ctx.moveTo(xt,0);ctx.lineTo(xt,g.y[1]*scale);ctx.stroke();\n    ctx.beginPath();ctx.moveTo(xb,g.y[1]*scale);ctx.lineTo(xb,cv.height);ctx.stroke();\n    ctx.setLineDash([]);\n    ctx.fillStyle="#3987ff";\n    ctx.fillRect(xt-5,4,10,18);\n    ctx.fillRect(xb-5,g.y[1]*scale+4,10,18);\n    ctx.restore();\n  }\n\n  // 左右外框固定。\n  ctx.save();\n  ctx.strokeStyle="#ff4040";\n  ctx.lineWidth=2.5;\n  ctx.setLineDash([10,7]);\n  ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(0,cv.height);ctx.stroke();\n  ctx.beginPath();ctx.moveTo(cv.width,0);ctx.lineTo(cv.width,cv.height);ctx.stroke();\n  ctx.setLineDash([]);\n  ctx.fillStyle="#ff4040";\n  ctx.fillRect(0,4,10,18);\n  ctx.fillRect(cv.width-10,4,10,18);\n  ctx.restore();  // Draw 3 horizontal guide lines.\n  for(let i=0;i<3;i++){\n    const y=g.y[i]*scale;\n    ctx.save();\n    ctx.strokeStyle="#ff922e";\n    ctx.lineWidth=i===1?2.5:2;\n    ctx.setLineDash([10,7]);\n    ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(cv.width,y);ctx.stroke();\n    ctx.setLineDash([]);\n    ctx.fillStyle="#ff922e";\n    ctx.fillRect(4,y-5,22,10);\n    ctx.restore();\n  }\n\n  // Number each derived cell; these are guide labels only.\n  const b=boxes();\n  const cellColors=["#ff4040","#3987ff","#32ad61","#a04bd0","#ff922e","#25aeb0","#db3f9b","#956c3c"];\n  for(let i=0;i<8;i++){\n    const x=b[i][0]*scale,y=b[i][1]*scale;\n    ctx.save();\n    ctx.font="bold 17px Arial";\n    ctx.lineWidth=3;\n    ctx.strokeStyle="rgba(255,255,255,.95)";\n    ctx.strokeText(String(i+1).padStart(2,"0"),x+8,y+21);\n    ctx.fillStyle=cellColors[i];\n    ctx.fillText(String(i+1).padStart(2,"0"),x+8,y+21);\n    ctx.restore();\n  }\n\n  updateCoords();\n}\n\nfunction updateCoords(){\n  // 座標資訊已從使用者介面移除；保留函式避免影響既有裁切流程。\n}\n\nfunction pointerPos(e){\n  const r=cv.getBoundingClientRect();\n  return {x:(e.clientX-r.left)*IW/r.width,y:(e.clientY-r.top)*IH/r.height};\n}\n\nfunction nearestGuide(p){\n  const tol=Math.max(18,18/scale);\n  let best=-1,bd=Infinity,type="";\n  const arr=p.y<g.y[1] ? g.xTop : g.xBottom;\n  const segType=p.y<g.y[1] ? "xTop" : "xBottom";\n  for(let i=0;i<3;i++){\n    const d=Math.abs(p.x-arr[i]);\n    if(d<tol&&d<bd){best=i;bd=d;type=segType;}\n  }\n  const dy=Math.abs(p.y-g.y[1]);\n  if(dy<tol&&dy<bd){best=1;bd=dy;type="y";}\n  return {index:best,type:type};\n}cv.addEventListener("pointerdown",e=>{\n  if(locked)return;\n  const p=pointerPos(e),h=nearestGuide(p);\n  if(h.index<0)return;\n  e.preventDefault();\n  try{cv.setPointerCapture(e.pointerId)}catch(_){}\n  active=h.index;startX=p.x;startY=p.y;snap={xTop:[...g.xTop],xBottom:[...g.xBottom],y:[...g.y],type:h.type};\n  statusEl.textContent="🖱️ 正在調整定位線……";\n});\n\ncv.addEventListener("pointermove",e=>{\n  if(active<0||locked)return;\n  e.preventDefault();\n  const p=pointerPos(e);\n  if(snap.type==="xTop" || snap.type==="xBottom"){\n    const arr=snap.type==="xTop" ? g.xTop : g.xBottom;\n    const snapArr=snap.type==="xTop" ? snap.xTop : snap.xBottom;\n    const min=30;\n    const lo=(active===0 ? 0 : arr[active-1])+min;\n    const hi=(active===2 ? IW : arr[active+1])-min;\n    arr[active]=clamp(snapArr[active]+(p.x-startX),lo,hi);\n  }else{\n    const min=30;\n    g.y[1]=clamp(snap.y[1]+(p.y-startY),g.y[0]+min,g.y[2]-min);\n  }\n  draw();\n});\n\ncv.addEventListener("pointerup",e=>{\n  if(active>=0){try{cv.releasePointerCapture(e.pointerId)}catch(_){}}\n  active=-1;\n  if(!locked)statusEl.textContent="✅ 定位線已更新";\n});\n\nfunction resetGuides(){\n  locked=false;\n  g=guidesDefault();\n  lockBtn.textContent="🔒 鎖定定位";\n  downloadBtn.disabled=true;\n  statusEl.textContent="↩️ 已恢復標準4×2定位";\n  draw();\n}\n\nfunction toggleLock(){\n  locked=!locked;\n  if(locked){\n    lockBtn.textContent="🔓 解鎖定位";\n    downloadBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，可以開始裁切";\n  }else{\n    lockBtn.textContent="🔒 鎖定定位";\n    downloadBtn.disabled=true;\n    statusEl.textContent="🛠️ 定位已解鎖，可以繼續微調";\n  }\n  draw();\n}\n\nfunction saveBlob(blob,name){\n  const u=URL.createObjectURL(blob),a=document.createElement("a");\n  a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();\n  setTimeout(()=>URL.revokeObjectURL(u),1000);\n}\n\n// V10 STEP 10C.6：移植 V8「邊界連通背景移除」邏輯。\n// 只移除從圖片邊緣連通、且接近背景色的區域；不整片刪除人物/文字中的白色。\nfunction removeConnectedBackground(canvas, tolerance=30){\n  const ctx2=canvas.getContext("2d",{willReadFrequently:true});\n  const W=canvas.width,H=canvas.height;\n  const img=ctx2.getImageData(0,0,W,H);\n  const px=img.data;\n\n  const pts=[\n    [0,0],[W-1,0],[0,H-1],[W-1,H-1],\n    [Math.min(4,W-1),Math.min(4,H-1)],\n    [Math.max(0,W-5),Math.min(4,H-1)],\n    [Math.min(4,W-1),Math.max(0,H-5)],\n    [Math.max(0,W-5),Math.max(0,H-5)]\n  ];\n\n  let sr=0,sg=0,sb=0,count=0;\n  for(const [x,y] of pts){\n    const k=(y*W+x)*4;\n    if(px[k+3]===0) continue;\n    sr+=px[k]; sg+=px[k+1]; sb+=px[k+2]; count++;\n  }\n  if(count===0) return;\n\n  const bg=[sr/count,sg/count,sb/count];\n  const tol2=tolerance*tolerance;\n\n  function nearBg(k){\n    if(px[k+3]===0) return true;\n    const dr=px[k]-bg[0],dg=px[k+1]-bg[1],db=px[k+2]-bg[2];\n    return dr*dr+dg*dg+db*db<=tol2;\n  }\n\n  const seen=new Uint8Array(W*H);\n  const qx=[],qy=[];\n  let head=0;\n\n  function push(x,y){\n    if(x<0||x>=W||y<0||y>=H) return;\n    const n=y*W+x;\n    if(seen[n]) return;\n    seen[n]=1;\n    qx.push(x);qy.push(y);\n  }\n\n  for(let x=0;x<W;x++){push(x,0);push(x,H-1);}\n  for(let y=0;y<H;y++){push(0,y);push(W-1,y);}\n\n  while(head<qx.length){\n    const x=qx[head],y=qy[head++];\n    const k=(y*W+x)*4;\n    if(!nearBg(k)) continue;\n    px[k+3]=0;\n    push(x-1,y);push(x+1,y);push(x,y-1);push(x,y+1);\n  }\n\n  ctx2.putImageData(img,0,0);\n}\n\nfunction makeSticker(i){\n  const b=boxes()[i].map(v=>Math.round(v));\n  const x1=clamp(b[0],0,IW-2),y1=clamp(b[1],0,IH-2);\n  const x2=clamp(b[2],x1+2,IW),y2=clamp(b[3],y1+2,IH);\n  const cw=370,ch=320,sw=x2-x1,sh=y2-y1;\n  const rat=Math.min(cw/sw,ch/sh),nw=Math.max(1,Math.round(sw*rat)),nh=Math.max(1,Math.round(sh*rat));\n\n  const o=document.createElement("canvas");\n  o.width=cw;o.height=ch;\n  const octx=o.getContext("2d",{willReadFrequently:true});\n  octx.clearRect(0,0,cw,ch);\n\n  // 保留 V10 原本的定位點裁切、等比例縮放、置中。\n  octx.drawImage(\n    image,x1,y1,sw,sh,\n    Math.round((cw-nw)/2),Math.round((ch-nh)/2),nw,nh\n  );\n\n  // 只有勾選「透明背景 PNG」時才套用 V8 邏輯。\n  if(__TRANSPARENT__){\n    removeConnectedBackground(o,30);\n  }\n\n  return new Promise(r=>o.toBlob(r,"image/png"));\n}\n\nasync function downloadAll(){\n  if(!locked)return;\n  statusEl.textContent="⏳ 正在製作01～08 PNG……";\n  for(let i=0;i<8;i++){\n    saveBlob(await makeSticker(i),String(i+1).padStart(2,"0")+".png");\n    await new Promise(r=>setTimeout(r,180));\n  }\n  statusEl.textContent="🎉 01～08 已全部裁切並下載！";\n}\n\nimage.onload=()=>{\n  resize();\n  statusEl.textContent="✅ 定位線已建立：上 3 條＋下 3 條藍色垂直線可獨立拖曳";\n};\nwindow.addEventListener("resize",resize);\n</script></body></html>'
    # 本版不在 Canvas 直接下載；裁切結果會先加入下方暫存作品籃，再一次打包。
    _CROP_HTML_TEMPLATE = _CROP_HTML_TEMPLATE.replace(
        '<button id="downloadBtn" class="primary" onclick="downloadAll()" disabled>✂️ 裁切並依序下載01～08(手機下載)</button>',
        '',
    ).replace(
        'const lockBtn=document.getElementById("lockBtn"),downloadBtn=document.getElementById("downloadBtn");',
        'const lockBtn=document.getElementById("lockBtn");',
    ).replace(
        '  downloadBtn.disabled=true;\n',
        '',
    ).replace(
        '    downloadBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，可以開始裁切";',
        '    statusEl.textContent="🔒 定位已鎖定，可以加入下方暫存作品籃";',
    )
    _image_b64 = base64.b64encode(st.session_state.generated_4x2_bytes).decode("ascii")
    _boxes_json = __import__("json").dumps(st.session_state.crop_boxes, ensure_ascii=False)
    _crop_html = _CROP_HTML_TEMPLATE.replace("__IMAGE_B64__", _image_b64).replace("__IW__", str(int(w))).replace("__IH__", str(int(h))).replace("__BOXES__", _boxes_json).replace("__TRANSPARENT__", "true" if transparent else "false")
    import streamlit.components.v1 as components
    components.html(
        _crop_html,
        height=760,
        scrolling=False,
    )

    st.divider()
    v10_section("🧺 STEP 11B｜暫存作品籃與一次打包", "#9b59b6")
    st.markdown(
        '<div style="font-size:22px;line-height:1.55;font-weight:700;'
        'text-align:center;color:#fff;margin:10px 0 16px;">'
        '先將目前這批 8 張裁切圖加入暫存作品籃；可繼續生成更多批，最後再一次挑選、指定 MAIN／TAB 與打包下載。'
        '</div>',
        unsafe_allow_html=True,
    )
    if st.button(
        "✂️ 將目前這批 8 張加入暫存作品籃",
        type="primary",
        use_container_width=True,
        key="v12_add_current_grid_to_basket",
    ):
        try:
            added, message = _v12_add_current_grid_to_sticker_basket(
                st.session_state.get("generated_4x2_bytes")
            )
            if added:
                st.success(message)
                st.rerun()
            else:
                st.info(message)
        except Exception as error:
            st.error(f"加入暫存作品籃失敗：{error}")
    st.caption("暫存作品籃只保留在目前瀏覽工作階段；可累積不限張數，最後才會下載 ZIP。")
    _v12_render_sticker_basket_workspace()
    _basket_library_col = st.columns([1, 2, 1])[1]
    with _basket_library_col:
        if st.button(
            "📚 前往我的作品",
            key="v12_basket_library",
            type="primary",
            use_container_width=True,
        ):
            _public02a_sync_style_bank_from_widgets()
            _save_v10_presets()
            st.session_state["v12_view"] = "library"
            st.rerun()

    # 舊版 01～08 立即打包區保留在原始碼中作為回溯參考，但不再執行。
    # 新版以暫存作品籃作為唯一的裁切後工作流程。
    st.stop()


    # ============================================================
    # V10 STEP 11B.1
    # 直接從 01～08 選擇 MAIN / TAB＋一鍵打包
    #
    # 設計原則：
    # 1. 不重新上傳 01～08。
    # 2. 不修改 STEP 10C.8 的 AI 生成、透明、定位核心。
    # 3. 使用目前原始 4×2 圖＋定位座標直接在伺服器端裁切。
    # 4. main/tab 選擇與裁切在同一頁完成。
    # ============================================================
    st.divider()
    v10_section("⭐ STEP 11B｜選擇 MAIN / TAB（電腦版下載）", "#9b59b6")
    st.markdown(
        '<div style="font-size:28px;line-height:1.5;font-weight:700;'
        'text-align:center;color:#fff;margin:10px 0 22px;">'
        '調整好定位線後，直接指定哪一格做 MAIN、哪一格做 TAB。'
        '</div>',
        unsafe_allow_html=True,
    )

    # The browser editor keeps the guide coordinates in JS. For this first
    # stable version, provide a simple 01～08 selector tied to the standard
    # 4×2 positions. The final crop package still uses the original image.
    _main_tab_cols = st.columns(4)
    _main_tab_options = [f"{i:02d}" for i in range(1,9)]

    _v12_loaded_main = str(st.session_state.pop("v12_loaded_history_main_no", "") or "")
    _v12_loaded_tab = str(st.session_state.pop("v12_loaded_history_tab_no", "") or "")

    if _v12_loaded_main in _main_tab_options:
        st.session_state["step11b1_main"] = _v12_loaded_main
    elif "step11b1_main" not in st.session_state:
        st.session_state.step11b1_main = "01"

    if _v12_loaded_tab in _main_tab_options:
        st.session_state["step11b1_tab"] = _v12_loaded_tab
    elif "step11b1_tab" not in st.session_state:
        st.session_state.step11b1_tab = "02"

    with _main_tab_cols[0]:
        st.markdown("### ⭐ MAIN")
    with _main_tab_cols[1]:
        st.markdown("### 🏷️ TAB")
    with _main_tab_cols[2]:
        st.markdown("### 📦 完整套件")
    with _main_tab_cols[3]:
        st.markdown("### 🖼️ 透明背景")

    cmain, ctab = st.columns(2)
    with cmain:
        main_no = st.selectbox(
            "選擇 MAIN",
            _main_tab_options,
            index=_main_tab_options.index(st.session_state.step11b1_main),
            key="step11b1_main_select",
            help="此格會製作 main.png（240×240）"
        )
        st.session_state.step11b1_main = main_no

    with ctab:
        tab_choices = [x for x in _main_tab_options if x != main_no]
        if st.session_state.step11b1_tab not in tab_choices:
            st.session_state.step11b1_tab = tab_choices[0]
        tab_no = st.selectbox(
            "選擇 TAB",
            tab_choices,
            index=tab_choices.index(st.session_state.step11b1_tab),
            key="step11b1_tab_select",
            help="此格會製作 tab.png（96×74）"
        )
        st.session_state.step11b1_tab = tab_no

    st.info(
        f"目前設定：⭐ MAIN = {main_no}　｜　🏷️ TAB = {tab_no}　｜　"
        f"其他 6 張照正常 01～08 輸出"
    )

    # Server-side crop helper based on the current original 4x2 image.
    # The initial guide grid is standard 4x2; this is intentionally isolated
    # so the successful STEP 10C.8 browser editor is not changed.
    def _v11b1_crop_cell(sheet, idx):
        sw, sh = sheet.size
        col = idx % 4
        row = idx // 4
        x1 = round(col * sw / 4)
        x2 = round((col + 1) * sw / 4)
        y1 = round(row * sh / 2)
        y2 = round((row + 1) * sh / 2)

        crop = sheet.crop((x1, y1, x2, y2)).convert("RGBA")
        out = Image.new("RGBA", (370, 320), (255,255,255,0))
        scale = min(370 / crop.width, 320 / crop.height)
        nw = max(1, round(crop.width * scale))
        nh = max(1, round(crop.height * scale))
        crop = crop.resize((nw, nh), Image.Resampling.LANCZOS)
        out.alpha_composite(crop, ((370-nw)//2, (320-nh)//2))
        return out

    def _v11b1_special(im, size):
        out = Image.new("RGBA", size, (255,255,255,0))
        scale = min(size[0]/im.width, size[1]/im.height)
        nw=max(1,round(im.width*scale))
        nh=max(1,round(im.height*scale))
        im=im.resize((nw,nh),Image.Resampling.LANCZOS)
        out.alpha_composite(im,((size[0]-nw)//2,(size[1]-nh)//2))
        return out


    # ============================================================
    # V12｜STEP 01A②｜作品名稱
    # 放在最後打包按鈕正上方
    # ============================================================
    st.markdown("### 🏷️ 作品名稱")
    st.caption("此名稱會同時用於「我的作品」紀錄與 ZIP 下載檔案名稱。")
    st.text_input(
        "為這組作品命名",
        key="v12_project_name_input",
        placeholder="例如：阿土的日常貼圖",
        max_chars=80,
        on_change=_v12_project_name_on_change,
    )
    _v12_name_preview = _v12_project_snapshot().get("project_name", "")
    if not _v12_name_preview:
        st.caption(f"未填寫時，會自動使用：{_v12_default_project_name()}")

    _cloud_sync_session = _beta_current_user() if _beta_config() else None
    _save_to_cloud = st.checkbox(
        "☁️ 同步保存到雲端「我的作品」",
        value=False,
        key="v12_save_to_cloud_on_package",
        disabled=not bool(_cloud_sync_session),
    )
    if _cloud_sync_session:
        st.caption("勾選後會同步保存到雲端，需要等候較久時間，請勿重新整理網頁。")
    else:
        st.caption("登入會員後可選擇同步保存到雲端「我的作品」。未勾選時，作品僅暫存於本次瀏覽。")

    if st.button("📦 完成裁切＋製作貼圖檔案＋一鍵打包", type="primary", use_container_width=True):
        try:
            _sheet = Image.open(BytesIO(st.session_state.generated_4x2_bytes)).convert("RGBA")
            _zipbuf = BytesIO()

            with zipfile.ZipFile(_zipbuf, "w", zipfile.ZIP_DEFLATED) as _zip:
                cropped_images = {}
                for i in range(8):
                    im = _v11b1_crop_cell(_sheet, i)
                    cropped_images[i+1] = im
                    _b = BytesIO()
                    im.save(_b, "PNG", optimize=True)
                    _zip.writestr(f"{i+1:02d}.png", _b.getvalue())

                main_im = _v11b1_special(cropped_images[int(main_no)], (240,240))
                tab_im = _v11b1_special(cropped_images[int(tab_no)], (96,74))

                _mb=BytesIO(); _tb=BytesIO()
                main_im.save(_mb,"PNG",optimize=True,dpi=(72,72))
                tab_im.save(_tb,"PNG",optimize=True,dpi=(72,72))
                _zip.writestr("main.png",_mb.getvalue())
                _zip.writestr("tab.png",_tb.getvalue())

            _zipbuf.seek(0)

            # V12｜STEP 01A③：打包成功後自動加入「我的作品」暫存。
            _history_stickers = {}
            for _no, _im in cropped_images.items():
                _history_stickers[f"{_no:02d}"] = _v12_png_bytes(_im)

            _history_project = _v12_project_snapshot()
            _history_project["project_name"] = (
                _history_project.get("project_name") or _v12_default_project_name()
            )

            _history_record = _v12_history_record(
                metadata=_history_project,
                zip_bytes=_zipbuf.getvalue(),
                grid_bytes=st.session_state.get("generated_4x2_bytes"),
                main_bytes=_mb.getvalue(),
                tab_bytes=_tb.getvalue(),
                sticker_bytes=_history_stickers,
                main_no=main_no,
                tab_no=tab_no,
            )

            st.success(
                f"🎉 完成！MAIN={main_no}、TAB={tab_no}，"
                "已將 01～08＋main.png＋tab.png 打包，並加入「我的作品」。"
            )
            _cloud_session = _beta_current_user() if _save_to_cloud and _beta_config() else None
            if _save_to_cloud and _cloud_session:
                try:
                    _cloud_progress = st.progress(
                        0,
                        text="正在準備同步保存到雲端，請勿重新整理網頁…",
                    )
                    _cloud_save = _beta_save_library_project(
                        _cloud_session,
                        _history_record,
                        progress_callback=lambda progress, message: _cloud_progress.progress(
                            progress, text=message + " 請勿重新整理網頁。"
                        ),
                    )
                    if _cloud_save.get("saved"):
                        _cloud_progress.progress(100, text="雲端作品保存完成！")
                        st.success(
                            f"☁️ 已同步保存至雲端作品庫（{_cloud_save['saved_count']}／"
                            f"{_cloud_save['library_capacity']} 組）。"
                        )
                    else:
                        _cloud_progress.empty()
                        st.warning(
                            f"雲端作品庫已達 {_cloud_save['saved_count']}／"
                            f"{_cloud_save['library_capacity']} 組；請到「我的作品」下載並刪除一組後再保存。"
                        )
                except Exception:
                    st.warning("本次作品已保留在本機暫存，但雲端保存暫時未完成。請稍後重新打包再試。")
            elif _save_to_cloud:
                st.warning("雲端保存尚未執行：請重新登入會員後再試。")
            else:
                st.caption("本次未同步保存到雲端；作品仍可在本次瀏覽的「我的作品」中下載。")
            st.download_button(
                "⬇️ 下載完整 LINE 套件 ZIP",
                data=_zipbuf.getvalue(),
                file_name=f"{_v12_safe_download_stem(_v12_project_snapshot().get('project_name'))}.zip",
                mime="application/zip",
                use_container_width=True,
                key="step11b1_download"
            )
        except Exception as e:
            st.error(f"打包失敗：{e}")
    st.caption("")
st.divider()
st.caption("")


# ─────────────────────────────────────────────
# LINE 貼圖官方網站
# ─────────────────────────────────────────────
st.markdown("""
<style>
div[data-testid="stLinkButton"] > a,
div[data-testid="stLinkButton"] > a:visited,
div[data-testid="stLinkButton"] > a:hover,
div[data-testid="stLinkButton"] > a:active {
    width: 100% !important;
    min-height: 52px !important;
    border-radius: 10px !important;
    font-size: 18px !important;
    font-weight: 800 !important;
    display: flex !important;
    align-items: center !important;
    justify-content: center !important;
    background: #FF4B4B !important;
    background-color: #FF4B4B !important;
    background-image: none !important;
    color: #FFFFFF !important;
    border: 1px solid #FF4B4B !important;
    box-shadow: none !important;
    text-decoration: none !important;
    opacity: 1 !important;
}
div[data-testid="stLinkButton"] > a span,
div[data-testid="stLinkButton"] > a p {
    color: #FFFFFF !important;
    font-weight: 800 !important;
}
div[data-testid="stLinkButton"] > a:hover {
    background: #FF3333 !important;
    background-color: #FF3333 !important;
    border-color: #FF3333 !important;
    color: #FFFFFF !important;
}
</style>
""", unsafe_allow_html=True)

# ============================================================
# ============================================================
# V11｜STEP 02C-1 FIX4｜UI 主題交給 Streamlit 原生 Light/Dark
# 不在 Python 內硬覆蓋 App 背景；避免「文字已變白、背景仍是白色」。
# ============================================================
st.markdown("""
<style>
/* 只處理我們自己建立的舊版自訂元件；不接管整個 Streamlit App。 */
.v10-soft-box{
  background:var(--secondary-background-color, #fafafa) !important;
  border-color:var(--border-color, #e5e7eb) !important;
}

.v10-section-title,
.v10-subsection,
.v10-note{
  color:var(--text-color, inherit);
}

/* section 的彩色框線保留原本設計，不設定整頁背景。 */
</style>
""", unsafe_allow_html=True)



st.markdown('<div style="height: 20px;"></div>', unsafe_allow_html=True)
_link_col = st.columns([1, 2, 1])[1]
with _link_col:
    if st.button(
        "📚 我的作品",
        key="v12_footer_library",
        use_container_width=True,
    ):
        _public02a_sync_style_bank_from_widgets()
        _save_v10_presets()
        st.session_state["v12_view"] = "library"
        st.rerun()

    st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)

    st.link_button(
        "🔗 連結 LINE 貼圖網頁",
        "https://creator.line.me/zh-hant/",
        use_container_width=True,
    )


# ============================================================
# PUBLIC STEP 02A｜個人設定匯出／匯入
# ============================================================
def _public02a_settings_panel():
    """
    V12｜STEP 01B②｜個人設定區（重新整理版）

    設計原則：
    - 完全移除針對 Streamlit file_uploader 內部 DOM 的偽元素、文字覆蓋與強制定位。
    - 不再用 CSS 假裝一個檔案選擇按鈕。
    - 匯出使用 Streamlit 原生 download_button。
    - 匯入使用 Streamlit 原生 file_uploader。
    - 兩者共用同一個乾淨的卡片版型，讓功能與版面分離。
    """

    settings = _public02a_get_settings()
    export_data = json.dumps(
        settings,
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")

    # ------------------------------------------------------------
    # 區塊標題
    # ------------------------------------------------------------
    st.markdown(
        """
        <div class="v12-settings-shell">
            <div class="v12-settings-title">🔐 我的個人設定</div>
            <div class="v12-settings-subtitle">
                自定義風格、詞語與人物／場景需求只屬於目前使用者。
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ------------------------------------------------------------
    # 本區專用 CSS
    #
    # 注意：不再碰 file_uploader 內部 button / span / ::after。
    # 只控制「外部卡片」與原生 Streamlit 元件的正常寬度。
    # ------------------------------------------------------------
    st.markdown(
        """
        <style>
        .v12-settings-shell{
            max-width:760px;
            margin:28px auto 30px;
            padding:18px 22px;
            border-radius:16px;
            border:2px solid #4b5563;
            background:#151b26;
            text-align:center;
            box-sizing:border-box;
        }

        .v12-settings-title{
            font-size:28px;
            font-weight:800;
            line-height:1.25;
            color:#ffffff;
        }

        .v12-settings-subtitle{
            margin-top:8px;
            font-size:16px;
            line-height:1.5;
            color:#cbd5e1;
        }

        /* 只控制我們自己建立的兩張功能卡片。 */
        div.st-key-v12_settings_export_card,
        div.st-key-v12_settings_import_card{
            width:min(620px, calc(100vw - 2rem));
            max-width:620px;
            margin:0 auto 24px;
            padding:20px 24px;
            border:1px solid #394150;
            border-radius:16px;
            background:#1c222c;
            box-sizing:border-box;
        }

        div.st-key-v12_settings_export_card .v12-settings-card-heading,
        div.st-key-v12_settings_import_card .v12-settings-card-heading{
            margin:0 0 6px;
            text-align:center;
            font-size:22px;
            font-weight:800;
            line-height:1.35;
        }

        /*
        匯出按鈕只設定它自己的 Streamlit 元件。
        不影響 uploader。
        */
        div.st-key-v12_settings_export_card [data-testid="stDownloadButton"],
        div.st-key-v12_settings_export_card [data-testid="stDownloadButton"] > button{
            width:100%;
            box-sizing:border-box;
        }

        div.st-key-v12_settings_export_card [data-testid="stDownloadButton"] > button{
            min-height:48px;
            display:flex;
            align-items:center;
            justify-content:center;
            text-align:center;
            font-size:17px;
            font-weight:700;
        }

        /*
        匯入區完全保留 Streamlit 原生 uploader 的內部結構。
        只讓最外層在卡片內正常填滿。
        不設定 button::after、不隱藏 span、不做 absolute。
        */
        div.st-key-v12_settings_import_card [data-testid="stFileUploader"]{
            width:100%;
            box-sizing:border-box;
        }

        div.st-key-v12_settings_import_card [data-testid="stFileUploaderDropzone"]{
            width:100%;
            box-sizing:border-box;
        }

        @media (max-width:640px){
            .v12-settings-shell{
                width:calc(100vw - 1.25rem);
                margin:18px auto 22px;
                padding:16px 14px;
            }

            .v12-settings-title{
                font-size:24px;
            }

            .v12-settings-subtitle{
                font-size:15px;
            }

            div.st-key-v12_settings_export_card,
            div.st-key-v12_settings_import_card{
                width:calc(100vw - 1.25rem);
                padding:18px 14px;
                border-radius:14px;
            }

            div.st-key-v12_settings_export_card .v12-settings-card-heading,
            div.st-key-v12_settings_import_card .v12-settings-card-heading{
                font-size:20px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ============================================================
    # 📤 匯出卡片
    # ============================================================
    with st.container(key="v12_settings_export_card"):
        st.markdown(
            '<div class="v12-settings-card-heading">📤 匯出自定義</div>',
            unsafe_allow_html=True,
        )

        st.download_button(
            label="匯出自定義",
            data=export_data,
            file_name="LINE貼圖工具_自定義設定.json",
            mime="application/json",
            use_container_width=True,
            key="public02a_export_settings",
        )

    # ============================================================
    # 📥 匯入卡片
    # ============================================================
    with st.container(key="v12_settings_import_card"):
        st.markdown(
            '<div class="v12-settings-card-heading">📥 匯入自定義</div>',
            unsafe_allow_html=True,
        )

        # 使用原生 uploader，不再對它的內部 button 做文字或定位覆蓋。
        imported = st.file_uploader(
            "匯入自定義",
            type=["json"],
            key="public02a_import_settings",
            help="選擇之前匯出的 LINE貼圖工具_自定義設定.json",
        )

    # ============================================================
    # 匯入處理邏輯：保持原有功能
    # ============================================================
    if imported is not None:
        try:
            raw = imported.getvalue()
            import_hash = hashlib.sha256(raw).hexdigest()
            last_hash = st.session_state.get("public02a_last_import_hash")

            # Streamlit rerun 後會保留 uploader 值，
            # 只有新檔案才真正套用一次。
            if import_hash != last_hash:
                data = json.loads(raw.decode("utf-8"))
                _public02a_apply_settings(data)
                st.session_state["public02a_last_import_hash"] = import_hash
                st.session_state["public02a_import_success"] = True
                st.rerun()

            elif st.session_state.get("public02a_import_success"):
                st.success("✅ 自定義設定已匯入")

        except Exception as exc:
            st.error(f"❌ 匯入失敗：{exc}")


# PUBLIC STEP 02A｜顯示個人設定匯出／匯入
_public02a_settings_panel()
