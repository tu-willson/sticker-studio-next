# V11｜STEP 02D-3｜公開版最後操作細節整理
import re
import zipfile
import copy
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
import textwrap
from concurrent.futures import ThreadPoolExecutor
from i18n import LANGUAGES, tr
from step04_frontend_test import render_frontend_editor, render_frontend_generate_button


def _v12_ui_locale():
    """Return the active public UI locale without changing creation data."""
    locale = str(st.session_state.get("v12_ui_locale", "zh-TW") or "zh-TW")
    return locale if locale in LANGUAGES else "zh-TW"


def t(key, **kwargs):
    """Translate an explicit public UI key; unknown keys safely fall back to Chinese."""
    return tr(_v12_ui_locale(), key, **kwargs)


_V12_CROP_ROOM_TEXT_FALLBACKS = {
    "zh-TW": {
        "auto_crop_room": "逐格縮小至 86% 以保留裁切安全邊距",
        "auto_crop_room_help": "只影響下一次生成。勾選後，透明 PNG 的每一格會獨立縮小至 86% 並置中，讓你在後續手動調整裁切線時保有較多安全空間。未勾選則保留完整原圖。",
    },
    "en": {
        "auto_crop_room": "Scale each cell to 86% to preserve crop safety margins",
        "auto_crop_room_help": "Applies only to the next generation. When enabled, each transparent-PNG cell is independently scaled to 86% and centered to leave more room for manual crop-guide adjustments. When disabled, the original grid is kept intact.",
    },
    "ja": {
        "auto_crop_room": "各枠を86％に縮小して切り抜き安全余白を確保",
        "auto_crop_room_help": "次回の生成だけに適用されます。有効にすると透明PNGの各枠を個別に86％へ縮小して中央配置し、手動で切り抜きガイドを調整する余地を確保します。無効の場合は元画像をそのまま保持します。",
    },
}


def _v12_crop_room_t(key, **kwargs):
    """Translate crop-room UI, with a local fallback during Streamlit reloads."""
    translated = t(key, **kwargs)
    if translated != key:
        return translated
    fallback = _V12_CROP_ROOM_TEXT_FALLBACKS[_v12_ui_locale()][key]
    return fallback.format(**kwargs)


def _v12_slot_clear_label():
    """Keep the compact clear action independent from translation-module cache."""
    return {"zh-TW": "清空", "en": "Clear", "ja": "クリア"}[_v12_ui_locale()]


def _v12_localized_option(value, translations):
    """Display a localized label while retaining the stable internal option value."""
    return translations.get(str(value), {}).get(_v12_ui_locale(), str(value))


V12_AI_TONE_LABELS = {
    "😂 搞笑自然": {"en": "😂 Playful and natural", "ja": "😂 自然でおもしろい"},
    "😤 憤憤不平": {"en": "😤 Frustrated", "ja": "😤 不満たっぷり"},
    "😈 嘲諷吐槽": {"en": "😈 Sarcastic", "ja": "😈 皮肉・ツッコミ"},
    "☁️ 委屈可愛": {"en": "☁️ Cute and pouty", "ja": "☁️ しょんぼり可愛い"},
    "👑 霸氣有梗": {"en": "👑 Bold and witty", "ja": "👑 強気でウィットに富む"},
    "💬 台灣口語": {"en": "💬 Taiwanese casual", "ja": "💬 台湾カジュアル"},
    "✨ 溫暖療癒": {"en": "✨ Warm and comforting", "ja": "✨ あたたかく癒やし系"},
}

V12_TEXT_OUTLINE_LABELS = {
    "不使用外框": {"en": "No outline", "ja": "外枠なし"},
    "厚白貼紙描邊": {"en": "Thick white sticker outline", "ja": "太い白ステッカー縁"},
    "雙層撞色描邊": {"en": "Two-tone double outline", "ja": "ツートン二重縁"},
    "漫畫爆炸對話框": {"en": "Comic burst speech bubble", "ja": "漫画の爆発吹き出し"},
    "雲朵對話泡泡框": {"en": "Cloud speech bubble", "ja": "雲の吹き出し"},
    "圓角膠囊標籤框": {"en": "Rounded capsule label", "ja": "角丸カプセルラベル"},
    "鋸齒票券外框": {"en": "Perforated ticket frame", "ja": "ギザギザチケット枠"},
    "火焰爆裂外框": {"en": "Flame burst outline", "ja": "炎の爆裂枠"},
    "愛心泡泡外框": {"en": "Heart bubble outline", "ja": "ハート泡枠"},
    "速度線聚焦外框": {"en": "Speed-line focus outline", "ja": "集中線フォーカス枠"},
    "金屬徽章外框": {"en": "Metal badge outline", "ja": "メタルバッジ枠"},
    "彩虹波浪外框": {"en": "Rainbow wave outline", "ja": "虹の波形枠"},
    "塗鴉手繪外框": {"en": "Hand-drawn doodle outline", "ja": "手描き落書き枠"},
}

V12_RANDOM_POOL_LABELS = {
    "日常對話": {"en": "Everyday chat", "ja": "日常会話"}, "生活應用": {"en": "Everyday use", "ja": "生活シーン"},
    "時間問候": {"en": "Time greetings", "ja": "時間のあいさつ"}, "溫暖關愛": {"en": "Warm care", "ja": "やさしい気づかい"},
    "歡樂笑聲": {"en": "Laughter & fun", "ja": "笑い・楽しさ"}, "表現難過": {"en": "Feeling sad", "ja": "悲しい気持ち"},
    "祝賀對方": {"en": "Congratulations", "ja": "お祝い"}, "帶點懷疑": {"en": "A little doubtful", "ja": "ちょっと疑問"},
    "尷尬反應": {"en": "Awkward reactions", "ja": "気まずい反応"}, "驚訝震驚": {"en": "Surprise & shock", "ja": "驚き・衝撃"},
    "無言傻眼": {"en": "Speechless", "ja": "あきれ・無言"}, "誇張荒謬": {"en": "Ridiculous", "ja": "大げさ・ありえない"},
    "調皮搗蛋": {"en": "Playful teasing", "ja": "いたずら・からかい"}, "衷心感謝": {"en": "Heartfelt thanks", "ja": "心からの感謝"},
    "誠摯道歉": {"en": "Sincere apology", "ja": "心からの謝罪"}, "不必客氣": {"en": "You're welcome", "ja": "どういたしまして"},
    "時間行程": {"en": "Time & plans", "ja": "時間・予定"}, "交通出門": {"en": "On the way", "ja": "移動・外出"},
    "地點詢問": {"en": "Location questions", "ja": "場所の質問"}, "忙碌相關": {"en": "Busy life", "ja": "忙しいとき"},
    "用餐相關": {"en": "Food & dining", "ja": "食事"}, "休息睡覺": {"en": "Rest & sleep", "ja": "休憩・睡眠"},
    "天氣感受": {"en": "Weather feelings", "ja": "天気の感想"}, "注意提醒": {"en": "Reminders", "ja": "注意・リマインド"},
    "意見表達": {"en": "Opinions", "ja": "意見表明"}, "參與話題": {"en": "Joining in", "ja": "話題に参加"},
    "表態行動": {"en": "Taking action", "ja": "行動の表明"}, "約定承諾": {"en": "Promises", "ja": "約束・承諾"},
    "思考回應": {"en": "Thinking it over", "ja": "考え中の返事"}, "請求拜託": {"en": "Requests", "ja": "お願い"},
    "正向情緒": {"en": "Positive feelings", "ja": "前向きな気持ち"}, "負面情緒": {"en": "Negative feelings", "ja": "ネガティブな気持ち"},
    "緊張憤怒": {"en": "Nervous & angry", "ja": "緊張・怒り"}, "逃避現實": {"en": "Avoiding reality", "ja": "現実逃避"},
    "職場學習": {"en": "Work & study", "ja": "仕事・学習"}, "幽默趣味": {"en": "Humour & fun", "ja": "ユーモア"},
    "戀愛表達": {"en": "Love & affection", "ja": "恋愛表現"},
}

V12_FONT_NAME_TRANSLATIONS = {
    "en": {
        1:"Chubby sticker lettering",2:"Marshmallow round lettering",3:"Jelly bouncy lettering",4:"Butter cookie lettering",5:"Frosted dessert lettering",6:"Playful crayon lettering",7:"Soft cute handwriting",8:"Japanese planner round lettering",9:"Korean cute lettering",10:"Comic impact lettering",11:"Speech-bubble lettering",12:"White-edged sticker lettering",13:"Foam toy lettering",14:"Rubber pad lettering",15:"Glossy resin lettering",16:"Clear acrylic lettering",17:"Enamel badge lettering",18:"Embroidered patch lettering",19:"Wool-felt lettering",20:"Embossed leather lettering",21:"Layered paper-cut lettering",22:"3D origami lettering",23:"Crackle-glaze ceramic lettering",24:"Jade relief lettering",25:"Marble-carved lettering",26:"Silver-steel relief lettering",27:"3D gold lettering",28:"Vintage bronze lettering",29:"Clear ice-crystal lettering",30:"Crystal glass lettering",31:"Neon glow lettering",32:"Hong Kong neon lettering",33:"Bulb-sign lettering",34:"Wood-carved lettering",35:"Heavy stone-carved lettering",36:"Brush-calligraphy lettering",37:"Seal-carving lettering",38:"Paper-cut art lettering",39:"Vintage poster lettering",40:"Vintage typewriter lettering",41:"Trendy graffiti lettering",42:"Street graffiti lettering",43:"Comic explosion lettering",44:"Hot-blooded manga lettering",45:"Hand-drawn doodle lettering",46:"Minimal handwriting",47:"Romantic handwriting",48:"Chalkboard lettering",49:"Sacred radiant lettering",50:"Dark Gothic lettering",51:"Handmade clay lettering",52:"Patchwork-stitch lettering",53:"Pearl-shell lettering",54:"Flowing feather lettering",55:"Bamboo-weave lettering",56:"Straw-weave lettering",57:"Moss-forest lettering",58:"Vine-garden lettering",59:"Petal-collage lettering",60:"Watercolour bloom lettering",61:"Ink-wash lettering",62:"Colour-pencil texture lettering",63:"Batik-fabric lettering",64:"Mosaic-tile lettering",65:"Flocked-velvet lettering",66:"Steampunk gear lettering",67:"Pixel-game lettering",68:"8-bit block lettering",69:"Glitch digital lettering",70:"Holographic laser lettering",71:"Fluffy cloud lettering",72:"Sea-salt grain lettering",73:"Coral-ocean lettering",74:"Dreamy star-sand lettering",75:"Cosmic nebula lettering",76:"Chocolate-syrup lettering",77:"Popcorn-snack lettering",78:"Cotton-linen weave lettering",79:"Denim-patch lettering",80:"Lace-trim lettering",81:"Button-collage lettering",82:"Crochet lettering",83:"Stained-glass cathedral lettering",84:"Iron-rivet lettering",85:"Rusted industrial lettering",86:"Circuit-chip lettering",87:"LCD-screen lettering",88:"Radar-scan lettering",89:"Rubber-print lettering",90:"Oil-paint brushstroke lettering",91:"Film-reel lettering",92:"Newspaper-collage lettering",93:"Kraft-paper packaging lettering",94:"Dewdrop leaf-vein lettering",95:"Firefly-forest lettering",96:"Constellation fortune-telling lettering",97:"Herbal-apothecary lettering",98:"Bubble-tea bouncy lettering",99:"Sushi toy-food lettering",100:"Donut-sprinkle lettering",101:"Ink-brush lettering",102:"Running-script lettering",103:"Expressive splash-ink lettering",104:"Seal-stamp lettering",105:"Fountain-pen flow lettering",106:"Vintage typewriter lettering",107:"Hand-drawn chalk lettering",108:"Rainbow hand-drawn lettering",109:"Graffiti hand-drawn lettering",110:"3D sticker lettering",111:"3D balloon lettering",112:"Fabric-collage lettering",113:"Grass-and-foliage lettering",114:"Beach-sand lettering",115:"Cloud-cotton lettering",116:"Frost-crack lettering",117:"Lava cracked-rock lettering",118:"Corroded-metal lettering",119:"Cracked dry-earth lettering",120:"Diamond-cut lettering",121:"Laser-iridescent lettering",122:"Liquid-metal lettering",123:"Neon-tube lettering",124:"Vintage-lightbulb lettering",125:"Pixel-dot lettering",
    },
    "ja": {
        1:"ぽってりステッカー文字",2:"マシュマロ丸文字",3:"ぷるぷるゼリー文字",4:"バタークッキー文字",5:"アイシングデザート文字",6:"クレヨンポップ文字",7:"やわらか手書き文字",8:"日本手帳風丸文字",9:"韓国キュート文字",10:"漫画インパクト文字",11:"吹き出し文字",12:"白ふちステッカー文字",13:"スポンジ玩具文字",14:"ラバーパッド文字",15:"つやつやレジン文字",16:"透明アクリル文字",17:"エナメルバッジ文字",18:"刺繍ワッペン文字",19:"羊毛フェルト文字",20:"レザー型押し文字",21:"重ね紙切り絵文字",22:"立体折り紙文字",23:"貫入陶器文字",24:"翡翠レリーフ文字",25:"大理石彫刻文字",26:"銀鋼レリーフ文字",27:"立体ゴールド文字",28:"ヴィンテージブロンズ文字",29:"氷晶クリア文字",30:"クリスタルガラス文字",31:"ネオン発光文字",32:"香港ネオン文字",33:"電球看板文字",34:"木彫り質感文字",35:"重厚な石彫り文字",36:"筆文字書道",37:"篆刻文字",38:"切り絵アート文字",39:"ヴィンテージポスター文字",40:"レトロタイプライター文字",41:"トレンド落書き文字",42:"ストリートグラフィティ文字",43:"漫画爆発文字",44:"熱血漫画文字",45:"手描き落書き文字",46:"ミニマル手書き文字",47:"ロマンティック手書き文字",48:"チョーク黒板文字",49:"神聖な光彩文字",50:"ダークゴシック文字",51:"ハンドメイド粘土文字",52:"パッチワーク縫い目文字",53:"真珠貝文字",54:"羽根の流麗文字",55:"竹編み工芸文字",56:"わら編み文字",57:"苔の森文字",58:"つる草庭園文字",59:"花びらコラージュ文字",60:"水彩にじみ文字",61:"墨彩にじみ文字",62:"色鉛筆テクスチャ文字",63:"ろうけつ染め布文字",64:"モザイクタイル文字",65:"フロッキー起毛文字",66:"スチームパンク歯車文字",67:"ピクセルゲーム文字",68:"8ビットブロック文字",69:"グリッチデジタル文字",70:"ホログラムレーザー文字",71:"ふわふわ雲文字",72:"海塩粒子文字",73:"サンゴ海洋文字",74:"夢幻星砂文字",75:"宇宙星雲文字",76:"チョコレートシロップ文字",77:"ポップコーン文字",78:"綿麻織り文字",79:"デニムパッチ文字",80:"レース縁取り文字",81:"ボタンコラージュ文字",82:"かぎ針編み文字",83:"ステンドグラス教会文字",84:"鉄リベット文字",85:"さびた工業文字",86:"回路チップ文字",87:"液晶画面文字",88:"レーダースキャン文字",89:"ゴム印刷文字",90:"油彩筆致文字",91:"フィルム映画文字",92:"新聞コラージュ文字",93:"クラフト紙包装文字",94:"露の葉脈文字",95:"ホタルの森文字",96:"星座占い文字",97:"ハーブ薬店文字",98:"タピオカもちもち文字",99:"寿司食品サンプル文字",100:"ドーナツシュガー文字",101:"水墨筆文字",102:"行書連筆文字",103:"潑墨写意文字",104:"篆刻印章文字",105:"万年筆流線文字",106:"レトロタイプライター文字",107:"手描きチョーク文字",108:"虹色手描き文字",109:"落書き手描き文字",110:"立体ステッカー文字",111:"立体風船文字",112:"布コラージュ文字",113:"芝生と植物文字",114:"砂浜の砂文字",115:"雲わた文字",116:"霜のひび割れ文字",117:"溶岩ひび岩文字",118:"腐食メタル文字",119:"乾いた土のひび文字",120:"ダイヤカット文字",121:"レーザー虹彩文字",122:"液体金属文字",123:"ネオン管文字",124:"レトロ電球文字",125:"ピクセルドット文字",
    },
}


def _v12_font_display_name(font_number):
    """Use translated font names in the UI but preserve original names for generation."""
    font_number = int(font_number)
    return V12_FONT_NAME_TRANSLATIONS.get(_v12_ui_locale(), {}).get(
        font_number, V8_TEXT_EFFECT_CATALOG[font_number]
    )


def _v12_basket_batch_display(batch_label):
    """Translate app-owned batch labels without changing custom project names."""
    raw_label = str(batch_label or "")
    if raw_label == "作品集":
        return t("basket_project")
    match = re.fullmatch(r"批次\s*(\d+)", raw_label)
    if match:
        return t("basket_batch", number=int(match.group(1)))
    return raw_label


def _v12_font_choice_display(option):
    """Render a translated 125-font option while leaving its session value stable."""
    raw_option = str(option)
    if raw_option == "— 快速選擇字型｜可直接輸入數字 —":
        return t("font_quick_placeholder")
    try:
        font_number = int(raw_option.split("｜", 1)[0])
        return f"{font_number:03d}｜{_v12_font_display_name(font_number)}"
    except (ValueError, KeyError):
        return raw_option


def _v12_generation_suffix():
    """Keep compact generation-plan wording natural in each public language."""
    return {"zh-TW": "／月", "en": " / month", "ja": "／月"}[_v12_ui_locale()]


def _v12_crop_canvas_translations():
    """Translate the isolated crop Canvas UI without translating project content."""
    locale = _v12_ui_locale()
    if locale == "en":
        return {
            "📍 定位點裁切": "📍 Crop guides",
            "3 條上半部＋3 條下半部垂直定位線＋1 條水平定位線。": "3 upper and 3 lower vertical guides, plus 1 horizontal guide.",
            "上下 6 條藍色垂直線可各自獨立拖曳；按「🔒 鎖定定位」後產生 01～08 裁切框。": "The six blue vertical guides can be dragged independently. Lock the guides to prepare crop frames 01–08.",
            "正在建立定位線……": "Creating crop guides…",
            "🔴 固定外框　🔵 上下 6 條獨立垂直分隔線　🟠 水平分隔線　｜　🔒 鎖定後可進行裁切": "🔴 Fixed outer frame　🔵 6 independent vertical guides　🟠 Horizontal guide　｜　🔒 Lock, then crop",
            "↩️ 恢復分隔線位置": "↩️ Reset guides",
            "🔒（第一步）鎖定定位": "🔒 Step 1: Lock guides",
            "✅（第二步）套用裁切並加入作品暫存區": "✅ Step 2: Apply crops and add to workspace",
            "🖱️ 正在調整定位線……": "🖱️ Adjusting guides…",
            "✅ 定位線已更新": "✅ Guides updated",
            "↩️ 已恢復標準4×2定位": "↩️ Standard 4×2 guides restored",
            "🔓 解鎖定位": "🔓 Unlock guides",
            "🔒 定位已鎖定，可以加入下方作品暫存區": "🔒 Guides locked. You can add this batch to the workspace below.",
            "🔒 定位已鎖定，正在儲存裁切範圍……": "🔒 Guides locked. Saving the crop areas…",
            "⚠️ 無法儲存裁切範圍，請重新整理後再試": "⚠️ The crop areas could not be saved. Please refresh and try again.",
            "🛠️ 定位已解鎖，可以繼續微調": "🛠️ Guides unlocked. You can keep fine-tuning.",
            "🔒 定位已鎖定，請套用裁切範圍後加入作品籃": "🔒 Guides locked. Apply the crop areas to add them to the workspace.",
            "🔒 正在套用裁切範圍並加入作品籃……": "🔒 Applying crop areas and adding them to the workspace…",
            "✅ 定位線已建立：上 3 條＋下 3 條藍色垂直線可獨立拖曳": "✅ Guides ready: the 3 upper and 3 lower blue vertical lines can be dragged independently.",
        }
    if locale == "ja":
        return {
            "📍 定位點裁切": "📍 切り抜きガイド",
            "3 條上半部＋3 條下半部垂直定位線＋1 條水平定位線。": "上段3本＋下段3本の垂直ガイドと、1本の水平ガイド。",
            "上下 6 條藍色垂直線可各自獨立拖曳；按「🔒 鎖定定位」後產生 01～08 裁切框。": "上下6本の青い垂直ガイドは個別にドラッグできます。ガイドをロックすると01～08の切り抜き枠を準備できます。",
            "正在建立定位線……": "切り抜きガイドを作成中…",
            "🔴 固定外框　🔵 上下 6 條獨立垂直分隔線　🟠 水平分隔線　｜　🔒 鎖定後可進行裁切": "🔴 固定外枠　🔵 6本の独立した垂直ガイド　🟠 水平ガイド　｜　🔒 ロック後に切り抜き可能",
            "↩️ 恢復分隔線位置": "↩️ ガイドをリセット",
            "🔒（第一步）鎖定定位": "🔒 手順1：ガイドをロック",
            "✅（第二步）套用裁切並加入作品暫存區": "✅ 手順2：切り抜きを適用して作業かごに追加",
            "🖱️ 正在調整定位線……": "🖱️ ガイドを調整中…",
            "✅ 定位線已更新": "✅ ガイドを更新しました",
            "↩️ 已恢復標準4×2定位": "↩️ 標準4×2ガイドに戻しました",
            "🔓 解鎖定位": "🔓 ガイドをロック解除",
            "🔒 定位已鎖定，可以加入下方作品暫存區": "🔒 ガイドをロックしました。下の作業かごにこのバッチを追加できます。",
            "🔒 定位已鎖定，正在儲存裁切範圍……": "🔒 ガイドをロックしました。切り抜き範囲を保存中…",
            "⚠️ 無法儲存裁切範圍，請重新整理後再試": "⚠️ 切り抜き範囲を保存できませんでした。更新してもう一度お試しください。",
            "🛠️ 定位已解鎖，可以繼續微調": "🛠️ ガイドのロックを解除しました。引き続き微調整できます。",
            "🔒 定位已鎖定，請套用裁切範圍後加入作品籃": "🔒 ガイドをロックしました。切り抜きを適用して作業かごに追加してください。",
            "🔒 正在套用裁切範圍並加入作品籃……": "🔒 切り抜きを適用して作業かごに追加中…",
            "✅ 定位線已建立：上 3 條＋下 3 條藍色垂直線可獨立拖曳": "✅ ガイドを作成しました。上3本と下3本の青い垂直線は個別にドラッグできます。",
        }
    return {}


def _v12_pricing_rules_markdown():
    """Public checkout rules, kept separate so they can be fully localized."""
    locale = _v12_ui_locale()
    if locale == "en":
        return textwrap.dedent("""
        - Each successfully generated **4×2 original sticker grid** uses **1 image generation**. Failed generations do not use a generation.
        - The AI Ideas allowance resets daily in Taiwan time and is separate from image generations.
        - A one-time generation pack does not renew automatically and is valid for 30 days.
        - A subscription starts on its successful-payment date. Unused subscription generations carry over until the end of the following billing cycle; the earliest-expiring generations are used first.

        > **Example:** If you start Basic on May 10 with 20 generations and have 5 left on June 9, 20 more are added on June 10, giving you 25. The carried-over 5 are available until July 9 and are used first.

        - After cancellation, current benefits remain available until the billing cycle ends and there is no charge for the next cycle. If saved projects exceed the new plan's limit, nothing is deleted automatically, but you must remove projects before saving another.

        > **Example:** For a May 10–June 9 cycle, cancelling on May 20 keeps your generation, AI Ideas, and library benefits until June 9. There is no renewal charge or new allowance on June 10.

        - Each project can save up to 40 selected stickers; MAIN/TAB export images do not count. ZIP files are not stored and can be created again after loading the project back into the studio.
        """).strip()
    if locale == "ja":
        return textwrap.dedent("""
        - **4×2 の元ステッカー総画像**を1回正常に生成すると、画像生成回数を **1回** 使用します。生成失敗時は回数を消費しません。
        - AIアイデアの利用枠は台湾時間で毎日計算され、画像生成回数とは別に管理されます。
        - 単発の生成パックは自動更新されず、購入後30日間有効です。
        - サブスクリプションは決済成功日から開始します。未使用の生成回数は次の請求期間の終了まで繰り越せ、期限の早い回数から使用されます。

        > **例：**5月10日にベーシックで20回分を取得し、6月9日に5回残っている場合、6月10日にさらに20回分が追加され合計25回になります。繰り越した5回分は7月9日まで利用でき、先に使用されます。

        - 解約後も当月の特典は請求期間の終了まで使え、次回の請求は行われません。作品数が新しいプラン上限を超えても自動削除はされませんが、新規保存前にご自身で整理が必要です。

        > **例：**5月10日から6月9日までの期間に5月20日に解約した場合、6月9日までは画像生成、AIアイデア、作品庫の特典を利用できます。6月10日に更新請求や新しい利用枠の追加はありません。

        - 1作品につき選択したステッカーを最大40枚保存できます。MAIN／TABの書き出し画像は上限に含まれません。ZIPは保存されず、作品を制作ページへ読み戻して再作成できます。
        """).strip()
    return textwrap.dedent("""
    - 每次成功生成 1 張 **4×2 原始貼圖總圖**，扣除 1 次圖片生成次數；生成失敗不扣次數。
    - AI幫想額度按台灣時間每日計算，與圖片生成次數分開計算。
    - 訂閱方案每月自付款成功日生效；未使用的訂閱生成次數可保留至下一個帳務週期結束，系統優先使用最早到期的次數。

    > **例子：**你在 5 月 10 日訂閱基本方案，取得 20 次圖片生成次數；到了 6 月 9 日還剩 5 次。6 月 10 日進入新一期後會再加入 20 次，因此共有 25 次可用；原本保留的 5 次最晚可用到 7 月 9 日，系統會優先使用這 5 次。

    - 單次生成包為一次性購買，不會自動續訂，購買後有效期為 30 天。
    - 取消訂閱後，當期權益可使用至帳務週期結束，下一期不再續扣。作品集保存總數超過新方案上限時，不會自動刪除，須自行刪減後才能新增保存。

    > **例子：**你的訂閱週期是 5 月 10 日至 6 月 9 日；若在 5 月 20 日取消，仍可使用當期的圖片生成次數、AI幫想額度與作品庫權益至 6 月 9 日。6 月 10 日起不會再扣款，也不會發放新一期的方案額度。

    - 每組作品最多保存 40 張已選貼圖；MAIN／TAB 規格圖不計入上限。ZIP 檔不會保存，可載回創作頁後重新下載。
    """).strip()


def _v12_member_rules_markdown():
    """Expanded member-rule content; the checkout version stays compact."""
    locale = _v12_ui_locale()
    if locale == "zh-TW":
        return """#### 🎨 服務內容

Sticker Studio Next 是一項 AI 輔助 LINE 貼圖創作服務。會員可上傳人物照片、選擇貼圖風格或上傳畫風參考，設定貼圖文字與動作後，產生 4×2 貼圖總圖；完成裁切後，可挑選單張貼圖下載或保存至雲端作品庫。

#### 💳 方案與提供額度

| 方案 | 月費／售價 | 每月圖片生成次數 | AI幫想每日額度 | 雲端作品庫上限 |
| --- | ---: | ---: | ---: | ---: |
| 基本方案 | NT$99／月 | 20 次 | 15 次 | 20 組 |
| 標準方案 | NT$149／月 | 30 次 | 20 次 | 30 組 |
| 進階方案 | NT$199／月 | 40 次 | 25 次 | 40 組 |
| 單次生成包 | NT$49／包 | 7 次 | 7 次 | 7 組 |

#### ✨ 圖片生成次數與使用方式

""" + _v12_pricing_rules_markdown() + """

#### ☁️ 雲端作品庫與下載

- 每組作品可保存原始人物照片、已上傳的畫風參考與使用者選取的貼圖成品；單組最多保存 **40 張貼圖**，**MAIN／TAB** 規格圖不計入此上限。
- 未勾選保存的裁切貼圖不會寫入作品庫；ZIP 壓縮檔不會另行保存，可在日後將作品載回創作頁後重新下載。
- 作品庫容量依目前有效方案計算；免費會員預設可保存 5 組作品，訂閱或單次生成包則依方案權益提供容量。

#### 📌 重要提醒

本服務提供數位化 AI 創作與雲端保存功能。使用者須確認上傳照片、參考圖、文字與生成內容具有合法使用權，且不侵害他人權利。正式付款頁面將同步揭示付款、續訂、取消與退款相關說明。"""
    if locale == "en":
        return """#### 🎨 What the service provides

Sticker Studio Next is an AI-assisted LINE sticker creation service. Members can upload a character photo, choose a sticker style or upload a style reference, set text and actions, generate a 4×2 sticker grid, then download selected stickers individually or save them to their cloud library.

#### 💳 Plans and allowances

| Plan | Price | Monthly image generations | Daily AI Ideas | Cloud projects |
| --- | ---: | ---: | ---: | ---: |
| Basic | NT$99/month | 20 | 15 | 20 |
| Standard | NT$149/month | 30 | 20 | 30 |
| Advanced | NT$199/month | 40 | 25 | 40 |
| One-time pack | NT$49/pack | 7 | 7 | 7 |

#### ✨ Image generations and use

""" + _v12_pricing_rules_markdown() + """

#### ☁️ Cloud library and downloads

- A project can save the original character photo, any uploaded style reference, and selected sticker results. Each project supports up to **40 stickers**; **MAIN/TAB** export images do not count.
- Cropped stickers not selected for saving are not written to the library. ZIP files are not stored; load the project back into the studio to create another download.
- Capacity follows the current active benefit. Free members can save 5 projects; subscriptions and one-time packs provide the capacity shown above.

#### 📌 Important notice

This service provides digital AI creation and cloud storage. You are responsible for having the rights to use every photo, reference, text, and generated result. Payment, renewal, cancellation, and refund details will appear on the checkout page."""
    return """#### 🎨 サービス内容

Sticker Studio Next は、AI を活用した LINE ステッカー制作サービスです。人物写真のアップロード、スタイル選択または参考画像のアップロード、テキストと動作の設定を行い、4×2 のステッカー総画像を生成できます。切り抜き後は単体ダウンロードまたはクラウド作品庫への保存が可能です。

#### 💳 プランと利用枠

| プラン | 料金 | 月間画像生成 | AIアイデア／日 | クラウド作品 |
| --- | ---: | ---: | ---: | ---: |
| ベーシック | NT$99／月 | 20回 | 15回 | 20組 |
| スタンダード | NT$149／月 | 30回 | 20回 | 30組 |
| アドバンス | NT$199／月 | 40回 | 25回 | 40組 |
| 単発パック | NT$49／パック | 7回 | 7回 | 7組 |

#### ✨ 画像生成と利用方法

""" + _v12_pricing_rules_markdown() + """

#### ☁️ クラウド作品庫とダウンロード

- 作品には人物写真、アップロードした画風参考、選択したステッカー成品を保存できます。1作品につき最大 **40枚** 保存でき、**MAIN／TAB** は上限に含まれません。
- 保存を選択しなかった切り抜きステッカーは作品庫へ保存されません。ZIP は保存されませんが、作品を制作ページに読み戻して再ダウンロードできます。
- 保存容量は現在有効な特典に応じます。無料会員は5作品、サブスクリプションと単発パックは上表の容量を利用できます。

#### 📌 ご注意

本サービスはデジタル AI 制作とクラウド保存を提供します。アップロードする写真、参考画像、テキスト、生成結果について、必要な利用権をお持ちであることをご確認ください。決済、更新、解約、返金の詳細は決済ページに表示されます。"""

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
        slot = 0

    # 0 是「點擊選擇」提示項，不把它偷偷當成第 01 組載入。
    if slot not in range(1, 11):
        st.session_state["v12_style_editor_name"] = ""
        st.session_state["v12_style_editor_prompt"] = ""
        return

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

# The crop editor needs to return the final guide coordinates to Python.
# `components.html` is display-only, so use a small first-party Streamlit
# component as a bridge instead of trying to navigate the enclosing page.
_V12_CROP_GUIDES_COMPONENT = components.declare_component(
    "v12_crop_guides",
    path=str(Path(__file__).with_name("crop_guides_component")),
)

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
/* 文字貼圖每格的清空操作維持低干擾：只容納兩字、採淡透明藍色。 */
.st-key-v12_clear_sticker_slot_0 button,
.st-key-v12_clear_sticker_slot_1 button,
.st-key-v12_clear_sticker_slot_2 button,
.st-key-v12_clear_sticker_slot_3 button,
.st-key-v12_clear_sticker_slot_4 button,
.st-key-v12_clear_sticker_slot_5 button,
.st-key-v12_clear_sticker_slot_6 button,
.st-key-v12_clear_sticker_slot_7 button,
[class*="st-key-v12_clear_expression_slot_"] button{
  min-height:34px !important;
  min-width:44px !important;
  padding:0 5px !important;
  border:1px solid rgba(104,190,255,.42) !important;
  border-radius:8px !important;
  background:rgba(76,165,232,.16) !important;
  color:#a9dcff !important;
  font-size:13px !important;
  font-weight:800 !important;
  white-space:nowrap !important;
  box-shadow:none !important;
}
.st-key-v12_clear_sticker_slot_0 button:hover,
.st-key-v12_clear_sticker_slot_1 button:hover,
.st-key-v12_clear_sticker_slot_2 button:hover,
.st-key-v12_clear_sticker_slot_3 button:hover,
.st-key-v12_clear_sticker_slot_4 button:hover,
.st-key-v12_clear_sticker_slot_5 button:hover,
.st-key-v12_clear_sticker_slot_6 button:hover,
.st-key-v12_clear_sticker_slot_7 button:hover,
[class*="st-key-v12_clear_expression_slot_"] button:hover{
  background:rgba(76,165,232,.28) !important;
  border-color:rgba(146,212,255,.7) !important;
}
/* 01～04 與 05～08 之間的輕量分隔，避免閱讀時誤認成同一排。 */
.v12-slot-row-divider{
  height:1px;
  margin:18px 0 16px;
  background:linear-gradient(90deg, transparent, rgba(183,220,255,.55), transparent);
  box-shadow:0 1px 5px rgba(148,206,255,.14);
}
/* 自定義風格是貼圖風格快選的模式切換，需比一般勾選更醒目。 */
.st-key-v12_use_custom_style [data-testid="stCheckbox"] label p{
  font-size:20px !important;
  font-weight:850 !important;
}
.st-key-v12_use_custom_style [data-testid="stCheckbox"] [data-baseweb="checkbox"] > div{
  width:23px !important;
  height:23px !important;
  background:rgba(28, 83, 130, .82) !important;
  border:2px solid #4d9ed3 !important;
  box-shadow:0 0 0 2px rgba(50, 133, 193, .16) !important;
}
.st-key-v12_use_custom_style [data-testid="stCheckbox"] [data-baseweb="checkbox"]:has(input:checked) > div{
  background:#147dc1 !important;
  border-color:#8ed8ff !important;
  box-shadow:0 0 0 2px rgba(74, 183, 244, .28) !important;
}
/* 與「貼圖風格」下拉欄左緣對齊，讓它成為該欄位上方的模式切換。 */
.st-key-v12_custom_style_toggle{
  max-width:820px;
  margin:4px auto 0 !important;
}
/* 「存入我的作品」是獨立於 ZIP 打包的雲端保存動作。 */
.st-key-v12_basket_save_to_library button{
  background:#2478b9 !important;
  border-color:#2478b9 !important;
  color:#fff !important;
  font-weight:850 !important;
}
.st-key-v12_basket_save_to_library button:hover{
  background:#17639c !important;
  border-color:#17639c !important;
}
.v12-project-save-divider{
  height:1px;
  margin:22px 0 18px;
  background:linear-gradient(90deg, transparent, rgba(166, 209, 240, .52), transparent);
}
/* 畫風參考的自訂元素需明確儲存，以紅色按鈕提高辨識度。 */
.st-key-v12_style_reference_save_button button,
.st-key-v12_character_settings_apply_button button{
  background:#ff4f67 !important;
  border-color:#ff4f67 !important;
  color:#fff !important;
  font-weight:800 !important;
}
.st-key-v12_style_reference_save_button button:hover,
.st-key-v12_character_settings_apply_button button:hover{
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
/* 已選檔案旁的原生移除鍵不可再被「上傳」覆蓋；明確顯示為刪除 X。 */
.st-key-v12_person_photo_upload [data-testid="stFileChipDeleteBtn"] button,
.st-key-v12_style_reference_upload [data-testid="stFileChipDeleteBtn"] button{
  min-width:42px !important;
  background:#b93650 !important;
  border-color:#d65a70 !important;
  box-shadow:none !important;
}
.st-key-v12_person_photo_upload [data-testid="stFileChipDeleteBtn"] button::after,
.st-key-v12_style_reference_upload [data-testid="stFileChipDeleteBtn"] button::after{
  content:"✕" !important;
  color:#fff !important;
  font-size:1.35rem !important;
  font-weight:900 !important;
}
/* 檔案已存在時，右側加號鍵是換檔用途，不要和刪除鍵同樣寫成上傳。 */
.st-key-v12_person_photo_upload [data-testid="stFileUploaderDropzone"]:has([data-testid="stFileChipDeleteBtn"]) button[aria-label="Add files"]::after,
.st-key-v12_style_reference_upload [data-testid="stFileUploaderDropzone"]:has([data-testid="stFileChipDeleteBtn"]) button[aria-label="Add files"]::after{
  content:"更換" !important;
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
/* 畫風參考的原生 X 會在換頁時被 Streamlit 模擬成空檔事件；改用預覽下方的
   明確清除按鈕，避免把正常換頁誤判為刪除。 */
.st-key-v12_style_reference_upload [data-testid="stFileChipDeleteBtn"]{
  display:none !important;
}
/* STEP 06｜用「貼圖小劇場」呈現等待階段；這是流程提示，不宣稱 AI 的精準完成百分比。 */
.v12-generation-showtime{
  width:min(860px,100%);
  margin:.85rem auto;
  padding:.85rem 1rem;
  border:1px solid rgba(255,183,91,.62);
  border-radius:15px;
  background:linear-gradient(110deg, rgba(107,51,23,.56), rgba(62,42,99,.55), rgba(21,80,107,.48));
  box-shadow:0 7px 22px rgba(255,143,63,.16);
}
.v12-generation-showtime-top{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:.8rem;
  color:#fff0d4;
  font-size:1.03rem;
  font-weight:850;
}
.v12-generation-showtime-scene{
  flex:0 0 auto;
  padding:.18rem .54rem;
  border-radius:999px;
  background:rgba(255,184,89,.20);
  color:#ffe3ae;
  font-size:.8rem;
}
.v12-generation-showtime-track{
  position:relative;
  height:10px;
  overflow:hidden;
  margin:.65rem 0 .5rem;
  border-radius:999px;
  background:rgba(9,18,35,.62);
  box-shadow:inset 0 0 0 1px rgba(255,220,163,.23);
}
.v12-generation-showtime-fill{
  position:relative;
  height:100%;
  border-radius:inherit;
  overflow:hidden;
  background:linear-gradient(90deg,#ff9f43,#ffcf5a,#ff6e91,#9d7dff,#ff9f43);
  background-size:220% 100%;
  box-shadow:0 0 14px rgba(255,180,83,.76);
  animation:v12-generation-stage-breathe 5.8s ease-in-out infinite,
            v12-generation-color-flow 7s linear infinite;
}
.v12-generation-showtime-fill::after{
  content:"";
  position:absolute;
  inset:0;
  width:42%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.64),transparent);
  transform:translateX(-155%) skewX(-18deg);
  animation:v12-generation-shimmer 2.65s ease-in-out infinite;
}
.v12-generation-showtime-note{
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:.7rem;
  color:#d8eaf7;
  font-size:.88rem;
}
.v12-generation-showtime-dots{ display:inline-flex; flex:0 0 auto; gap:.22rem; }
.v12-generation-showtime-dots i{
  width:.34rem;
  height:.34rem;
  border-radius:50%;
  background:#ffe1a3;
  box-shadow:0 0 8px rgba(255,220,163,.72);
  animation:v12-generation-dot-bounce 1.15s ease-in-out infinite;
}
.v12-generation-showtime-dots i:nth-child(2){ animation-delay:.16s; }
.v12-generation-showtime-dots i:nth-child(3){ animation-delay:.32s; }
@keyframes v12-generation-stage-breathe{
  0%,100%{ width:var(--v12-stage-start); }
  50%{ width:var(--v12-stage-end); }
}
@keyframes v12-generation-color-flow{
  from{ background-position:0% 50%; }
  to{ background-position:220% 50%; }
}
@keyframes v12-generation-shimmer{
  0%,24%{ transform:translateX(-155%) skewX(-18deg); opacity:0; }
  32%{ opacity:1; }
  74%,100%{ transform:translateX(330%) skewX(-18deg); opacity:0; }
}
@keyframes v12-generation-dot-bounce{
  0%,100%{ transform:translateY(0); opacity:.4; }
  50%{ transform:translateY(-.24rem); opacity:1; }
}
@media (prefers-reduced-motion: reduce){
  .v12-generation-showtime-fill,.v12-generation-showtime-fill::after,
  .v12-generation-showtime-dots i{ animation:none !important; }
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
.v12-marketplace-intro{
  width:min(1050px,100%);
  margin:-.85rem auto 1.15rem;
  padding:.85rem 1rem;
  border:1px solid rgba(74,183,255,.48);
  border-left:5px solid #39b8ff;
  border-radius:13px;
  background:linear-gradient(100deg,rgba(12,48,78,.96),rgba(20,34,59,.90));
  color:#e7f5ff;
  font-size:clamp(1.02rem,2vw,1.18rem);
  font-weight:800;
  line-height:1.65;
  letter-spacing:.015em;
  box-shadow:0 8px 22px rgba(0,0,0,.16);
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
  bottom:116px;
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
div[class*="st-key-v12_quick_library"],
div[class*="st-key-v12_quick_step_"]{
  position:fixed !important;
  right:24px;
  bottom:calc(170px + env(safe-area-inset-bottom, 0px));
  z-index:10000;
  display:none !important;
  width:48px !important;
  margin:0 !important;
}
body:has(.v12-quick-nav details[open]) div[class*="st-key-v12_quick_library"],
body:has(.v12-quick-nav details[open]) div[class*="st-key-v12_quick_step_"]{display:block !important;}
div[class*="st-key-v12_quick_library"] button,
div[class*="st-key-v12_quick_step_"] button{
  width:48px !important;
  min-width:48px !important;
  height:48px !important;
  min-height:48px !important;
  padding:0 !important;
  border-radius:50% !important;
  border:1px solid rgba(255,255,255,.72) !important;
  background:linear-gradient(145deg,#a58aff,#7c5cff) !important;
  box-shadow:0 7px 16px rgba(2,7,18,.34) !important;
  color:#fff !important;
  font-size:.65rem !important;
  font-weight:950 !important;
  line-height:1.02 !important;
  white-space:pre-line !important;
}
div[class*="st-key-v12_quick_library"] button p,
div[class*="st-key-v12_quick_step_"] button p{
  color:#fff !important;
  font-size:.65rem !important;
  font-weight:950 !important;
  line-height:1.02 !important;
  white-space:pre-line !important;
}
.v12-quick-nav-library .v12-quick-nav-menu{
  visibility:hidden;
  pointer-events:none;
}
div[class*="st-key-v12_quick_step_01"]{bottom:calc(224px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_02"]{bottom:calc(278px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_03"]{bottom:calc(332px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_04"]{bottom:calc(386px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_05"]{bottom:calc(440px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_06"]{bottom:calc(494px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_07"]{bottom:calc(548px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_08"]{bottom:calc(602px + env(safe-area-inset-bottom, 0px));}
div[class*="st-key-v12_quick_step_01"] button{background:linear-gradient(145deg,#ff9aac,#ff5c7a) !important;}
div[class*="st-key-v12_quick_step_02"] button{background:linear-gradient(145deg,#ffc078,#ff9f43) !important;}
div[class*="st-key-v12_quick_step_03"] button{background:linear-gradient(145deg,#75e3a0,#2ecc71) !important;}
div[class*="st-key-v12_quick_step_04"] button{background:linear-gradient(145deg,#79c7fa,#3498db) !important;}
div[class*="st-key-v12_quick_step_05"] button{background:linear-gradient(145deg,#ab8cec,#8e67d8) !important;}
div[class*="st-key-v12_quick_step_06"] button{background:linear-gradient(145deg,#f2ae69,#e67e22) !important;}
div[class*="st-key-v12_quick_step_07"] button{background:linear-gradient(145deg,#72dfda,#32c6c0) !important;}
div[class*="st-key-v12_quick_step_08"] button{background:linear-gradient(145deg,#ef8d84,#e74c3c) !important;}
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
.v12-pricing-contact{
  display:inline-flex;
  align-items:center;
  gap:.38rem;
  margin-top:.82rem;
  padding:.38rem .72rem;
  border:1px solid rgba(255,210,220,.34);
  border-radius:999px;
  color:rgba(255,244,247,.94);
  background:rgba(5,14,32,.22);
  font-size:.82rem;
  font-weight:720;
}
.v12-pricing-contact strong{color:#fff;font-weight:900;letter-spacing:.02em;}
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
@media (max-width:640px){.v12-pricing-hero{padding:1rem .8rem;border-radius:16px;}.v12-pricing-hero p{font-size:.9rem;}.v12-pricing-contact{font-size:.72rem;}.v12-pricing-card{min-height:0;margin-bottom:.42rem;padding:.78rem .55rem;}.v12-pricing-plan-name{font-size:.95rem;}.v12-pricing-price{font-size:1.16rem;}.v12-pricing-benefit{font-size:.7rem;}.v12-pricing-badge,.v12-pricing-current{padding:.15rem .32rem;font-size:.57rem;}}

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
    bottom:107px;
    transform:scale(.9);
    transform-origin:bottom right;
  }
  div[class*="st-key-v12_quick_library"],
  div[class*="st-key-v12_quick_step_"]{
    right:14px;
    bottom:calc(171px + env(safe-area-inset-bottom, 0px));
  }
  div[class*="st-key-v12_quick_library"] button,
  div[class*="st-key-v12_quick_step_"] button{
    width:46px !important;
    min-width:46px !important;
    height:46px !important;
    min-height:46px !important;
  }
  div[class*="st-key-v12_quick_step_01"]{bottom:calc(223px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_02"]{bottom:calc(272px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_03"]{bottom:calc(321px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_04"]{bottom:calc(370px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_05"]{bottom:calc(419px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_06"]{bottom:calc(468px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_07"]{bottom:calc(517px + env(safe-area-inset-bottom, 0px));}
  div[class*="st-key-v12_quick_step_08"]{bottom:calc(566px + env(safe-area-inset-bottom, 0px));}
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

_v12_quick_nav_library_mode = st.session_state.get("v12_view", "create") == "library"
_v12_quick_nav_mode_class = " v12-quick-nav-library" if _v12_quick_nav_library_mode else ""
st.markdown(f"""
<div class="v12-quick-nav{_v12_quick_nav_mode_class}" aria-label="{t('quick_nav_menu')}">
  <details>
    <summary title="{t('quick_nav_open')}" aria-label="{t('quick_nav_open')}">✦</summary>
    <nav class="v12-quick-nav-menu" aria-label="{t('quick_nav_menu')}">
      <a class="v12-quick-nav-link" style="--nav-accent:#ff5c7a" href="#v12-step-01" title="01 {t('quick_nav_01')}"><span class="v12-quick-nav-label">{t('quick_nav_01')}</span><span class="v12-quick-nav-number">01</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#ff9f43" href="#v12-step-02" title="02 {t('quick_nav_02')}"><span class="v12-quick-nav-label">{t('quick_nav_02')}</span><span class="v12-quick-nav-number">02</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#2ecc71" href="#v12-step-03" title="03 {t('quick_nav_03')}"><span class="v12-quick-nav-label">{t('quick_nav_03')}</span><span class="v12-quick-nav-number">03</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#3498db" href="#v12-step-04" title="04 {t('quick_nav_04')}"><span class="v12-quick-nav-label">{t('quick_nav_04')}</span><span class="v12-quick-nav-number">04</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#8e67d8" href="#v12-step-05" title="05 {t('quick_nav_05')}"><span class="v12-quick-nav-label">{t('quick_nav_05')}</span><span class="v12-quick-nav-number">05</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#e67e22" href="#v12-step-06" title="06 {t('quick_nav_06')}"><span class="v12-quick-nav-label">{t('quick_nav_06')}</span><span class="v12-quick-nav-number">06</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#32c6c0" href="#v12-step-07" title="07 {t('quick_nav_07')}"><span class="v12-quick-nav-label">{t('quick_nav_07')}</span><span class="v12-quick-nav-number">07</span></a>
      <a class="v12-quick-nav-link" style="--nav-accent:#e74c3c" href="#v12-step-08" title="08 {t('quick_nav_08')}"><span class="v12-quick-nav-label">{t('quick_nav_08')}</span><span class="v12-quick-nav-number">08</span></a>
    </nav>
  </details>
</div>
""", unsafe_allow_html=True)

def v10_section(title, accent="#7c4dff"):
    """Render every major area as one consistent, compact creation-step card."""
    title = str(title or "")
    matched = re.match(r"^(\S+)\s+([①②③④⑤⑥⑦⑧])\s+(.+)$", title)
    flow_names = {
        "①": t("flow_01"), "②": t("flow_02"), "③": t("flow_03"), "④": t("flow_04"),
        "⑤": t("flow_05"), "⑥": t("flow_06"), "⑦": t("flow_07"), "⑧": t("flow_08"),
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
        eyebrow = "Creative tools" if _v12_ui_locale() == "en" else "制作ツール" if _v12_ui_locale() == "ja" else "創作工具"
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
        # Supabase wraps PostgreSQL exceptions in JSON.  Keep transport and
        # database details private, but preserve our small set of deliberate,
        # user-actionable domain codes so the UI can explain what happened.
        error_code = ""
        try:
            error_payload = json.loads(exc.read().decode("utf-8") or "{}")
            if isinstance(error_payload, dict):
                error_code = str(error_payload.get("message", "") or "")
        except Exception:
            pass
        known_codes = {
            "shared_template_already_exists",
            "shared_template_publish_limit",
            "shared_templates_subscription_required",
            "shared_template_insufficient_credits",
        }
        if error_code in known_codes:
            raise RuntimeError(error_code) from exc
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


def _beta_storage_upload(session, object_path, content, content_type,
                         bucket="member-projects"):
    if not content:
        raise RuntimeError("library_asset_empty")
    return _beta_raw_http(
        "POST",
        "/storage/v1/object/" + urllib.parse.quote(bucket, safe="") + "/"
        + urllib.parse.quote(object_path, safe="/"),
        body=bytes(content),
        access_token=session["access_token"],
        content_type=content_type,
        extra_headers={"x-upsert": "false"},
    )


def _beta_storage_download(session, object_path, bucket="member-projects"):
    return _beta_raw_http(
        "GET",
        "/storage/v1/object/" + urllib.parse.quote(bucket, safe="") + "/"
        + urllib.parse.quote(object_path, safe="/"),
        access_token=session["access_token"],
    )


def _beta_storage_delete(session, object_paths, bucket="member-projects"):
    paths = [str(path) for path in object_paths if path]
    if not paths:
        return
    _beta_http(
        "DELETE",
        "/storage/v1/object/" + urllib.parse.quote(bucket, safe=""),
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


def _beta_creator_reel_status(session):
    response = _beta_rpc(
        "get_my_creator_reel_status", {}, access_token=session["access_token"]
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("creator_reel_status_invalid")
    return {
        "eligible": bool(response.get("eligible", False)),
        "product_code": str(response.get("product_code", "") or ""),
        "auto_reel_limit": max(0, int(response.get("auto_reel_limit", 0) or 0)),
        "retention_days": max(0, int(response.get("retention_days", 0) or 0)),
        "collected_reel_limit": max(
            0, int(response.get("collected_reel_limit", 0) or 0)
        ),
        "publish_template_limit": max(
            0, int(response.get("publish_template_limit", 0) or 0)
        ),
        "active_auto_reel_count": max(
            0, int(response.get("active_auto_reel_count", 0) or 0)
        ),
    }


def _beta_list_creator_reels(session):
    """Return the member's private automatic reels, newest first."""
    query = (
        "/rest/v1/creator_reels?select=id,source_project_name,reel_number,status,"
        "snapshot,grid_path,style_reference_path,created_at,completed_at,auto_expires_at"
        "&status=in.(active,collected,published)&order=created_at.desc"
    )
    response = _beta_http("GET", query, access_token=session["access_token"])
    return response if isinstance(response, list) else []


def _beta_collect_creator_reel(session, reel_id):
    """Promote one active automatic reel into the member's permanent shelf."""
    response = _beta_rpc(
        "collect_creator_reel",
        {"p_reel_id": str(reel_id)},
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise RuntimeError("creator_reel_collect_invalid")
    return {
        "count": max(0, int(response.get("collected_reel_count", 0) or 0)),
        "limit": max(0, int(response.get("collected_reel_limit", 0) or 0)),
    }


def _beta_list_my_shared_template_sources(session):
    """Map a collected reel to its current sharing-template publication state."""
    try:
        response = _beta_rpc(
            "get_my_shared_template_sources",
            {},
            access_token=session["access_token"],
        )
    except Exception:
        # The private reel shelf must remain usable while an administrator is
        # still applying the publishing migration.
        return {}
    if not isinstance(response, list):
        return {}
    return {
        str(item.get("source_reel_id")): str(item.get("status", "") or "")
        for item in response
        if isinstance(item, dict) and item.get("source_reel_id")
    }


def _beta_list_shared_template_marketplace(session):
    response = _beta_rpc(
        "list_shared_template_marketplace",
        {"p_limit": 24, "p_offset": 0},
        access_token=session["access_token"],
    )
    return response if isinstance(response, list) else []


def _beta_list_my_shared_template_management(session):
    response = _beta_rpc(
        "get_my_shared_template_management",
        {},
        access_token=session["access_token"],
    )
    return response if isinstance(response, list) else []


def _beta_set_shared_template_visibility(session, template_id, publish):
    response = _beta_rpc(
        "set_my_shared_template_visibility",
        {"p_template_id": str(template_id), "p_publish": bool(publish)},
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict) or not response.get("template_id"):
        raise RuntimeError("shared_template_management_invalid")
    return response


def _beta_redeem_shared_template(session, template_id, idempotency_key):
    response = _beta_rpc(
        "redeem_shared_template",
        {
            "p_template_id": str(template_id),
            "p_idempotency_key": str(idempotency_key),
        },
        access_token=session["access_token"],
    )
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict) or not response.get("template_id"):
        raise RuntimeError("shared_template_redemption_invalid")
    return response


def _beta_publish_creator_reel(session, reel, title, description, apply_cost):
    """Copy a collected reel's allowed assets into isolated template storage."""
    reel_id = str((reel or {}).get("id", "") or "")
    grid_source_path = str((reel or {}).get("grid_path", "") or "")
    style_source_path = str((reel or {}).get("style_reference_path", "") or "")
    if not reel_id or not grid_source_path:
        raise RuntimeError("shared_template_source_invalid")

    template_id = str(uuid4())
    reservation = _beta_rpc(
        "reserve_shared_template",
        {
            "p_template_id": template_id,
            "p_source_reel_id": reel_id,
            "p_title": str(title or "").strip(),
            "p_description": str(description or "").strip(),
            "p_apply_cost": int(apply_cost),
        },
        access_token=session["access_token"],
    )
    if isinstance(reservation, list):
        reservation = reservation[0] if reservation else {}
    if not isinstance(reservation, dict) or not reservation.get("template_id"):
        raise RuntimeError("shared_template_reservation_invalid")

    preview_path = str(reservation.get("preview_path", "") or "")
    style_path = str(reservation.get("style_reference_path", "") or "")
    if not preview_path:
        raise RuntimeError("shared_template_path_missing")

    created_paths = []
    try:
        preview_bytes = _beta_storage_download(
            session, grid_source_path, bucket="creator-reels"
        )
        _beta_storage_upload(
            session, preview_path, preview_bytes, "image/webp",
            bucket="shared-template-assets",
        )
        created_paths.append(preview_path)
        style_bytes = b""
        if style_path and style_source_path:
            style_bytes = _beta_storage_download(
                session, style_source_path, bucket="creator-reels"
            )
            _beta_storage_upload(
                session, style_path, style_bytes, "image/webp",
                bucket="shared-template-assets",
            )
            created_paths.append(style_path)
        _beta_rpc(
            "complete_shared_template_upload",
            {
                "p_template_id": template_id,
                "p_preview_bytes": len(preview_bytes),
                "p_style_reference_bytes": len(style_bytes),
            },
            access_token=session["access_token"],
        )
    except Exception:
        try:
            _beta_storage_delete(
                session, created_paths, bucket="shared-template-assets"
            )
            _beta_rpc(
                "cancel_shared_template_upload",
                {"p_template_id": template_id},
                access_token=session["access_token"],
            )
        except Exception:
            pass
        raise

    return {
        "template_id": template_id,
        "limit": max(0, int(reservation.get("publish_template_limit", 0) or 0)),
        "count": max(0, int(reservation.get("published_template_count", 0) or 0)),
    }


def _beta_finalize_creator_reel_cleanup(session, reel_ids, object_paths):
    """Remove Storage objects first, then delete their expired database rows."""
    valid_ids = [str(reel_id) for reel_id in (reel_ids or []) if reel_id]
    valid_paths = [str(path) for path in (object_paths or []) if path]
    if valid_paths:
        _beta_storage_delete(session, valid_paths, bucket="creator-reels")
    if valid_ids:
        _beta_rpc(
            "finalize_creator_reel_cleanup",
            {"p_reel_ids": valid_ids},
            access_token=session["access_token"],
        )


def _beta_save_creator_reel(session, snapshot, grid_bytes,
                            style_reference_bytes=b""):
    """Persist one completed 4×2 generation for an eligible subscriber.

    This is intentionally best-effort from the generator's perspective: a
    snapshot problem never invalidates a successfully generated image.
    """
    if not isinstance(snapshot, dict) or not grid_bytes:
        raise RuntimeError("creator_reel_snapshot_invalid")

    reel_id = str(uuid4())
    project_id = str(snapshot.get("project_id", "") or "")
    if not project_id:
        raise RuntimeError("creator_reel_project_missing")
    try:
        reservation = _beta_rpc(
            "reserve_creator_reel",
            {
                "p_reel_id": reel_id,
                "p_source_project_id": project_id,
                "p_source_project_name": str(snapshot.get("project_name", "") or ""),
                "p_snapshot": snapshot,
                "p_has_style_reference": bool(style_reference_bytes),
            },
            access_token=session["access_token"],
        )
    except Exception as exc:
        raise RuntimeError("creator_reel_reservation_failed") from exc
    if isinstance(reservation, list):
        reservation = reservation[0] if reservation else {}
    if not isinstance(reservation, dict) or not reservation.get("reel_id"):
        raise RuntimeError("creator_reel_reservation_invalid")

    grid_path = str(reservation.get("grid_path", "") or "")
    style_path = str(reservation.get("style_reference_path", "") or "")
    if not grid_path:
        raise RuntimeError("creator_reel_path_missing")

    # The reel is a private review snapshot, not the member's downloadable
    # master. A compact WebP keeps automatic saving unobtrusive after image
    # generation has completed.
    grid_webp = _v12_webp_bytes(grid_bytes, quality=80, method=4)
    style_webp = (
        _v12_webp_bytes(style_reference_bytes, quality=82, method=4)
        if style_reference_bytes else b""
    )
    created_paths = [grid_path]
    if style_path and style_webp:
        created_paths.append(style_path)
    stage = "grid_upload"
    try:
        _beta_storage_upload(
            session, grid_path, grid_webp, "image/webp", bucket="creator-reels"
        )
        if style_path and style_webp:
            stage = "style_upload"
            _beta_storage_upload(
                session, style_path, style_webp, "image/webp", bucket="creator-reels"
            )
        stage = "completion"
        _beta_rpc(
            "complete_creator_reel_upload",
            {
                "p_reel_id": reel_id,
                "p_grid_bytes": len(grid_webp),
                "p_style_reference_bytes": len(style_webp),
            },
            access_token=session["access_token"],
        )
    except Exception as exc:
        try:
            _beta_rpc(
                "cancel_creator_reel_upload",
                {"p_reel_id": reel_id},
                access_token=session["access_token"],
            )
            _beta_finalize_creator_reel_cleanup(
                session, [reel_id], created_paths
            )
        except Exception:
            pass
        raise RuntimeError(f"creator_reel_{stage}_failed") from exc

    # Cleanup retries are deliberately non-fatal.  A stale private object is
    # never shown in the UI and is retried at the next successful snapshot.
    try:
        _beta_finalize_creator_reel_cleanup(
            session,
            reservation.get("cleanup_reel_ids", []),
            reservation.get("cleanup_object_paths", []),
        )
    except Exception:
        pass
    return {
        "saved": True,
        "reel_id": reel_id,
        "auto_reel_limit": int(reservation.get("auto_reel_limit", 0) or 0),
        "active_auto_reel_count": int(
            reservation.get("active_auto_reel_count", 0) or 0
        ),
        "auto_expires_at": str(reservation.get("auto_expires_at", "") or ""),
    }


def _v12_webp_bytes(image_or_bytes, quality=86, method=6):
    """Compact persistent project assets without altering the downloadable PNG ZIP."""
    image = image_or_bytes
    if not isinstance(image, Image.Image):
        image = Image.open(BytesIO(bytes(image_or_bytes)))
    image = image.convert("RGBA")
    buf = BytesIO()
    image.save(buf, "WEBP", quality=quality, method=max(0, min(6, int(method))))
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
        "basic_monthly": t("plan_basic"),
        "standard_monthly": t("plan_standard"),
        "advanced_monthly": t("plan_advanced"),
        "credit_pack_7": t("plan_pack"),
    }
    kind = str(entitlement.get("entitlement_kind", "free") or "free")
    product_name = product_names.get(
        str(entitlement.get("product_code", "") or ""), t("entitlement_free")
    )
    if kind == "subscription":
        key = "entitlement_subscription"
    elif kind == "credit_pack":
        key = "entitlement_pack"
    else:
        return t("entitlement_free")
    expires_at = str(entitlement.get("expires_at", "") or "")[:10]
    expiry_text = f"｜{expires_at}" if expires_at else ""
    return t(key, name=product_name, expiry=expiry_text)


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
        st.markdown(f"#### {t('login_title')}")
        user = session.get("user", {})
        email = html.escape(str(user.get("email", "Member") or "Member"))
        account_status_col, refresh_balance_col = st.columns([5, 1], vertical_alignment="center")
        with account_status_col:
            try:
                balance = _beta_credit_balance(session)
                st.success(t("account_balance", email=email, balance=balance))
            except Exception:
                st.warning(t("account_unavailable", email=email))
        with refresh_balance_col:
            if st.button(t("refresh"), key="beta_refresh_credit_balance", use_container_width=True):
                _beta_clear_cached_copy_usage_status()
        try:
            st.markdown(f"**📋 {_beta_entitlement_label(_beta_effective_entitlement(session))}**")
        except Exception:
            st.caption(t("plan_unavailable"))
        try:
            copy_status = _beta_copy_usage_status_cached(session)
            st.markdown("**" + t(
                "copy_quota",
                remaining=copy_status["remaining_count"],
                limit=copy_status["daily_limit"],
            ) + "**")
        except Exception:
            st.caption(t("copy_quota_unavailable"))
        if st.button(t("logout"), type="primary", key="beta_logout"):
            st.session_state.pop("beta_auth_session", None)
            st.session_state.pop("beta_pending_login_url", None)
            _beta_clear_cached_copy_usage_status()
            st.rerun()
        if not _beta_site_openai_key():
            st.info("會員登入與生成次數帳本已啟用；站方生成 API 正在進行最後設定。")
        if _v12_ui_locale() != "zh-TW":
            with st.expander(t("rules_title"), expanded=False):
                st.markdown(_v12_member_rules_markdown())
            return session
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
    st.markdown(f'<div class="v12-beta-login-title">{t("login_title")}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="v12-beta-login-copy">{t("login_trial")}</div>', unsafe_allow_html=True)

    login_url = str(st.session_state.get("beta_pending_login_url", "") or "")
    if not login_url:
        try:
            login_url = _beta_new_login_url()
            st.session_state["beta_pending_login_url"] = login_url
        except Exception:
            st.error(
                "Login service is temporarily unavailable."
                if _v12_ui_locale() == "en"
                else "ログインサービスを現在開始できません。"
                if _v12_ui_locale() == "ja"
                else "登入服務暫時無法啟動，請稍後再試。"
            )

    if login_url:
        _login_left, _login_center, _login_right = st.columns([1, 1.35, 1])
        with _login_center:
            st.link_button(
                t("login_google"),
                login_url,
                type="primary",
                key="beta_google_login_link",
                use_container_width=True,
            )
        st.markdown(
            f'<div class="v12-beta-login-copy">{t("login_expiry")}</div>',
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
        f"""
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
    "療癒系 Cute Wstyle","大頭小身","柔和光影",
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
V8_STYLE_CUSTOM_OPTION = "⭐ 自定義風格"
V12_STYLE_REFERENCE_STRENGTHS = ("柔和融入", "強烈融入")
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
            "action_enabled": False,
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
            # Keep saved pre-checkbox action directions visible after the UI
            # migration.  The retired visual-effect selector itself is ignored.
            "action_enabled": bool(item.get(
                "action_enabled", bool(str(item.get("effect_action_prompt", "") or "").strip())
            )),
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


def _v12_expression_draft():
    """Persistent expression draft, independent from conditional widgets.

    Streamlit removes widget state when a conditional branch is not rendered.
    Keeping this canonical copy means switching to text mode never discards
    the eight expression directions.
    """
    raw = st.session_state.get("v12_expression_draft")
    raw = raw if isinstance(raw, list) else []
    fallback = []
    for i in range(8):
        item = raw[i] if i < len(raw) and isinstance(raw[i], dict) else {}
        fallback.append({
            "emotion": str(item.get(
                "emotion", st.session_state.get(f"v12_expression_emotion_{i}", "")
            ) or ""),
            "emotion_random": bool(item.get(
                "emotion_random",
                st.session_state.get(f"v12_expression_emotion_random_{i}", False),
            )),
            "action_enabled": bool(item.get(
                "action_enabled",
                st.session_state.get(
                    f"v12_expression_action_enabled_{i}",
                    bool(str(item.get("effect_action_prompt", "") or "").strip()),
                ),
            )),
            "effect": item.get(
                "effect", st.session_state.get(
                    f"v12_expression_effect_{i}", V12_EXPRESSION_EFFECTS[0]
                )
            ),
            "effect_action_prompt": str(item.get(
                "effect_action_prompt",
                st.session_state.get(f"v12_expression_effect_action_prompt_{i}", ""),
            ) or ""),
        })
    normalized = _v12_normalize_expression_specs(fallback)
    st.session_state["v12_expression_draft"] = normalized
    return normalized


def _v12_write_expression_draft_slot(
    i, emotion=None, emotion_random=None, action_enabled=None, effect=None,
    effect_action_prompt=None,
):
    draft = _v12_expression_draft()
    if not 0 <= i < 8:
        return
    if emotion is not None:
        draft[i]["emotion"] = str(emotion or "").strip()[:160]
    if emotion_random is not None:
        draft[i]["emotion_random"] = bool(emotion_random)
    if action_enabled is not None:
        draft[i]["action_enabled"] = bool(action_enabled)
    if effect is not None:
        draft[i]["effect"] = (
            effect if effect in V12_EXPRESSION_EFFECTS else V12_EXPRESSION_EFFECTS[0]
        )
    if effect_action_prompt is not None:
        draft[i]["effect_action_prompt"] = str(effect_action_prompt or "").strip()[:500]
    st.session_state["v12_expression_draft"] = _v12_normalize_expression_specs(draft)


def _v12_capture_expression_draft():
    """Capture visible expression widgets before their branch disappears."""
    for i, item in enumerate(_v12_expression_draft()):
        _v12_write_expression_draft_slot(
            i,
            emotion=st.session_state.get(f"v12_expression_emotion_{i}", item["emotion"]),
            emotion_random=st.session_state.get(
                f"v12_expression_emotion_random_{i}", item["emotion_random"]
            ),
            action_enabled=st.session_state.get(
                f"v12_expression_action_enabled_{i}", item["action_enabled"]
            ),
            effect=st.session_state.get(f"v12_expression_effect_{i}", item["effect"]),
            effect_action_prompt=st.session_state.get(
                f"v12_expression_effect_action_prompt_{i}",
                item["effect_action_prompt"],
            ),
        )


def _v12_restore_expression_widgets():
    """Restore expression widgets from the canonical draft before rendering."""
    for i, item in enumerate(_v12_expression_draft()):
        st.session_state[f"v12_expression_emotion_{i}"] = item["emotion"]
        st.session_state[f"v12_expression_emotion_random_{i}"] = bool(
            item["emotion_random"]
        )
        st.session_state[f"v12_expression_action_enabled_{i}"] = bool(
            item["action_enabled"]
        )
        st.session_state[f"v12_expression_effect_{i}"] = item["effect"]
        st.session_state[f"v12_expression_effect_action_prompt_{i}"] = item[
            "effect_action_prompt"
        ]


def _v12_get_expression_specs():
    specs = _v12_expression_draft()
    return _v12_normalize_expression_specs([
        {
            **item,
            "emotion": "" if item["emotion_random"] else item["emotion"],
        }
        for item in specs
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


def _v12_normalize_style_reference_strength(value):
    """Map retired saved values to the two current style-strength choices."""
    value = str(value or "").strip()
    if value == "明顯融入":
        return "強烈融入"
    return value if value in V12_STYLE_REFERENCE_STRENGTHS else "柔和融入"


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
        "style_reference_strength": "柔和融入",
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
            "style_reference_strength": "柔和融入",
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
    # 自訂人物／場景改為有內容即啟用；保留欄位只為相容舊作品資料。
    custom_character_enabled = bool(custom_character)
    sticker_mode = str(data.get("sticker_mode", creative.get("sticker_mode", "text")) or "text")
    if sticker_mode not in {"text", "expression"}:
        sticker_mode = "text"
    expression_specs = _v12_normalize_expression_specs(
        data.get("expression_specs", creative.get("expression_specs", []))
    )
    style_reference_strength = _v12_normalize_style_reference_strength(
        data.get("style_reference_strength", creative.get("style_reference_strength"))
    )
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


def _v12_valid_crop_boxes(crop_boxes, width, height):
    """Return eight safe image-coordinate crop boxes, or the standard grid.

    The browser guide editor works in original-image pixels.  Keeping one
    validator here means the basket, downloads, and cloud project all apply
    exactly the same saved guide positions.
    """
    fallback = base_boxes(width, height)
    if not isinstance(crop_boxes, (list, tuple)) or len(crop_boxes) != 8:
        return fallback

    normalized = []
    for default, raw in zip(fallback, crop_boxes):
        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return fallback
        try:
            x1, y1, x2, y2 = (int(round(float(value))) for value in raw)
        except (TypeError, ValueError):
            return fallback
        x1 = max(0, min(width - 2, x1))
        y1 = max(0, min(height - 2, y1))
        x2 = max(x1 + 2, min(width, x2))
        y2 = max(y1 + 2, min(height, y2))
        if x2 <= x1 or y2 <= y1:
            return fallback
        normalized.append([x1, y1, x2, y2])
    return normalized


def _v12_crop_grid_cell(sheet, idx, crop_boxes=None):
    """Create one LINE-size sticker from the saved straight crop guides."""
    sw, sh = sheet.size
    boxes = _v12_valid_crop_boxes(crop_boxes, sw, sh)
    x1, y1, x2, y2 = boxes[int(idx)]
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


def _v12_prepare_generated_grid(raw, add_crop_room=False, shrink_ratio=0.86):
    """Validate the API image and preserve its exact bytes unless opted in."""
    image = Image.open(BytesIO(raw)).convert("RGBA")
    if not add_crop_room:
        return raw, False
    image, applied = _v12_add_grid_breathing_room(image, shrink_ratio=shrink_ratio)
    return (_v12_png_bytes(image) if applied else raw), applied


def _v12_add_grid_breathing_room(sheet, shrink_ratio=0.86):
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
        ratio = 0.86
    ratio = max(0.60, min(0.95, ratio))
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
        position = (
            x1 + (cell.width - scaled.width) // 2,
            y1 + (cell.height - scaled.height) // 2,
        )
        result.alpha_composite(scaled, position)
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


def _v12_add_current_grid_to_sticker_basket(grid_bytes, crop_boxes=None):
    """Crop the current 4×2 result once and add all eight images to the session basket."""
    payload = bytes(grid_bytes or b"")
    if not payload:
        raise ValueError("尚未取得可裁切的原始總圖。")
    sheet = Image.open(BytesIO(payload)).convert("RGBA")
    saved_boxes = _v12_valid_crop_boxes(crop_boxes, *sheet.size)
    source_hash = hashlib.sha256(
        payload + json.dumps(saved_boxes, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    basket = _v12_get_sticker_basket()
    if any(item.get("source_grid_hash") == source_hash for item in basket):
        return False, "這組原始圖與目前裁切範圍已加入作品暫存區。"

    batch_number = int(st.session_state.get("v12_sticker_basket_batch_number", 0)) + 1
    batch_label = f"批次 {batch_number:02d}"
    additions = []
    for index in range(8):
        item_id = str(uuid4())
        additions.append({
            "id": item_id,
            "image_bytes": _v12_png_bytes(
                _v12_crop_grid_cell(sheet, index, saved_boxes)
            ),
            "batch_label": batch_label,
            "source_slot": index + 1,
            "source_grid_hash": source_hash,
        })
        st.session_state.setdefault(f"v12_basket_select_{item_id}", True)

    st.session_state["v12_sticker_basket"] = basket + additions
    st.session_state["v12_sticker_basket_batch_number"] = batch_number
    return True, f"已將 {batch_label} 的 8 張貼圖加入作品暫存區。"


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


def _v12_save_selected_stickers_to_history(selected, options, main_label, tab_label,
                                           include_style_reference):
    """Create a My Works record directly from the selected workspace stickers.

    This intentionally does not build a ZIP.  The same selected PNGs, MAIN/TAB
    choices and optional private materials are retained for My Works instead.
    """
    history_stickers = {
        f"{number:02d}": bytes(item["image_bytes"])
        for number, item in enumerate(selected, start=1)
    }
    main_image = _v12_make_special_sticker(
        options[main_label]["image_bytes"], (240, 240)
    )
    tab_image = _v12_make_special_sticker(
        options[tab_label]["image_bytes"], (96, 74)
    )
    main_buffer, tab_buffer = BytesIO(), BytesIO()
    main_image.save(main_buffer, "PNG", optimize=True, dpi=(72, 72))
    tab_image.save(tab_buffer, "PNG", optimize=True, dpi=(72, 72))

    history_project = _v12_project_snapshot()
    history_project["project_name"] = (
        history_project.get("project_name") or _v12_default_project_name()
    )
    history_record = _v12_history_record(
        metadata=history_project,
        zip_bytes=b"",
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
        ) if include_style_reference else b"",
    )
    history_record["cloud_main_asset_index"] = next(
        index for index, item in enumerate(selected, start=1)
        if item["id"] == options[main_label]["id"]
    )
    history_record["cloud_tab_asset_index"] = next(
        index for index, item in enumerate(selected, start=1)
        if item["id"] == options[tab_label]["id"]
    )
    return history_record


@st.fragment
def _v12_render_sticker_basket_workspace():
    """Allow an unlimited session basket, then make one final limited-size project ZIP."""
    basket = _v12_get_sticker_basket()
    if not basket:
        st.info(t("basket_empty"))
        return

    selected = _v12_selected_sticker_basket_items()
    st.info(t("basket_summary", count=len(basket), selected=len(selected), limit=V12_PROJECT_STICKER_LIMIT))

    control_left, control_right = st.columns(2)
    with control_left:
        if st.button(t("basket_select_all"), key="v12_basket_select_all", use_container_width=True):
            for item in basket:
                st.session_state[f"v12_basket_select_{item['id']}"] = True
            st.rerun(scope="fragment")
    with control_right:
        if st.button(t("basket_deselect_all"), key="v12_basket_deselect_all", use_container_width=True):
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
                    caption=t("basket_source", batch=_v12_basket_batch_display(item["batch_label"]), slot=item["source_slot"]),
                    use_container_width=True,
                )
                st.session_state.setdefault(f"v12_basket_select_{item_id}", True)
                st.checkbox(t("basket_keep"), key=f"v12_basket_select_{item_id}")
                # 手機／平板通常不方便處理 ZIP；作品籃中的每一張都能直接存成 PNG。
                st.download_button(
                    t("basket_png"),
                    data=item["image_bytes"],
                    file_name=(
                        f"LINE貼圖_{item['batch_label'].replace(' ', '')}_"
                        f"{item['source_slot']:02d}.png"
                    ),
                    mime="image/png",
                    use_container_width=True,
                    key=f"v12_basket_download_{item_id}",
                )
                if st.button(t("remove"), key=f"v12_basket_remove_{item_id}", use_container_width=True):
                    _v12_remove_sticker_basket_item(item_id)
                    st.rerun(scope="fragment")

    selected = _v12_selected_sticker_basket_items()
    st.divider()
    if not selected:
        st.warning(t("basket_selection_required"))
        return
    if len(selected) > V12_PROJECT_STICKER_LIMIT:
        excess = len(selected) - V12_PROJECT_STICKER_LIMIT
        st.warning(t("basket_limit", limit=V12_PROJECT_STICKER_LIMIT, selected=len(selected), excess=excess))
        return

    options = {
        t("basket_source", batch=_v12_basket_batch_display(item["batch_label"]), slot=item["source_slot"]): item
        for item in selected
    }
    option_labels = list(options.keys())

    main_key = "v12_basket_main_choice"
    tab_key = "v12_basket_tab_choice"
    if st.session_state.get(main_key) not in option_labels:
        st.session_state[main_key] = option_labels[0]
    main_label = st.session_state[main_key]
    tab_labels = [label for label in option_labels if label != main_label] or [main_label]
    if st.session_state.get(tab_key) not in tab_labels:
        st.session_state[tab_key] = tab_labels[0]
    tab_label = st.session_state[tab_key]

    # 作品名稱與保存動作先出現；它們會使用目前 MAIN／TAB 的選擇值。
    st.markdown(f"### {t('project_title')}")
    st.caption(t("project_caption"))
    st.text_input(
        t("project_label"),
        key="v12_project_name_input",
        placeholder=t("project_placeholder"),
        max_chars=80,
        on_change=_v12_project_name_on_change,
    )
    name_preview = _v12_project_snapshot().get("project_name", "")
    if not name_preview:
        st.caption(t("project_default", name=_v12_default_project_name()))

    # 雲端保存僅由上方「存入我的作品」按鈕執行；ZIP 打包不再附帶同步。
    cloud_session = _beta_current_user() if _beta_config() else None

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
        t("save_materials"),
        value=True,
        key="v12_save_creation_materials_on_package",
        disabled=not bool(cloud_session) or not has_creation_materials,
    )
    if has_creation_materials:
        st.caption(t("materials_private"))

    # 直接存入「我的作品」不建立 ZIP，使用與一般雲端同步相同的素材選項。
    with st.container(key="v12_basket_save_to_library"):
        if st.button(
            t("save_to_library"),
            use_container_width=True,
            key="v12_basket_save_to_library_button",
            disabled=not bool(cloud_session),
        ):
            try:
                history_record = _v12_save_selected_stickers_to_history(
                    selected,
                    options,
                    main_label,
                    tab_label,
                    _active_style_reference_for_save,
                )
                cloud_record = dict(history_record)
                if not save_creation_materials:
                    cloud_record["source_photo_bytes"] = b""
                    cloud_record["style_reference_bytes"] = b""
                cloud_progress = st.progress(0, text=t("cloud_prepare"))
                cloud_save = _beta_save_library_project(
                    cloud_session,
                    cloud_record,
                    progress_callback=lambda progress, message: cloud_progress.progress(
                        progress, text=message + " " + t("do_not_refresh")
                    ),
                )
                if cloud_save.get("saved"):
                    cloud_progress.progress(
                        100,
                        text=t(
                            "cloud_saved",
                            count=cloud_save["saved_count"],
                            limit=cloud_save["library_capacity"],
                        ),
                    )
                    st.success(
                        t(
                            "cloud_saved",
                            count=cloud_save["saved_count"],
                            limit=cloud_save["library_capacity"],
                        )
                    )
                else:
                    cloud_progress.empty()
                    st.warning(
                        t(
                            "cloud_full",
                            count=cloud_save["saved_count"],
                            limit=cloud_save["library_capacity"],
                        )
                    )
            except Exception:
                st.warning(t("cloud_failed"))

    # 把保存動作與 MAIN／TAB 選擇清楚分開，避免兩種用途混在同一區。
    st.markdown('<div class="v12-project-save-divider"></div>', unsafe_allow_html=True)
    st.markdown(f"### {t('main_tab_title')}")
    st.caption(t("main_tab_caption"))
    main_choices, tab_choices = st.columns(2)
    with main_choices:
        main_label = st.selectbox(t("choose_main"), option_labels, key=main_key)
    tab_labels = [label for label in option_labels if label != main_label] or [main_label]
    if st.session_state.get(tab_key) not in tab_labels:
        st.session_state[tab_key] = tab_labels[0]
    with tab_choices:
        tab_label = st.selectbox(t("choose_tab"), tab_labels, key=tab_key)

    if len(selected) == 1:
        st.caption(t("main_tab_same"))
    st.info(t("package_summary", selected=len(selected), limit=V12_PROJECT_STICKER_LIMIT, main=main_label, tab=tab_label))

    if st.button(
        t("package_selected", count=len(selected)),
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
            st.success(t("package_complete", count=len(selected)))

        except Exception as error:
            st.error(t("package_failed", error=error))

    last_zip = st.session_state.get("v12_basket_last_zip_bytes")
    if last_zip:
        st.download_button(
            t("download_zip"),
            data=last_zip,
            file_name=st.session_state.get("v12_basket_last_zip_name") or t("zip_default_name"),
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

    # 使用者主動載入另一組作品時，以該作品為準，不能再套回離開前的草稿。
    st.session_state.pop("v12_restore_creation_draft", None)
    st.session_state["v12_current_project"] = metadata
    # A saved project must replace the browser draft, never merge with it.
    st.session_state["v12_step04_frontend_revision"] = (
        int(st.session_state.get("v12_step04_frontend_revision", 0)) + 1
    )
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
    st.session_state["transparent_png_option"] = bool(
        metadata.get("transparent_background", False)
    )
    # 逐格縮小是下一次生成才適用的暫時設定，不隨歷史作品強制帶回。
    st.session_state["v12_auto_crop_room_enabled"] = False

    st.session_state["v12_style_reference_strength"] = _v12_normalize_style_reference_strength(
        metadata.get("style_reference_strength")
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
        metadata.get("custom_character", "")
    )

    loaded_style_mode = str(metadata.get("style_mode", "") or "")
    if loaded_style_mode:
        st.session_state["v10_style_mode"] = loaded_style_mode
    st.session_state["v12_use_custom_style"] = (
        loaded_style_mode == V8_STYLE_CUSTOM_OPTION
    )
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
    _loaded_expression_specs = _v12_normalize_expression_specs(
        metadata.get("expression_specs", [])
    )
    st.session_state["v12_expression_draft"] = _loaded_expression_specs
    for i, spec in enumerate(_loaded_expression_specs):
        st.session_state[f"v12_expression_emotion_{i}"] = spec["emotion"]
        st.session_state[f"v12_expression_emotion_random_{i}"] = spec[
            "emotion_random"
        ]
        st.session_state[f"v12_expression_action_enabled_{i}"] = spec[
            "action_enabled"
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
    project["style_reference_strength"] = _v12_normalize_style_reference_strength(
        st.session_state.get("v12_style_reference_strength")
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
    project["custom_character_enabled"] = bool(project["custom_character"])
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


def _v12_creator_reel_snapshot():
    """Freeze the exact settings for one completed 4×2 generation.

    The snapshot intentionally excludes the uploader's source photograph.  An
    optional style-reference image is handled as a separate private asset so a
    later sharing flow can ask for explicit permission before exposing it.
    """
    snapshot = _beta_library_metadata(_v12_project_snapshot())
    resolved_expression_specs = st.session_state.get(
        "v12_expression_generation_specs"
    )
    if isinstance(resolved_expression_specs, list):
        snapshot["expression_specs"] = copy.deepcopy(resolved_expression_specs)
    grid_bytes = bytes(st.session_state.get("generated_4x2_bytes") or b"")
    snapshot["schema_version"] = "v12.creator-reel.1"
    snapshot["creator_reel"] = {
        "kind": "auto",
        "grid_sha256": hashlib.sha256(grid_bytes).hexdigest() if grid_bytes else "",
        "grid_width": 1536,
        "grid_height": 1024,
        "crop_boxes": None,
        "prompt": str(st.session_state.get("last_prompt", "") or "")[:30000],
    }
    return snapshot


_V12_CREATOR_REEL_EXECUTOR = ThreadPoolExecutor(max_workers=2)


def _v12_current_creator_reel_payload():
    """Capture all reel inputs on Streamlit's main thread before queuing work."""
    grid_bytes = bytes(st.session_state.get("generated_4x2_bytes") or b"")
    if not grid_bytes:
        raise RuntimeError("creator_reel_grid_missing")
    style_bytes = bytes(
        st.session_state.get("v12_style_reference_image_bytes") or b""
    )
    has_style_reference = (
        st.session_state.get("v12_style_source_mode")
        == V12_STYLE_REFERENCE_MODE
    )
    return (
        _v12_creator_reel_snapshot(),
        grid_bytes,
        style_bytes if has_style_reference else b"",
    )


def _v12_persist_creator_reel(session, snapshot, grid_bytes, style_bytes=b""):
    """Pure worker task: never access Streamlit state from a background thread."""
    try:
        status = _beta_creator_reel_status(session)
        if not status.get("eligible"):
            return {"saved": False, "reason": "subscription_required"}
        saved = _beta_save_creator_reel(
            session, snapshot, grid_bytes, style_reference_bytes=style_bytes
        )
        return {
            "saved": bool(saved.get("saved", False)),
            "active_count": int(saved.get("active_auto_reel_count", 0) or 0),
            "limit": int(saved.get("auto_reel_limit", 0) or 0),
        }
    except Exception as exc:
        return {
            "saved": False,
            "reason": "save_retry",
            "diagnostic": str(exc or "creator_reel_save_failed")[:100],
        }


def _v12_save_current_creator_reel(session):
    """Save synchronously only when the member explicitly presses Retry."""
    snapshot, grid_bytes, style_bytes = _v12_current_creator_reel_payload()
    feedback = _v12_persist_creator_reel(
        session, snapshot, grid_bytes, style_bytes
    )
    st.session_state["v12_creator_reel_generation_feedback"] = feedback
    return feedback


def _v12_queue_current_creator_reel(session):
    """Start automatic reel saving without delaying the completed-image UI."""
    snapshot, grid_bytes, style_bytes = _v12_current_creator_reel_payload()
    st.session_state["v12_creator_reel_save_future"] = (
        _V12_CREATOR_REEL_EXECUTOR.submit(
            _v12_persist_creator_reel,
            dict(session),
            snapshot,
            grid_bytes,
            style_bytes,
        )
    )
    feedback = {"saved": False, "reason": "saving"}
    st.session_state["v12_creator_reel_generation_feedback"] = feedback
    return feedback


def _v12_collect_creator_reel_save():
    """Publish a finished background save result on the next normal rerun."""
    future = st.session_state.get("v12_creator_reel_save_future")
    if not future or not future.done():
        return
    try:
        feedback = future.result()
    except Exception as exc:
        feedback = {
            "saved": False,
            "reason": "save_retry",
            "diagnostic": str(exc or "creator_reel_save_failed")[:100],
        }
    st.session_state["v12_creator_reel_generation_feedback"] = feedback
    st.session_state.pop("v12_creator_reel_save_future", None)


def _v12_creator_reel_diagnostic_label(value):
    labels = {
        "creator_reel_reservation_failed": t("creator_reel_diagnostic_reservation"),
        "creator_reel_grid_upload_failed": t("creator_reel_diagnostic_grid"),
        "creator_reel_style_upload_failed": t("creator_reel_diagnostic_style"),
        "creator_reel_completion_failed": t("creator_reel_diagnostic_completion"),
    }
    return labels.get(str(value or ""), t("creator_reel_diagnostic_unknown"))


def _v12_creator_reel_slot_labels(snapshot):
    """Summarize the frozen 01–08 directions without exposing source photos."""
    if not isinstance(snapshot, dict):
        return []
    mode = str(snapshot.get("sticker_mode", "text") or "text")
    if mode == "expression":
        specs = _v12_normalize_expression_specs(snapshot.get("expression_specs", []))
        labels = []
        for item in specs:
            emotion = str(item.get("emotion", "") or "").strip()
            if bool(item.get("emotion_random")):
                emotion = t("creator_reel_random_expression")
            action = str(item.get("effect_action_prompt", "") or "").strip()
            labels.append("｜".join(part for part in (emotion, action) if part))
        return labels
    return [
        str(item or "").strip()
        for item in list(snapshot.get("sticker_texts", []) or [])[:8]
    ]


def _v12_render_shared_template_manager(session):
    """Let a publisher see rewards and control marketplace visibility."""
    feedback = st.session_state.pop("v12_shared_template_management_feedback", None)
    if isinstance(feedback, dict):
        st.success(
            t(
                "shared_template_visibility_success",
                title=feedback.get("title", ""),
                status=t(
                    "shared_template_status_published"
                    if feedback.get("published")
                    else "shared_template_status_withdrawn"
                ),
            )
        )
    try:
        templates = _beta_list_my_shared_template_management(session)
    except Exception:
        st.warning(t("shared_template_management_unavailable"))
        return

    with st.expander(t("shared_template_management_title"), expanded=bool(templates)):
        st.caption(t("shared_template_management_intro"))
        if not templates:
            st.caption(t("shared_template_management_empty"))
            return
        for template in templates:
            if not isinstance(template, dict) or not template.get("template_id"):
                continue
            template_id = str(template["template_id"])
            title = str(template.get("title", "") or t("untitled_project"))
            status = str(template.get("status", "") or "withdrawn")
            published = status == "published"
            unlock_count = max(0, int(template.get("unlock_count", 0) or 0))
            reward_credits = max(0, int(template.get("reward_credits", 0) or 0))
            content_col, action_col = st.columns([2.6, 1.0], vertical_alignment="center")
            with content_col:
                st.markdown(f"**🌐 {html.escape(title)}**")
                st.caption(
                    t(
                        "shared_template_management_stats",
                        cost=max(1, int(template.get("apply_cost", 1) or 1)),
                        unlocks=unlock_count,
                        rewards=reward_credits,
                    )
                )
                if str(template.get("description", "") or "").strip():
                    st.caption(str(template["description"]).strip())
            with action_col:
                st.caption(
                    t(
                        "shared_template_status_published"
                        if published
                        else "shared_template_status_withdrawn"
                    )
                )
                action_label = t(
                    "shared_template_withdraw_button"
                    if published
                    else "shared_template_republish_button"
                )
                if st.button(
                    action_label,
                    key=f"v12_shared_template_visibility_{template_id}",
                    use_container_width=True,
                ):
                    try:
                        with st.spinner(t("shared_template_visibility_saving")):
                            _beta_set_shared_template_visibility(
                                session, template_id, publish=not published
                            )
                        st.session_state["v12_shared_template_management_feedback"] = {
                            "title": title,
                            "published": not published,
                        }
                        st.rerun()
                    except Exception:
                        st.error(t("shared_template_visibility_failed"))
            st.divider()


def _v12_render_creator_reels(session):
    """Render the private first-stage reel shelf inside My Works."""
    try:
        status = _beta_creator_reel_status(session)
    except Exception:
        st.warning(t("creator_reel_status_unavailable"))
        return

    st.markdown(f"### {t('creator_reel_title')}")
    collection_feedback = st.session_state.pop(
        "v12_creator_reel_collection_feedback", None
    )
    if isinstance(collection_feedback, dict):
        st.success(
            t(
                "creator_reel_collect_success",
                count=collection_feedback.get("count", 0),
                limit=collection_feedback.get("limit", 0),
            )
        )
    template_feedback = st.session_state.pop(
        "v12_shared_template_publish_feedback", None
    )
    if isinstance(template_feedback, dict):
        st.success(
            t(
                "shared_template_publish_success",
                count=template_feedback.get("count", 0),
                limit=template_feedback.get("limit", 0),
            )
        )
    if not status["eligible"]:
        st.info(t("creator_reel_subscription_only"))
        return

    st.caption(t("creator_reel_intro"))
    st.info(
        t(
            "creator_reel_capacity",
            count=status["active_auto_reel_count"],
            limit=status["auto_reel_limit"],
            days=status["retention_days"],
        )
    )
    st.caption(
        t(
            "creator_reel_policy",
            collected=status["collected_reel_limit"],
            publish=status["publish_template_limit"],
        )
    )
    _v12_render_shared_template_manager(session)
    try:
        reels = _beta_list_creator_reels(session)
    except Exception:
        st.warning(t("creator_reel_list_unavailable"))
        return

    if not reels:
        st.caption(t("creator_reel_empty"))
        return

    published_sources = _beta_list_my_shared_template_sources(session)

    for reel in reels:
        if not isinstance(reel, dict) or not reel.get("id"):
            continue
        reel_id = str(reel["id"])
        snapshot = reel.get("snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        source_name = str(reel.get("source_project_name", "") or "").strip()
        if not source_name:
            source_name = str(snapshot.get("project_name", "") or t("untitled_project"))
        reel_number = max(1, int(reel.get("reel_number", 1) or 1))
        style_name = str(snapshot.get("style_name", "") or "").strip()
        labels = [label for label in _v12_creator_reel_slot_labels(snapshot) if label]
        left, right = st.columns([1.1, 2.2], vertical_alignment="top")
        with left:
            try:
                st.image(
                    _beta_storage_download(
                        session, str(reel.get("grid_path", "") or ""),
                        bucket="creator-reels",
                    ),
                    caption=t("creator_reel_grid_caption"),
                    use_container_width=True,
                )
            except Exception:
                st.info(t("creator_reel_preview_unavailable"))
        with right:
            st.markdown(
                f"### 🎞️ {html.escape(source_name)} · {t('creator_reel_number', number=reel_number)}"
            )
            st.caption(
                t(
                    "creator_reel_created_at",
                    time=_v12_format_display_time(reel.get("created_at", "")),
                )
            )
            expires_at = str(reel.get("auto_expires_at", "") or "")
            if expires_at:
                st.caption(
                    t(
                        "creator_reel_expires_at",
                        time=_v12_format_display_time(expires_at),
                    )
                )
            reel_status = str(reel.get("status", "") or "")
            if reel_status == "collected":
                st.success(t("creator_reel_collected_badge"))
                published_status = published_sources.get(reel_id, "")
                if published_status == "published":
                    st.info(t("shared_template_published_badge"))
                elif published_status != "uploading":
                    with st.expander(t("shared_template_publish_expander"), expanded=False):
                        st.caption(t("shared_template_publish_intro"))
                        with st.form(f"v12_shared_template_publish_{reel_id}"):
                            template_title = st.text_input(
                                t("shared_template_title"),
                                value=f"{source_name} · {t('creator_reel_number', number=reel_number)}",
                                max_chars=80,
                                key=f"v12_shared_template_title_{reel_id}",
                            )
                            template_description = st.text_area(
                                t("shared_template_description"),
                                max_chars=300,
                                key=f"v12_shared_template_description_{reel_id}",
                            )
                            template_cost = st.number_input(
                                t("shared_template_apply_cost"),
                                min_value=1,
                                max_value=20,
                                value=1,
                                step=1,
                                key=f"v12_shared_template_cost_{reel_id}",
                            )
                            rights_confirmed = st.checkbox(
                                t("shared_template_rights_confirm"),
                                key=f"v12_shared_template_rights_{reel_id}",
                            )
                            publish_pressed = st.form_submit_button(
                                t("shared_template_publish_button"),
                                type="primary",
                                use_container_width=True,
                            )
                        if publish_pressed:
                            if len(str(template_title or "").strip()) < 3:
                                st.warning(t("shared_template_title_required"))
                            elif not rights_confirmed:
                                st.warning(t("shared_template_rights_required"))
                            else:
                                try:
                                    with st.spinner(t("shared_template_publishing")):
                                        published = _beta_publish_creator_reel(
                                            session,
                                            reel,
                                            template_title,
                                            template_description,
                                            template_cost,
                                        )
                                    st.session_state[
                                        "v12_shared_template_publish_feedback"
                                    ] = published
                                    st.rerun()
                                except Exception as exc:
                                    message = str(exc or "")
                                    if "shared_template_publish_limit" in message:
                                        st.warning(
                                            t(
                                                "shared_template_publish_limit",
                                                limit=status["publish_template_limit"],
                                            )
                                        )
                                    elif "shared_template_already_exists" in message:
                                        st.warning(t("shared_template_already_published"))
                                    else:
                                        st.error(t("shared_template_publish_failed"))
            elif reel_status == "active":
                collect_button, collect_note = st.columns([1.05, 1.95])
                with collect_button:
                    if st.button(
                        t("creator_reel_collect_button"),
                        key=f"v12_creator_reel_collect_{reel_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            collected = _beta_collect_creator_reel(session, reel_id)
                            st.session_state[
                                "v12_creator_reel_collection_feedback"
                            ] = collected
                            st.rerun()
                        except Exception as exc:
                            message = str(exc or "")
                            if "creator_reel_collection_limit" in message:
                                st.warning(
                                    t(
                                        "creator_reel_collect_limit",
                                        limit=status["collected_reel_limit"],
                                    )
                                )
                            else:
                                st.error(t("creator_reel_collect_failed"))
                with collect_note:
                    st.caption(t("creator_reel_collect_help"))
            if style_name:
                st.caption(t("creator_reel_style", style=style_name))
            if labels:
                with st.expander(t("creator_reel_snapshot_settings"), expanded=False):
                    for index, label in enumerate(labels, start=1):
                        st.caption(f"{index:02d}｜{label}")
                    st.caption(t("creator_reel_private_note"))
            else:
                st.caption(t("creator_reel_private_note"))
        st.divider()


def _v12_apply_shared_template(snapshot, style_reference_bytes, title):
    """Load a redeemed template while preserving the recipient's own photo."""
    metadata = copy.deepcopy(snapshot) if isinstance(snapshot, dict) else {}
    metadata.pop("source_photo_bytes", None)
    # A shared template carries the requested style/text directions, never the
    # publisher's personal character or scene requirements.
    metadata["character_features"] = []
    metadata["custom_character"] = ""
    metadata["custom_character_enabled"] = False
    metadata["project_id"] = str(uuid4())
    metadata["project_name"] = _v12_normalize_project_name(
        f"模板｜{str(title or '').strip()}"
    )
    metadata["created_at"] = _v12_now_iso()
    metadata["updated_at"] = _v12_now_iso()

    current_photo = bytes(st.session_state.get("uploaded_image_bytes") or b"")
    _v12_load_history_record(
        {
            **metadata,
            "source_photo_bytes": current_photo,
            "style_reference_bytes": bytes(style_reference_bytes or b""),
            "grid_bytes": b"",
            "sticker_bytes": {},
        }
    )
    _v12_update_navigation_safe_creation_draft(
        uploaded_image_bytes=current_photo or None,
        v12_style_source_mode=st.session_state.get("v12_style_source_mode"),
        v10_style_mode=st.session_state.get("v10_style_mode"),
        v12_style_reference_image_bytes=st.session_state.get(
            "v12_style_reference_image_bytes"
        ),
        v12_style_reference_filename=st.session_state.get(
            "v12_style_reference_filename"
        ),
        v12_style_reference_fingerprint=st.session_state.get(
            "v12_style_reference_fingerprint"
        ),
    )
    st.session_state["v12_view"] = "create"
    st.session_state["v12_scroll_target"] = "v12-step-02"


def _v12_render_shared_template_marketplace(session):
    """Browse public templates without exposing protected template internals."""
    st.markdown(f'<div class="v10-main-title">{t("shared_template_market_title")}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="v12-marketplace-intro">{html.escape(t("shared_template_market_intro"))}</div>',
        unsafe_allow_html=True,
    )
    if st.button(
        t("shared_template_back_library"),
        key="v12_shared_template_back_library",
    ):
        st.session_state["v12_view"] = "library"
        st.rerun()
    try:
        templates = _beta_list_shared_template_marketplace(session)
    except Exception:
        st.warning(t("shared_template_market_unavailable"))
        return
    if not templates:
        st.info(t("shared_template_market_empty"))
        return

    for template in templates:
        if not isinstance(template, dict) or not template.get("template_id"):
            continue
        template_id = str(template["template_id"])
        title = str(template.get("title", "") or "").strip()
        description = str(template.get("description", "") or "").strip()
        cost = max(1, int(template.get("apply_cost", 1) or 1))
        is_owner = bool(template.get("is_owner", False))
        is_unlocked = bool(template.get("is_unlocked", False))
        preview_path = str(template.get("preview_path", "") or "")
        left, right = st.columns([1.05, 2.15], vertical_alignment="top")
        with left:
            try:
                if preview_path:
                    st.image(
                        _beta_storage_download(
                            session, preview_path, bucket="shared-template-assets"
                        ),
                        caption=t("shared_template_preview_caption"),
                        use_container_width=True,
                    )
            except Exception:
                st.info(t("shared_template_preview_unavailable"))
        with right:
            st.markdown(f"### 🌐 {html.escape(title or t('untitled_project'))}")
            if description:
                st.caption(description)
            st.caption(
                t(
                    "shared_template_style_note",
                    value=t("shared_template_has_style_reference")
                    if bool(template.get("has_style_reference", False))
                    else t("shared_template_no_style_reference"),
                )
            )
            if is_owner:
                button_label = t("shared_template_apply_own")
            elif is_unlocked:
                button_label = t("shared_template_apply_unlocked")
            else:
                button_label = t("shared_template_apply_cost_button", cost=cost)
                st.info(t("shared_template_cost_explainer", cost=cost))
            if st.button(
                button_label,
                key=f"v12_shared_template_apply_{template_id}",
                type="primary",
                use_container_width=True,
            ):
                idempotency_state_key = (
                    f"v12_shared_template_redemption_key_{template_id}"
                )
                redemption_key = st.session_state.setdefault(
                    idempotency_state_key, str(uuid4())
                )
                try:
                    with st.spinner(t("shared_template_redeeming")):
                        redeemed = _beta_redeem_shared_template(
                            session, template_id, redemption_key
                        )
                        style_reference_bytes = b""
                        style_reference_path = str(
                            redeemed.get("style_reference_path", "") or ""
                        )
                        if style_reference_path:
                            style_reference_bytes = _beta_storage_download(
                                session,
                                style_reference_path,
                                bucket="shared-template-assets",
                            )
                    _v12_apply_shared_template(
                        redeemed.get("template_snapshot"),
                        style_reference_bytes,
                        redeemed.get("title", title),
                    )
                    st.session_state.pop(idempotency_state_key, None)
                    st.session_state["v12_shared_template_apply_feedback"] = {
                        "charged": max(
                            0, int(redeemed.get("charged_cost", 0) or 0)
                        ),
                        "title": str(redeemed.get("title", title) or title),
                    }
                    st.rerun()
                except Exception as exc:
                    message = str(exc or "")
                    if "shared_template_insufficient_credits" in message:
                        st.warning(t("shared_template_insufficient_credits"))
                    else:
                        st.error(t("shared_template_apply_failed"))
        st.divider()


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
st.session_state.setdefault("v12_style_reference_strength", "柔和融入")
st.session_state.setdefault("v12_style_reference_elements_enabled", False)
st.session_state.setdefault("v12_style_reference_elements", [])
st.session_state.setdefault("v12_style_reference_custom_elements", "")
st.session_state.setdefault("generated_4x2_bytes", None)
st.session_state.setdefault("v12_grid_breathing_room_applied", False)
st.session_state.setdefault("v12_grid_breathing_room_percent", 0)
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

# 「我的作品」頁不會建立 STEP 01～03 的 Streamlit widgets；而 Streamlit
# 會在該次 rerun 結束時清除未渲染 widget 的值。把創作中的關鍵資料先放到
# 非 widget 的草稿快照，回到「開始製作」時再提前還原，避免照片、畫風、
# 預設風格與人物設定因為單純換頁而消失。
_V12_CREATION_DRAFT_KEYS = (
    "uploaded_image_bytes",
    "v12_style_source_mode",
    "v10_style_mode",
    "v12_last_preset_style_mode",
    "v12_use_custom_style",
    "v12_style_reference_image_bytes",
    "v12_style_reference_filename",
    "v12_style_reference_fingerprint",
    "v12_style_reference_strength",
    "v12_style_reference_custom_elements",
    "v10_character_options",
    "v10_character_custom_1",
    "v10_character_enabled_1",
    "transparent_png_option",
    "v12_auto_crop_room_enabled",
)


def _v12_preserve_creation_draft_for_navigation():
    """Snapshot the active creation draft before leaving the create page."""
    durable = st.session_state.get("v12_navigation_safe_creation_draft")
    snapshot = copy.deepcopy(durable) if isinstance(durable, dict) else {}
    # 正常創作頁已把這些值同步到 durable；這裡只補上尚未同步的新 Session。
    # 不讓檔案上傳元件被 Streamlit 重建後的空值覆蓋掉有效畫風參考圖。
    for key in _V12_CREATION_DRAFT_KEYS:
        if key not in snapshot and key in st.session_state:
            snapshot[key] = copy.deepcopy(st.session_state[key])
    st.session_state["v12_creation_draft_snapshot"] = snapshot
    if snapshot.get("v12_style_reference_image_bytes"):
        st.session_state["v12_style_reference_navigation_guard"] = True
    st.session_state["v12_restore_creation_draft"] = True


def _v12_restore_creation_draft_after_navigation():
    """Restore the saved draft before STEP 01～03 widgets are created again."""
    if not st.session_state.pop("v12_restore_creation_draft", False):
        return
    snapshot = st.session_state.get("v12_creation_draft_snapshot")
    if not isinstance(snapshot, dict):
        return
    for key, value in snapshot.items():
        st.session_state[key] = copy.deepcopy(value)


def _v12_update_navigation_safe_creation_draft(**values):
    """Update only deliberate create-page values in a widget-independent store."""
    current = st.session_state.get("v12_navigation_safe_creation_draft")
    current = copy.deepcopy(current) if isinstance(current, dict) else {}
    for key, value in values.items():
        if key in _V12_CREATION_DRAFT_KEYS:
            current[key] = copy.deepcopy(value)
    st.session_state["v12_navigation_safe_creation_draft"] = current


def _v12_restore_durable_style_reference():
    """Restore an active reference from the durable draft, never from uploader state."""
    draft = st.session_state.get("v12_navigation_safe_creation_draft")
    if not isinstance(draft, dict) or not draft.get("v12_style_reference_image_bytes"):
        return
    for key in (
        "v12_style_reference_image_bytes",
        "v12_style_reference_filename",
        "v12_style_reference_fingerprint",
        "v12_style_reference_strength",
        "v12_style_reference_custom_elements",
    ):
        if key in draft:
            st.session_state[key] = copy.deepcopy(draft[key])
    if draft.get("v12_style_source_mode") == V12_STYLE_REFERENCE_MODE:
        st.session_state["v12_style_source_mode"] = V12_STYLE_REFERENCE_MODE


def _v12_restore_durable_character_settings():
    """Rehydrate the character form only when its Streamlit widget was removed."""
    draft = st.session_state.get("v12_navigation_safe_creation_draft")
    if not isinstance(draft, dict):
        return
    for key in (
        "v10_character_options",
        "v10_character_custom_1",
        "v10_character_enabled_1",
    ):
        saved = draft.get(key)
        current = st.session_state.get(key)
        if saved and (current is None or current == [] or current == ""):
            st.session_state[key] = copy.deepcopy(saved)


def _v12_capture_character_settings_draft():
    """Form-submit callback: commit new values before any navigation rerun can erase them."""
    _v12_update_navigation_safe_creation_draft(
        v10_character_options=list(st.session_state.get("v10_character_options", []) or []),
        v10_character_custom_1=str(st.session_state.get("v10_character_custom_1", "") or ""),
        v10_character_enabled_1=bool(st.session_state.get("v10_character_enabled_1", False)),
    )

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


def _v12_clear_sticker_slot(i):
    """Clear one text slot and every setting that belongs exclusively to it."""
    if not 0 <= i < 8:
        return

    st.session_state[f"sticker_text_{i}"] = ""
    st.session_state[f"v12_sticker_text_widget_{i}"] = ""

    _v12_write_sticker_action_draft_slot(i, enabled=False, prompt="")
    st.session_state[f"v12_sticker_action_enabled_widget_{i}"] = False
    st.session_state[f"v12_sticker_action_prompt_widget_{i}"] = ""

    st.session_state[f"v12_text_color_enabled_{i}"] = False
    st.session_state[f"v12_text_color_enabled_widget_{i}"] = False
    st.session_state[f"v12_text_color_count_{i}"] = 0
    st.session_state[f"v12_text_color_segments_{i}"] = _v12_default_color_segments()
    for j in range(3):
        st.session_state.pop(f"v12_text_color_segment_{i}_{j}", None)


def _v12_clear_expression_slot(i):
    """Clear one expression slot without touching the other seven slots."""
    if not 0 <= i < 8:
        return

    draft = _v12_expression_draft()
    draft[i] = {
        "emotion": "",
        "emotion_random": False,
        "action_enabled": False,
        # Retain this legacy field for saved-project compatibility only.
        "effect": V12_EXPRESSION_EFFECTS[0],
        "effect_action_prompt": "",
    }
    st.session_state["v12_expression_draft"] = _v12_normalize_expression_specs(draft)

    st.session_state[f"v12_expression_emotion_{i}"] = ""
    st.session_state[f"v12_expression_emotion_random_{i}"] = False
    st.session_state[f"v12_expression_action_enabled_{i}"] = False
    st.session_state[f"v12_expression_effect_action_prompt_{i}"] = ""
    st.session_state[f"v12_expression_effect_{i}"] = V12_EXPRESSION_EFFECTS[0]


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


def _v12_step04_frontend_snapshot():
    """Return the full STEP 04 draft in a browser-component friendly shape.

    This deliberately mirrors the existing canonical session-state fields instead
    of introducing a second project format.  It is the compatibility boundary
    used by the standalone frontend experiment before it is wired into the live
    STEP 04 UI.
    """
    text_slots = []
    for index, action in enumerate(_v12_action_draft()):
        segments = _v12_get_color_segments(index)
        active_segments = [
            dict(segment)
            for segment in segments
            if str(segment.get("text", "") or "").strip()
        ]
        text_slots.append({
            "text": str(st.session_state.get(f"sticker_text_{index}", "") or ""),
            "actionEnabled": bool(action.get("enabled")),
            "action": str(action.get("prompt", "") or ""),
            "colorEnabled": bool(active_segments),
            "colorSegments": active_segments,
        })
    expression_slots = []
    for item in _v12_get_expression_specs():
        expression_slots.append({
            "emotion": str(item.get("emotion", "") or ""),
            "random": bool(item.get("emotion_random")),
            "actionEnabled": bool(item.get("action_enabled")),
            "action": str(item.get("effect_action_prompt", "") or ""),
        })
    return {
        "mode": _v12_sticker_mode_value(),
        "textSlots": text_slots,
        "expressionSlots": expression_slots,
        "ai": {
            "topic": str(st.session_state.get("v12_ai_copy_topic", "") or ""),
            "tone": str(st.session_state.get("v12_ai_copy_tone", "") or ""),
            "candidates": list(st.session_state.get("v12_ai_copy_candidates", []) or []),
            "selected": list(st.session_state.get("v12_ai_copy_selected", []) or []),
            # Keep a small cross-batch queue: users may like only a few lines
            # from one AI batch and continue choosing from the next one.
            "picked": list(st.session_state.get("v12_ai_copy_picked", []) or []),
            "themePools": dict(st.session_state.get("v12_theme_pools", {}) or {}),
            "selectedThemes": list(st.session_state.get("v12_selected_themes", []) or []),
        },
        "random": {
            "pool": str(st.session_state.get("v10_phrase_pool_choice", "") or ""),
            "customPhrases": list(V10_CUSTOM_PHRASE_POOL),
        },
        "version": 1,
    }


def _v12_step04_frontend_component_state():
    """Adapt the canonical STEP 04 draft for the browser-side preview editor."""
    snapshot = _v12_step04_frontend_snapshot()
    text_slots = snapshot["textSlots"]
    expression_slots = snapshot["expressionSlots"]
    slots = []
    for index in range(8):
        text_slot = text_slots[index]
        expression_slot = expression_slots[index]
        slots.append({
            "text": text_slot["text"],
            "actionEnabled": text_slot["actionEnabled"],
            "action": text_slot["action"],
            "colorEnabled": text_slot["colorEnabled"],
            "colorSegments": text_slot["colorSegments"],
            "emotion": expression_slot["emotion"],
            "random": expression_slot["random"],
            "expressionActionEnabled": expression_slot["actionEnabled"],
            "expressionAction": expression_slot["action"],
        })
    tone = snapshot["ai"]["tone"]
    pool = snapshot["random"]["pool"]
    random_pools = {
        str(name): [str(phrase) for phrase in values]
        for name, values in V8_RANDOM_POOLS.items()
        if isinstance(values, list)
    }
    ai_quota_text = "AI幫想 今日額度（台灣時間）：登入後可查看"
    ai_login_notice = "需登入Google才能使用"
    ai_session = _beta_current_user()
    if ai_session:
        ai_login_notice = ""
        try:
            ai_usage = _beta_copy_usage_status_cached(ai_session)
            ai_quota_text = (
                "AI幫想 今日額度（台灣時間）：剩 "
                f"{ai_usage['remaining_count']} / {ai_usage['daily_limit']} 次"
            )
        except Exception:
            ai_quota_text = "AI幫想 今日額度（台灣時間）：暫時無法確認"
    return {
        "mode": snapshot["mode"],
        "sourcePanel": str(
            st.session_state.get("v12_step04_frontend_source_panel", "") or ""
        ),
        "aiTopic": snapshot["ai"]["topic"],
        "aiTone": tone if tone in V12_AI_COPY_TONES else V12_AI_COPY_TONES[0],
        "aiCandidates": snapshot["ai"]["candidates"],
        "aiSelected": snapshot["ai"]["selected"],
        "aiPicked": snapshot["ai"]["picked"],
        "randomPool": pool if pool in random_pools else "__all_builtin__",
        "randomCustomDraft": "",
        "randomCustomPhrases": snapshot["random"]["customPhrases"],
        "randomPools": random_pools,
        "aiQuotaText": ai_quota_text,
        "aiLoginNotice": ai_login_notice,
        "message": "",
        "slots": slots,
        "version": 3,
    }


def _v12_apply_step04_frontend_snapshot(payload):
    """Apply a validated frontend STEP 04 draft to the existing canonical state.

    It intentionally performs no generation, cloud write, or rerun.  Existing
    generation and project-saving code therefore keeps consuming exactly the
    same fields as before.  The caller decides when an explicit frontend sync is
    appropriate.
    """
    if not isinstance(payload, dict):
        return False

    # The frontend test editor keeps text and expression values in one 8-slot
    # array. Convert it into the production compatibility shape first.
    if isinstance(payload.get("slots"), list):
        payload = dict(payload)
        payload["textSlots"] = [
            {
                "text": item.get("text", "") if isinstance(item, dict) else "",
                "actionEnabled": item.get("actionEnabled", False) if isinstance(item, dict) else False,
                "action": item.get("action", "") if isinstance(item, dict) else "",
                "colorEnabled": item.get("colorEnabled", False) if isinstance(item, dict) else False,
                "colorSegments": item.get("colorSegments", []) if isinstance(item, dict) else [],
            }
            for item in payload["slots"][:8]
        ]
        payload["expressionSlots"] = [
            {
                "emotion": item.get("emotion", "") if isinstance(item, dict) else "",
                "random": item.get("random", False) if isinstance(item, dict) else False,
                "actionEnabled": item.get("expressionActionEnabled", False) if isinstance(item, dict) else False,
                "action": item.get("expressionAction", "") if isinstance(item, dict) else "",
            }
            for item in payload["slots"][:8]
        ]
        payload["ai"] = {
            "topic": payload.get("aiTopic", ""),
            "tone": payload.get("aiTone", ""),
            "candidates": payload.get("aiCandidates", []),
            "selected": payload.get("aiSelected", []),
            "picked": payload.get("aiPicked", []),
        }
        payload["random"] = {
            "pool": payload.get("randomPool", ""),
            "customPhrases": payload.get("randomCustomPhrases", []),
        }

    mode = "expression" if payload.get("mode") == "expression" else "text"
    st.session_state["v12_sticker_content_mode"] = (
        V12_STICKER_EXPRESSION_MODE if mode == "expression" else V12_STICKER_TEXT_MODE
    )

    text_slots = payload.get("textSlots")
    if isinstance(text_slots, list):
        drafts = []
        for index in range(8):
            raw = text_slots[index] if index < len(text_slots) and isinstance(text_slots[index], dict) else {}
            text = str(raw.get("text", "") or "")[:500]
            enabled = bool(raw.get("actionEnabled"))
            prompt = str(raw.get("action", "") or "").strip()[:500] if enabled else ""
            st.session_state[f"sticker_text_{index}"] = text
            st.session_state[f"v12_sticker_text_widget_{index}"] = text
            st.session_state[f"v12_sticker_action_enabled_{index}"] = enabled
            st.session_state[f"v12_sticker_action_enabled_widget_{index}"] = enabled
            st.session_state[f"v12_sticker_action_prompt_{index}"] = prompt
            st.session_state[f"v12_sticker_action_prompt_widget_{index}"] = prompt
            drafts.append({"enabled": enabled, "prompt": prompt})

            raw_segments = raw.get("colorSegments", []) if bool(raw.get("colorEnabled")) else []
            normalized_segments = _v12_default_color_segments()
            if isinstance(raw_segments, list):
                for segment_index, segment in enumerate(raw_segments[:3]):
                    if not isinstance(segment, dict):
                        continue
                    normalized_segments[segment_index] = {
                        "text": str(segment.get("text", "") or "")[:120],
                        "color_name": str(segment.get("color_name", "") or "")[:40],
                        "hex": str(segment.get("hex", "") or "")[:16],
                    }
            active_count = sum(
                bool(str(segment.get("text", "") or "").strip())
                for segment in normalized_segments
            )
            st.session_state[f"v12_text_color_segments_{index}"] = normalized_segments
            st.session_state[f"v12_text_color_count_{index}"] = active_count
            st.session_state[f"v12_text_color_enabled_{index}"] = bool(active_count)
            st.session_state[f"v12_text_color_enabled_widget_{index}"] = bool(active_count)
            for segment_index, segment in enumerate(normalized_segments):
                st.session_state[f"v12_text_color_segment_{index}_{segment_index}"] = segment["text"]
        st.session_state["v12_sticker_action_draft"] = drafts

    expression_slots = payload.get("expressionSlots")
    if isinstance(expression_slots, list):
        specs = []
        for index in range(8):
            raw = expression_slots[index] if index < len(expression_slots) and isinstance(expression_slots[index], dict) else {}
            random_emotion = bool(raw.get("random"))
            action_enabled = bool(raw.get("actionEnabled"))
            spec = {
                "emotion": str(raw.get("emotion", "") or "")[:200],
                "emotion_random": random_emotion,
                "action_enabled": action_enabled,
                "effect": V12_EXPRESSION_EFFECTS[0],
                "effect_action_prompt": str(raw.get("action", "") or "").strip()[:500] if action_enabled else "",
            }
            specs.append(spec)
            st.session_state[f"v12_expression_emotion_{index}"] = spec["emotion"]
            st.session_state[f"v12_expression_emotion_random_{index}"] = random_emotion
            st.session_state[f"v12_expression_action_enabled_{index}"] = action_enabled
            st.session_state[f"v12_expression_effect_action_prompt_{index}"] = spec["effect_action_prompt"]
        st.session_state["v12_expression_draft"] = _v12_normalize_expression_specs(specs)

    ai = payload.get("ai")
    if isinstance(ai, dict):
        st.session_state["v12_ai_copy_topic"] = str(ai.get("topic", "") or "")[:300]
        _frontend_tone_map = {
            "溫暖鼓勵": "✨ 溫暖療癒",
            "自然日常": "💬 台灣口語",
            "輕鬆搞笑": "😂 搞笑自然",
        }
        _frontend_tone = _frontend_tone_map.get(
            str(ai.get("tone", "") or ""), ai.get("tone")
        )
        if _frontend_tone in V12_AI_COPY_TONES:
            st.session_state["v12_ai_copy_tone"] = _frontend_tone
        candidates = ai.get("candidates")
        if isinstance(candidates, list):
            st.session_state["v12_ai_copy_candidates"] = [str(item).strip()[:120] for item in candidates if str(item).strip()][:16]
        selected = ai.get("selected")
        if isinstance(selected, list):
            st.session_state["v12_ai_copy_selected"] = [int(item) for item in selected if isinstance(item, int) and 0 <= item < 16]
        picked = ai.get("picked")
        if isinstance(picked, list):
            st.session_state["v12_ai_copy_picked"] = list(dict.fromkeys(
                str(item).strip()[:120] for item in picked if str(item).strip()
            ))[:8]

    random_state = payload.get("random")
    if isinstance(random_state, dict):
        pool = str(random_state.get("pool", "") or "")
        if pool and not pool.startswith("__"):
            st.session_state["v10_phrase_pool_choice"] = pool
        custom_phrases = random_state.get("customPhrases")
        if isinstance(custom_phrases, list):
            _save_v10_phrase_pool(custom_phrases)
    return True

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

    # The studio language controls AI Ideas output only; existing sticker text is
    # always left untouched so changing the interface never rewrites a project.
    _copy_output_language = {"en": "English", "ja": "Japanese"}.get(_v12_ui_locale())
    if _copy_output_language:
        system_prompt += (
            f"\n\n【輸出語言】本次介面使用 {_copy_output_language}。"
            f"所有 16 句候選都必須以自然的 {_copy_output_language} 輸出；"
            "除非使用者在主題中明確指定其他語言，否則不要混入中文。"
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
        t("ai_topic"),
        key="v12_ai_copy_topic",
        placeholder=t("ai_topic_placeholder"),
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
        t("ai_tone"),
        V12_AI_COPY_TONES,
        key="v12_ai_copy_tone",
        format_func=lambda value: _v12_localized_option(value, V12_AI_TONE_LABELS),
    )

    # 在點選生成前就先讓會員看見今日剩餘額度。
    # 勾選候選文案時只顯示既有額度，避免每次局部互動都重新驗證登入。
    _copy_status_session = st.session_state.get("beta_auth_session")
    if _copy_status_session:
        try:
            _copy_status = _beta_copy_usage_status_cached(_copy_status_session)
            st.info(t("ai_quota", remaining=_copy_status["remaining_count"], limit=_copy_status["daily_limit"]))
        except Exception:
            st.warning("今日文案額度暫時無法確認，請稍後再試。")
    else:
        st.info("完成 Google 登入後，即可查看並使用每日 AI幫想額度。")

    _generate = st.button(
        t("ai_generate"),
        type="primary",
        use_container_width=True,
        key="v12_ai_copy_generate",
    )
    if _generate:
        if not _topic:
            st.warning(t("ai_topic_required"))
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
    st.markdown(f"#### {t('ai_theme_pool')}")
    _pool_themes = list(st.session_state["v12_theme_pools"].keys())
    if not _pool_themes:
        st.info(t("ai_theme_pool_empty"))
    else:
        # 清掉已不存在的選擇，並限制最多 3 個。
        st.session_state["v12_selected_themes"] = [
            x for x in st.session_state.get("v12_selected_themes", [])
            if x in _pool_themes
        ][:3]

        st.caption(t("ai_theme_pick_caption", count=len(st.session_state["v12_selected_themes"])))

        for _theme_i, _theme in enumerate(_pool_themes):
            _pool = list(st.session_state["v12_theme_pools"].get(_theme, []))[:30]
            _theme_key = f"v12_theme_use_{_theme_i}"
            if _theme_key not in st.session_state:
                st.session_state[_theme_key] = _theme in st.session_state["v12_selected_themes"]

            _currently_selected = _theme in st.session_state["v12_selected_themes"]
            _can_select = _currently_selected or len(st.session_state["v12_selected_themes"]) < 3
            _use_theme = st.checkbox(
                t("ai_add_theme", theme=_theme, count=len(_pool)),
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
                    st.caption(t("ai_theme_empty"))
                else:
                    st.caption(t("ai_theme_delete_help"))
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
                        st.warning(t("ai_theme_delete_count", count=len(_delete_refs)))
                        if st.button(
                            t("ai_theme_delete_button", count=len(_delete_refs)),
                            key=f"v12_theme_pool_delete_{_theme_i}",
                            use_container_width=True,
                        ):
                            _remaining_pool = [
                                x for i, x in enumerate(_pool) if i not in _delete_refs
                            ]
                            st.session_state["v12_theme_pools"][_theme] = _remaining_pool
                            st.success(f"✅ 已刪除 {len(_delete_refs)} 句。")
                            st.rerun()

        st.caption(t("ai_themes_selected", count=len(st.session_state["v12_selected_themes"])))

    # ------------------------------------------------------------
    # ⑤ 從本次已選的最多 3 個主題中挑最終 8 句。
    # ------------------------------------------------------------
    st.markdown(f"#### {t('ai_select_eight')}")
    _active_selected_themes = [
        x for x in st.session_state.get("v12_selected_themes", [])
        if x in st.session_state["v12_theme_pools"]
    ][:3]
    if not _active_selected_themes:
        st.info(t("ai_select_theme_first"))
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
        st.caption(t("ai_selected_eight", count=len(_final_new)))
        if len(_final_new) > 8:
            st.warning(t("ai_max_eight"))
        if st.button(
            t("ai_apply_eight"),
            key="v12_ai_copy_apply_pool",
            use_container_width=True,
        ):
            if len(_final_new) != 8:
                st.warning(t("ai_apply_eight_warning"))
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
        st.caption(t("ai_applied_edit"))
        st.session_state["v12_ai_copy_last_applied"] = False

def build_prompt(style, custom_style, selected_character, custom_character,
                 texts, transparent, color_segments=None, ai_copy_topic="", ai_copy_tone="", ai_copy_topics=None,
                 sticker_mode="text", expression_specs=None, action_prompts=None):
    _layout_instruction = "人物與特效請置中安排，四周保留自然空間，方便後續以裁切線微調。"
    _safety_margin_instruction = "每格左右與上下都要保留至少約 12% 的乾淨透明／背景安全邊距，作為裁切走廊。"
    p = [
        "請以我提供的人物照片作為主要人物參考。",
        "保留人物身份辨識特徵，不任意改變人物核心外觀。",
        "請一次生成一張清楚的4×2八格LINE貼圖總圖。",
        "第一排四格、第二排四格；依使用者輸入順序由左至右、由上至下對應。",
        "八格人物維持同一人物身份與主要視覺風格。",
        "每一格都是獨立貼圖，請讓人物、手勢、文字、道具與特效適合各自裁切。",
        "人物與整體動作可維持誇張、自然、有張力；但任何拳頭、手勢、情緒特效都不得碰到或越過格子邊界。",
        _layout_instruction,
        "遇到大動作時，請優先整體縮小並保留完整姿勢，而不是把手腳、道具、陰影或裝飾延伸到隔壁格。",
        "相鄰貼圖之間必須保留一整條連續、乾淨的背景色或透明空間作為裁切走廊；不要畫格線、框線或分隔線。",
        "【排版硬性規則】將 8 格視為 8 張完全獨立、不可跨越的畫板。每一格的所有像素——人物、文字、道具、特效、光芒、陰影與裝飾——都必須完整留在該格內，絕對不得延伸、重疊或漏進左右／上下相鄰格。",
        _safety_margin_instruction + "內容較多時必須整體縮小，不可把多出的部分放到隔壁格；尤其第二排第 05～08 格，不可將物件、角色或裝飾跨到鄰格。",
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
                f"第{i+1}格：表情／情緒為「{emotion}」。"
            )
            if spec["action_enabled"] and spec["effect_action_prompt"]:
                p.append(
                    f"第{i+1}格需搭配以下人物動作／互動："
                    f"「{spec['effect_action_prompt']}」。"
                    "請讓動作清楚、有張力，且完整保留在該格可裁切範圍內。"
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
    _v12_update_navigation_safe_creation_draft(
        v12_style_source_mode=V12_STYLE_REFERENCE_MODE,
        v10_style_mode="↓ 請選擇風格",
        v12_use_custom_style=False,
        v12_style_reference_image_bytes=buffer.getvalue(),
        v12_style_reference_filename=st.session_state["v12_style_reference_filename"],
        v12_style_reference_fingerprint=fingerprint,
    )


def _v12_style_reference_upload_changed():
    """Keep the active reference in sync with its upload control."""
    if st.session_state.get("v12_style_reference_upload") is not None:
        st.session_state["v12_style_source_mode"] = V12_STYLE_REFERENCE_MODE
        st.session_state["v10_style_mode"] = "↓ 請選擇風格"
        st.session_state["v12_use_custom_style"] = False
        st.session_state.pop("v12_loaded_project_custom_style", None)
    else:
        # 原生 file_uploader 在換頁重建時會回傳 None；這不是使用者刪除。
        # 畫風圖只能經由畫面上的「清除這張畫風參考」明確移除。
        return


def _v12_clear_style_reference():
    """Explicitly remove the active style reference without confusing navigation resets."""
    # 下一次 rerun 時、file_uploader 建立之前再清掉 widget key，避免 Streamlit
    # 的「widget 已建立後不可直接修改」限制。
    st.session_state["v12_style_reference_clear_upload_control"] = True
    st.session_state["v12_style_reference_image_bytes"] = None
    st.session_state["v12_style_reference_filename"] = ""
    st.session_state["v12_style_reference_fingerprint"] = ""
    st.session_state["v12_style_source_mode"] = V12_STYLE_PRESET_MODE
    _v12_update_navigation_safe_creation_draft(
        v12_style_source_mode=V12_STYLE_PRESET_MODE,
        v12_style_reference_image_bytes=None,
        v12_style_reference_filename="",
        v12_style_reference_fingerprint="",
    )


def _v12_render_pricing_page():
    """Render the public-ready plan selection screen before ECPay checkout goes live."""
    products = [
        {
            "code": "basic_monthly",
            "name": t("plan_basic"),
            "price": "NT$99",
            "period": t("period_monthly"),
            "credits": t("benefit_credits", count=20, suffix=_v12_generation_suffix()),
            "copy": t("benefit_copy", count=15),
            "library": t("benefit_library", count=20),
            "button": t("pricing_choose_basic"),
        },
        {
            "code": "standard_monthly",
            "name": t("plan_standard"),
            "price": "NT$149",
            "period": t("period_monthly"),
            "credits": t("benefit_credits", count=30, suffix=_v12_generation_suffix()),
            "copy": t("benefit_copy", count=20),
            "library": t("benefit_library", count=30),
            "button": t("pricing_choose_standard"),
            "recommended": True,
        },
        {
            "code": "advanced_monthly",
            "name": t("plan_advanced"),
            "price": "NT$199",
            "period": t("period_monthly"),
            "credits": t("benefit_credits", count=40, suffix=_v12_generation_suffix()),
            "copy": t("benefit_copy", count=25),
            "library": t("benefit_library", count=40),
            "button": t("pricing_choose_advanced"),
        },
        {
            "code": "credit_pack_7",
            "name": t("plan_pack"),
            "price": "NT$49",
            "period": t("period_pack"),
            "credits": t("benefit_credits", count=7, suffix=""),
            "copy": t("benefit_copy", count=7),
            "library": t("benefit_library", count=7),
            "button": t("pricing_choose_pack"),
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
        f"""
        <div class="v12-pricing-hero">
          <div class="v12-pricing-kicker">STICKER STUDIO NEXT</div>
          <h1>{t('pricing_title')}</h1>
          <p>{t('pricing_intro')}</p>
          <div class="v12-pricing-contact">📮 {t('pricing_contact_label')}：<strong>AGUZN@MSN.COM</strong></div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if current_label:
        st.success(f"📋 {current_label}")
    else:
        st.info(t("pricing_login_notice"))

    plan_cols = st.columns(4, gap="medium")
    for col, product in zip(plan_cols, products):
        is_selected = selected_code == product["code"]
        is_current = current_code == product["code"]
        card_class = " v12-pricing-card-featured" if product.get("recommended") else ""
        with col:
            badge = f'<div class="v12-pricing-badge">{t("pricing_recommended")}</div>' if product.get("recommended") else ""
            state = f'<div class="v12-pricing-current">{t("pricing_current")}</div>' if is_current else ""
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
                "✓ " + t("pricing_current") if is_selected else product["button"],
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
        st.markdown(f"### {t('pricing_selected', name=chosen['name'], price=chosen['price'])}")
        if session:
            st.info(t("pricing_pending"))
            st.button(
                t("pricing_pending_button"),
                key="v12_ecpay_checkout_pending",
                type="primary",
                use_container_width=True,
                disabled=True,
            )
        else:
            st.warning(t("pricing_signin_notice"))
            if st.button(t("pricing_signin"), key="v12_pricing_go_login", type="primary", use_container_width=True):
                st.session_state["v12_view"] = "create"
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(f"### {t('pricing_rules_heading')}")
    with st.expander(t("pricing_rules_title"), expanded=True):
        st.markdown(_v12_pricing_rules_markdown())
    st.caption(t("pricing_footer"))


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
if "v12_ui_locale" not in st.session_state:
    _requested_locale = str(st.query_params.get("lang", "") or "")
    st.session_state["v12_ui_locale"] = (
        _requested_locale if _requested_locale in LANGUAGES else "zh-TW"
    )

_locale_spacer, _locale_picker = st.columns([4, 1])
with _locale_picker:
    st.selectbox(
        t("language"),
        list(LANGUAGES.keys()),
        format_func=lambda value: f"🌐 {LANGUAGES[value]}",
        key="v12_ui_locale",
        label_visibility="collapsed",
    )

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
    if _v12_current_view in {"library", "pricing", "marketplace"}:
        if st.button(t("nav_create"), key="v12_nav_create", use_container_width=True):
            st.session_state["v12_view"] = "create"
            st.rerun()
    else:
        st.link_button(
            t("nav_contact"),
            "https://www.threads.com/@tuwillson",
            use_container_width=True,
        )
with _v12_nav_cols[1]:
    if st.button(t("nav_pricing"), key="v12_nav_pricing", use_container_width=True):
        _v12_preserve_creation_draft_for_navigation()
        _public02a_sync_style_bank_from_widgets()
        _save_v10_presets()
        st.session_state["v12_view"] = "pricing"
        st.rerun()
with _v12_nav_cols[2]:
    if st.button(t("nav_library"), key="v12_nav_library", use_container_width=True):
        # FIX11: leave-create navigation explicitly commits the dedicated style bank
        # before conditional editor widgets disappear.
        _v12_preserve_creation_draft_for_navigation()
        _public02a_sync_style_bank_from_widgets()
        _save_v10_presets()
        st.session_state["v12_view"] = "library"
        st.rerun()

# This is a real Streamlit button, visually placed in the expanded floating
# menu so it follows the exact same in-page navigation as "My Works" above.
if st.button(t("quick_nav_library_button"), key="v12_quick_library", help=t("quick_nav_library_title")):
    _v12_preserve_creation_draft_for_navigation()
    _public02a_sync_style_bank_from_widgets()
    _save_v10_presets()
    st.session_state["v12_view"] = "library"
    st.rerun()

if st.session_state.get("v12_view") == "library":
    for _v12_step_number, _v12_step_key in enumerate(
        ("quick_nav_01", "quick_nav_02", "quick_nav_03", "quick_nav_04", "quick_nav_05", "quick_nav_06", "quick_nav_07", "quick_nav_08"),
        start=1,
    ):
        if st.button(
            f"{t(_v12_step_key)}\n{_v12_step_number:02d}",
            key=f"v12_quick_step_{_v12_step_number:02d}",
            help=f"{_v12_step_number:02d} {t(_v12_step_key)}",
        ):
            st.session_state["v12_view"] = "create"
            st.session_state["v12_scroll_target"] = f"v12-step-{_v12_step_number:02d}"
            st.rerun()

st.session_state.setdefault("v12_view", "create")

# 必須在 create 頁面的 widgets 被重新建立前執行，否則 Streamlit 會以空白的
# widget state 蓋掉原本草稿。
if st.session_state["v12_view"] == "create":
    _v12_restore_creation_draft_after_navigation()

if st.session_state["v12_view"] == "pricing":
    _v12_render_pricing_page()
    st.stop()

if st.session_state["v12_view"] == "marketplace":
    _marketplace_session = _beta_current_user() if _beta_config() else None
    if _marketplace_session:
        _v12_render_shared_template_marketplace(_marketplace_session)
    else:
        st.markdown(f'<div class="v10-main-title">{t("shared_template_market_title")}</div>', unsafe_allow_html=True)
        st.warning(t("shared_template_market_login_required"))
        if st.button(t("shared_template_back_library"), key="v12_shared_template_login_back"):
            st.session_state["v12_view"] = "library"
            st.rerun()
    st.stop()

if st.session_state["v12_view"] == "library":
    st.markdown(f'<div class="v10-main-title">{t("nav_library")}</div>', unsafe_allow_html=True)
    st.caption(t("library_intro"))

    if st.button(
        t("shared_template_market_nav"),
        key="v12_shared_template_market_nav",
        type="primary",
        use_container_width=True,
    ):
        st.session_state["v12_view"] = "marketplace"
        st.rerun()

    _cloud_session = _beta_current_user() if _beta_config() else None
    if _cloud_session:
        _v12_render_creator_reels(_cloud_session)
        st.divider()
        try:
            _cloud_status = _beta_library_status(_cloud_session)
            st.info(t("cloud_library_status", count=_cloud_status["saved_count"], limit=_cloud_status["library_capacity"]))
            st.caption(t("cloud_capacity_note"))
            _cloud_projects = _beta_list_library_projects(_cloud_session)
        except Exception:
            _cloud_projects = []
            st.warning(t("cloud_unavailable"))

        if _cloud_projects:
            st.markdown(f"### {t('cloud_projects')}")
            for _cloud_project in _cloud_projects:
                if not isinstance(_cloud_project, dict) or not _cloud_project.get("id"):
                    continue
                _cloud_id = str(_cloud_project["id"])
                _cloud_meta = _cloud_project.get("metadata")
                if not isinstance(_cloud_meta, dict):
                    _cloud_meta = {}
                _cloud_title = str(_cloud_project.get("title") or _cloud_meta.get("project_name") or t("untitled_project"))
                _cloud_left, _cloud_right = st.columns([1, 2.4], vertical_alignment="top")
                with _cloud_left:
                    try:
                        st.image(
                            _beta_storage_download(_cloud_session, _cloud_project.get("preview_path", "")),
                            caption=t("cloud_preview"),
                            use_container_width=True,
                        )
                    except Exception:
                        st.info(t("preview_unavailable"))
                with _cloud_right:
                    st.markdown(f"### 🏷️ {html.escape(_cloud_title)}")
                    st.caption(t("cloud_saved_at", time=_v12_format_display_time(_cloud_project.get("created_at", ""))))
                    st.caption(t("cloud_assets_note"))
                    _cloud_act1, _cloud_act2 = st.columns(2)
                    with _cloud_act1:
                        if st.button(t("return_create"), key=f"cloud_load_{_cloud_id}", use_container_width=True):
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
                            t("confirm_delete"),
                            key=f"cloud_delete_ack_{_cloud_id}",
                        )
                        if st.button(t("delete_cloud"), key=f"cloud_delete_{_cloud_id}", use_container_width=True):
                            if not _delete_ack:
                                st.warning(t("delete_confirm_first"))
                            else:
                                try:
                                    _beta_delete_library_project(_cloud_session, _cloud_id)
                                    st.rerun()
                                except Exception:
                                    st.error(t("delete_failed"))
                st.divider()
        else:
            st.caption(t("cloud_empty"))
    else:
        st.info(t("cloud_login"))

    _projects_all = list(st.session_state.get("v12_projects_local_index", []))
    _top_a, _top_b = st.columns([4, 1])
    with _top_a:
        _search_name = st.text_input(
            t("project_search"),
            placeholder=t("project_search_placeholder"),
            key="v12_project_search",
        )
    with _top_b:
        st.metric(t("session_projects"), len(_projects_all))

    _projects = list(_projects_all)
    if _search_name.strip():
        needle = _search_name.strip().lower()
        _projects = [p for p in _projects if needle in str(p.get("project_name", "")).lower()]

    if not _projects:
        st.info(t("no_projects"))
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

    st.stop()


st.markdown('<div id="v12-page-top"></div>', unsafe_allow_html=True)
st.markdown(f'<div class="v10-main-title">{t("main_title")}</div>', unsafe_allow_html=True)
_shared_template_apply_feedback = st.session_state.pop(
    "v12_shared_template_apply_feedback", None
)
if isinstance(_shared_template_apply_feedback, dict):
    _shared_template_charged = max(
        0, int(_shared_template_apply_feedback.get("charged", 0) or 0)
    )
    st.success(
        t(
            "shared_template_apply_success",
            title=_shared_template_apply_feedback.get("title", ""),
            cost=_shared_template_charged,
        )
    )

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
    st.warning(t("login_required"))


st.markdown(f"""
<div class="v12-journey">
  <div class="v12-journey-heading"><span class="v12-journey-icon">🎯</span>{t('journey_title')}</div>
  <div class="v12-journey-steps">
    <div class="v12-journey-step" style="--journey-accent:#ff6b86"><span class="v12-journey-number">01</span><span class="v12-journey-label">{t('step_01')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#ffad54"><span class="v12-journey-number">02</span><span class="v12-journey-label">{t('step_02')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#53d68b"><span class="v12-journey-number">03</span><span class="v12-journey-label">{t('step_03')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#55b8ff"><span class="v12-journey-number">04</span><span class="v12-journey-label">{t('step_04')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#a78bfa"><span class="v12-journey-number">05</span><span class="v12-journey-label">{t('step_05')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#ffad54"><span class="v12-journey-number">06</span><span class="v12-journey-label">{t('step_06')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#5ad7d1"><span class="v12-journey-number">07</span><span class="v12-journey-label">{t('step_07')}</span></div>
    <div class="v12-journey-step" style="--journey-accent:#ff748f"><span class="v12-journey-number">08</span><span class="v12-journey-label">{t('step_08')}</span></div>
  </div>
</div>
""", unsafe_allow_html=True)
st.markdown(f'<div class="v11-section-tip">{t("journey_tip")}</div>', unsafe_allow_html=True)
st.divider()

v10_section(t("section_person"), "#ff5c7a")
st.caption(t("photo_intro"))
with st.container(key="v12_person_photo_upload"):
    uploaded = st.file_uploader(t("photo_select"), type=["jpg","jpeg","png","webp"])
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
            st.image(im, caption=t("photo_preview"), width=420)
        st.success(t("photo_loaded"))
    except Exception as e:
        st.error(t("photo_failed"))
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
            st.image(restored_photo, caption=t("photo_restored_preview"), width=420)
        st.info(t("photo_restored"))
    except Exception:
        st.warning(t("photo_restore_failed"))

_v12_update_navigation_safe_creation_draft(
    uploaded_image_bytes=st.session_state.get("uploaded_image_bytes"),
)

st.divider()

v10_section(t("section_style"), "#ff9f43")
st.caption(t("style_intro"))
st.session_state.setdefault("v12_style_source_mode", V12_STYLE_PRESET_MODE)

def _v12_set_style_source_mode(mode):
    st.session_state["v12_style_source_mode"] = mode
    _v12_update_navigation_safe_creation_draft(v12_style_source_mode=mode)
    if mode == V12_STYLE_REFERENCE_MODE:
        st.session_state["v10_style_mode"] = "↓ 請選擇風格"
        st.session_state["v12_use_custom_style"] = False
        st.session_state.pop("v12_loaded_project_custom_style", None)


_style_source_mode = str(st.session_state.get(
    "v12_style_source_mode", V12_STYLE_PRESET_MODE
))
if _style_source_mode not in {V12_STYLE_PRESET_MODE, V12_STYLE_REFERENCE_MODE}:
    _style_source_mode = V12_STYLE_PRESET_MODE
    st.session_state["v12_style_source_mode"] = _style_source_mode

# 原生 file_uploader 的值不可靠；創作草稿中的圖片才是畫風參考的真實來源。
_v12_restore_durable_style_reference()
_style_source_mode = str(st.session_state.get(
    "v12_style_source_mode", V12_STYLE_PRESET_MODE
))

if st.session_state.pop("v12_style_reference_clear_upload_control", False):
    st.session_state.pop("v12_style_reference_upload", None)

_style_preset_col, _style_reference_col = st.columns(2)
with _style_preset_col:
    st.button(
        ("✓ " if _style_source_mode == V12_STYLE_PRESET_MODE else "")
        + t("style_preset_mode"),
        key="v12_select_style_preset_mode",
        use_container_width=True,
        type="primary" if _style_source_mode == V12_STYLE_PRESET_MODE else "secondary",
        on_click=_v12_set_style_source_mode,
        args=(V12_STYLE_PRESET_MODE,),
    )
with _style_reference_col:
    st.button(
        ("✓ " if _style_source_mode == V12_STYLE_REFERENCE_MODE else "")
        + t("style_reference_mode"),
        key="v12_select_style_reference_mode",
        use_container_width=True,
        type="primary" if _style_source_mode == V12_STYLE_REFERENCE_MODE else "secondary",
        on_click=_v12_set_style_source_mode,
        args=(V12_STYLE_REFERENCE_MODE,),
    )

# 自定義風格改由獨立勾選啟用，預設風格下拉只保留預設選項。
_v8_style_items = [x for x in V8_STYLES if not x.startswith("⭐ 自訂")]
_style_options = ["↓ 請選擇風格"] + _v8_style_items
style_mode = str(st.session_state.get("v10_style_mode", "↓ 請選擇風格"))
style = style_mode
custom_style = ""
_style_reference_active = False

if _style_source_mode == V12_STYLE_PRESET_MODE:
    st.caption(t("style_preset_caption"))
    st.session_state.setdefault(
        "v12_use_custom_style", style_mode == V8_STYLE_CUSTOM_OPTION
    )
    # 對齊下方的「貼圖風格」欄位，避免模式切換落在整個頁面的最左側。
    with st.container(key="v12_custom_style_toggle"):
        _use_custom_style = st.checkbox(
            t("custom_style_enable"),
            key="v12_use_custom_style",
        )
    if _use_custom_style:
        # Keep the canonical mode explicit so prompts and saved projects retain
        # the exact custom-style behavior.
        st.session_state["v10_style_mode"] = V8_STYLE_CUSTOM_OPTION
        style_mode = V8_STYLE_CUSTOM_OPTION
    else:
        if style_mode == V8_STYLE_CUSTOM_OPTION:
            _last_preset_style = str(st.session_state.get(
                "v12_last_preset_style_mode", "↓ 請選擇風格"
            ))
            st.session_state["v10_style_mode"] = (
                _last_preset_style
                if _last_preset_style in _style_options
                else "↓ 請選擇風格"
            )
        style_mode = st.selectbox(
            t("style_label"),
            _style_options,
            key="v10_style_mode",
        )
        st.session_state["v12_last_preset_style_mode"] = style_mode

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
            # 不預設選到第 01 組；避免畫面看似正在編輯第 01 組，
            # 但本次 session 的內容尚未載入。
            if not st.session_state.get("v12_style_editor_choice_initialized", False):
                st.session_state["v12_style_editor_slot"] = 0
                st.session_state.pop("v12_style_editor_name", None)
                st.session_state.pop("v12_style_editor_prompt", None)
                st.session_state["v12_style_editor_choice_initialized"] = True
            st.session_state.setdefault("v12_style_editor_slot", 0)
            if st.session_state.pop("v12_style_editor_clear_pending", False):
                _v12_load_style_editor_from_slot()
                st.success("🧹 此風格已清空")
            _editor_slot = st.selectbox(
                "📁 編輯哪一組風格",
                [0, *range(1, 11)],
                format_func=lambda x: (
                    "↓ 點擊選擇要編輯的自定義風格"
                    if x == 0
                    else f"{x:02d}｜{str(_public02a_get_style_bank()[str(x)]['name']).strip() or f'使用者自定{x}'}"
                ),
                key="v12_style_editor_slot",
                on_change=_v12_load_style_editor_from_slot,
            )
            _save_style_editor = False
            _clear_style_editor = False
            if _editor_slot == 0:
                st.info("👆 請先從上方選單點選 01～10 的一組風格，才會載入可編輯的內容。")
            else:
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
        st.write("、".join(_style_options[1:]))
else:
    style_mode = "↓ 請選擇風格"
    style = style_mode
    custom_style = ""
    st.markdown(f"<div style='text-align:center;margin:16px 0 6px;font-size:19px;font-weight:800;'>{t('style_reference_title')}</div>", unsafe_allow_html=True)
    st.caption(t("style_reference_caption"))
    _style_reference_left, _style_reference_center, _style_reference_right = st.columns([1, 2, 1])
    with _style_reference_center:
        _style_reference_upload = st.file_uploader(
            t("style_reference_upload"),
            type=["jpg", "jpeg", "png", "webp"],
            key="v12_style_reference_upload",
            on_change=_v12_style_reference_upload_changed,
            help=t("style_reference_help"),
        )
    if _style_reference_upload is not None:
        try:
            _v12_set_style_reference_from_upload(_style_reference_upload)
        except Exception:
            st.session_state["v12_style_reference_image_bytes"] = None
            st.session_state["v12_style_reference_filename"] = ""
            st.session_state["v12_style_reference_fingerprint"] = ""
            st.error(t("style_reference_failed"))
    _style_reference_active = bool(st.session_state.get("v12_style_reference_image_bytes"))
    if _style_reference_active:
        _reference_preview_left, _reference_preview_center, _reference_preview_right = st.columns([1, 2, 1])
        with _reference_preview_center:
            st.image(
                st.session_state["v12_style_reference_image_bytes"],
                caption=t("style_reference_preview") + "：" + str(st.session_state.get("v12_style_reference_filename", "reference")),
                width=240,
            )
        st.success(t("style_reference_enabled"))
        st.info(t("style_identity_priority"))
        if st.button(
            "🗑️ 清除這張畫風參考",
            key="v12_clear_style_reference",
            help="明確移除目前畫風參考圖。",
        ):
            _v12_clear_style_reference()
            st.rerun()
        st.caption(t("style_settings_caption"))
        _normalized_style_strength = _v12_normalize_style_reference_strength(
            st.session_state.get("v12_style_reference_strength")
        )
        if st.session_state.get("v12_style_reference_strength") != _normalized_style_strength:
            st.session_state["v12_style_reference_strength"] = _normalized_style_strength
        st.radio(
            f"{t('style_strength')}{t('style_strength_immediate')}",
            V12_STYLE_REFERENCE_STRENGTHS,
            horizontal=True,
            key="v12_style_reference_strength",
            format_func=lambda value: {
                "柔和融入": t("style_strength_soft"),
                "強烈融入": t("style_strength_strong"),
            }[value],
            help=t("style_strength_help"),
        )
        with st.form("v12_style_reference_details_form", border=False):
            st.text_area(
                t("style_elements"),
                key="v12_style_reference_custom_elements",
                placeholder=t("style_elements_placeholder"),
                max_chars=1200,
            )
            with st.container(key="v12_style_reference_save_button"):
                _apply_style_reference_details = st.form_submit_button(
                    t("style_apply"),
                    type="primary",
                    use_container_width=True,
                )
        if _apply_style_reference_details:
            st.success(t("style_applied"))
        st.caption(t("style_elements_help"))
        st.caption(t("style_materials_caption"))

# 檔案上傳控制項已在這次 create rerun 建立完成，換頁保護旗標不再需要。
st.session_state.pop("v12_style_reference_navigation_guard", None)

_v12_update_navigation_safe_creation_draft(
    v12_style_source_mode=st.session_state.get("v12_style_source_mode"),
    v10_style_mode=st.session_state.get("v10_style_mode"),
    v12_last_preset_style_mode=st.session_state.get("v12_last_preset_style_mode"),
    v12_use_custom_style=st.session_state.get("v12_use_custom_style"),
    v12_style_reference_image_bytes=st.session_state.get("v12_style_reference_image_bytes"),
    v12_style_reference_filename=st.session_state.get("v12_style_reference_filename"),
    v12_style_reference_fingerprint=st.session_state.get("v12_style_reference_fingerprint"),
    v12_style_reference_strength=st.session_state.get("v12_style_reference_strength"),
    v12_style_reference_custom_elements=st.session_state.get("v12_style_reference_custom_elements"),
)

v10_section(t("section_features"), "#2ecc71")
st.caption(t("features_intro"))
# Clean up selections retired from the feature menu so an older session can
# reopen this form without retaining hidden style choices.
_v12_restore_durable_character_settings()
_saved_character_options = st.session_state.get("v10_character_options")
if isinstance(_saved_character_options, list):
    st.session_state["v10_character_options"] = [
        option for option in _saved_character_options if option in CHARACTER_OPTIONS
    ]
with st.form("v12_character_settings_form", border=False):
    selected_character = st.multiselect(
        t("feature_select"),
        CHARACTER_OPTIONS,
        key="v10_character_options",
    )
    st.text_area(
        t("custom_character"),
        key="v10_character_custom_1",
        height=70,
    )
    with st.container(key="v12_character_settings_apply_button"):
        _save_character_settings = st.form_submit_button(
            t("save_character"),
            type="primary",
            use_container_width=True,
            on_click=_v12_capture_character_settings_draft,
        )
custom_character = str(
    st.session_state.get("v10_character_custom_1", "") or ""
).strip()
if _save_character_settings:
    if _save_v10_presets():
        st.success(t("character_saved"))
    else:
        st.error(t("save_failed"))

_v12_update_navigation_safe_creation_draft(
    v10_character_options=list(st.session_state.get("v10_character_options", []) or []),
    v10_character_custom_1=str(st.session_state.get("v10_character_custom_1", "") or ""),
    v10_character_enabled_1=bool(st.session_state.get("v10_character_enabled_1", False)),
)

st.session_state.setdefault("v12_sticker_content_mode", V12_STICKER_TEXT_MODE)
st.session_state.setdefault("v12_step04_frontend_revision", 1)
v10_section(t("section_content"), "#3498db")
st.caption(t("content_intro"))

# STEP 04｜正式採用瀏覽器端互動。一般輸入不觸發 Streamlit rerun；
# 只有需要後端的動作（例如 AI 幫想或生成總圖）才會同步一次。
_v12_step04_frontend_enabled = True
if _v12_step04_frontend_enabled:
    _v12_frontend_result = render_frontend_editor(
        data={
            "storageKey": "sticker-studio-next.step04-formal.v1",
            "initial": _v12_step04_frontend_component_state(),
            "revision": st.session_state["v12_step04_frontend_revision"],
            "formal": True,
        },
        key="v12_step04_frontend_formal",
        component_name="v12_step04_frontend_formal",
    )
    _v12_frontend_event = (
        _v12_frontend_result.get("event")
        if isinstance(_v12_frontend_result, dict)
        else None
    )
    if isinstance(_v12_frontend_event, dict):
        _v12_event_state = _v12_frontend_event.get("state")
        if isinstance(_v12_event_state, dict):
            _v12_apply_step04_frontend_snapshot(_v12_event_state)
            _v12_source_panel = str(
                _v12_event_state.get("sourcePanel", "") or ""
            )
            st.session_state["v12_step04_frontend_source_panel"] = (
                _v12_source_panel if _v12_source_panel in {"ai", "random"} else ""
            )
        st.session_state["v12_step04_frontend_pending_event"] = dict(
            _v12_frontend_event
        )

def _v12_set_sticker_content_mode(mode):
    # 切換模式前後都以獨立草稿保存／回填，不依賴條件式 widget state。
    if mode == V12_STICKER_EXPRESSION_MODE:
        _v12_capture_sticker_action_draft()
        _v12_restore_expression_widgets()
    elif mode == V12_STICKER_TEXT_MODE:
        _v12_capture_expression_draft()
        _v12_restore_sticker_action_widgets()
    st.session_state["v12_sticker_content_mode"] = mode


_sticker_content_choice = str(st.session_state.get(
    "v12_sticker_content_mode", V12_STICKER_TEXT_MODE
))
_sticker_mode = _v12_sticker_mode_value(_sticker_content_choice)
if not _v12_step04_frontend_enabled:
    _mode_text_col, _mode_expression_col = st.columns(2)
    with _mode_text_col:
        st.button(
            ("✓ " if _sticker_content_choice == V12_STICKER_TEXT_MODE else "")
            + t("content_text_mode"),
            key="v12_select_text_sticker_mode",
            use_container_width=True,
            type="primary" if _sticker_content_choice == V12_STICKER_TEXT_MODE else "secondary",
            on_click=_v12_set_sticker_content_mode,
            args=(V12_STICKER_TEXT_MODE,),
        )
    with _mode_expression_col:
        st.button(
            ("✓ " if _sticker_content_choice == V12_STICKER_EXPRESSION_MODE else "")
            + t("content_expression_mode"),
            key="v12_select_expression_sticker_mode",
            use_container_width=True,
            type="primary" if _sticker_content_choice == V12_STICKER_EXPRESSION_MODE else "secondary",
            on_click=_v12_set_sticker_content_mode,
            args=(V12_STICKER_EXPRESSION_MODE,),
        )
    if _sticker_mode == "text":
        v12_mode_panel_heading(t("text_source_heading"), '#3498db')
        st.caption(t("text_source_caption"))
    else:
        v12_mode_panel_heading(t("expression_heading"), "#3498db")
        st.caption(t("expression_caption"))

# V12｜文字來源面板：只有使用者主動點選後，才展開對應功能，
# 避免 AI 文案與隨機用語區塊同時堆在畫面上。
st.session_state.setdefault("v12_text_source_panel", "")

def _v12_toggle_text_source(_panel):
    if st.session_state.get("v12_text_source_panel", "") == _panel:
        st.session_state["v12_text_source_panel"] = ""
    else:
        st.session_state["v12_text_source_panel"] = _panel

if not _v12_step04_frontend_enabled and _sticker_mode == "text":
    _src_a, _src_b = st.columns(2)
    with _src_a:
        st.button(
            t("ai_copy_button"),
            key="v12_open_ai_copy_panel",
            use_container_width=True,
            type="primary" if st.session_state.get("v12_text_source_panel") == "ai" else "secondary",
            on_click=_v12_toggle_text_source,
            args=("ai",),
        )
    with _src_b:
        st.button(
            t("random_phrases_button"),
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
        t("text_slot", number=i + 1),
        key=_widget_key,
        on_change=_v12_sync_sticker_text,
        args=(i,),
        placeholder=t("text_placeholder"),
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
    # Keep 「清空」 compact but wide enough to remain readable in each slot.
    _action_toggle, _action_clear = st.columns([7, 3])
    with _action_toggle:
        _action_enabled = st.checkbox(
            t("action_enable"),
            key=_action_enabled_widget_key,
            on_change=_v12_sync_sticker_action_enabled,
            args=(i,),
        )
    with _action_clear:
        st.button(
            _v12_slot_clear_label(),
            key=f"v12_clear_sticker_slot_{i}",
            use_container_width=False,
            on_click=_v12_clear_sticker_slot,
            args=(i,),
        )
    _v12_write_sticker_action_draft_slot(i, enabled=_action_enabled)
    if _action_enabled:
        _action_prompt_widget_key = f"v12_sticker_action_prompt_widget_{i}"
        st.session_state.setdefault(
            _action_prompt_widget_key,
            str(_action_draft_item["prompt"] or ""),
        )
        st.text_area(
            t("action_prompt"),
            key=_action_prompt_widget_key,
            on_change=_v12_sync_sticker_action_prompt,
            args=(i,),
            placeholder=t("action_placeholder"),
            max_chars=500,
            height=68,
        )
        _v12_write_sticker_action_draft_slot(
            i,
            prompt=st.session_state.get(_action_prompt_widget_key, ""),
        )

    _color_enabled_widget_key = f"v12_text_color_enabled_widget_{i}"
    _color_enabled = st.checkbox(
        t("color_enable"),
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

        st.caption(t("color_segment_hint"))

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
                            <span>{t('color_segment', number=j + 1)}</span>
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
                        f'<div style="margin:0 0 6px 0;font-weight:700;">{t("color_segment", number=j + 1)}</div>',
                        unsafe_allow_html=True,
                    )
                st.button(
                    t("paste_text"),
                    key=f"v12_text_color_segment_paste_{i}_{j}",
                    on_click=_v12_paste_sticker_text_to_color_segment,
                    args=(i, j),
                    type="primary",
                    use_container_width=True,
                )
                st.text_input(
                    t("color_segment", number=j + 1),
                    key=f"v12_text_color_segment_{i}_{j}",
                    placeholder=t("color_segment_placeholder"),
                    label_visibility="collapsed",
                )
                segs[j]["text"] = str(
                    st.session_state.get(f"v12_text_color_segment_{i}_{j}", "")
                )

            with _b:
                st.markdown(f"**{t('color_choose')}**")
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
                t("color_add"),
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

    st.markdown('<div class="v12-slot-row-divider"></div>', unsafe_allow_html=True)
    cols = st.columns(4)
    for i, col in enumerate(cols, start=4):
        with col:
            _v12_render_text_slot(i)

    texts = get_texts()
    filled = sum(bool(x.strip()) for x in texts)
    st.info(t("text_filled", filled=filled))


@st.fragment
def _v12_render_expression_sticker_slots():
    """Keep expression-only edits local, like the text editor fragment."""
    st.caption(t("expression_intro"))
    for row_start in (0, 4):
        cols = st.columns(4)
        for offset, col in enumerate(cols):
            i = row_start + offset
            with col:
                st.markdown(f"**{t('image_number', number=i + 1)}**")
                st.session_state.setdefault(f"v12_expression_emotion_{i}", "")
                st.session_state.setdefault(
                    f"v12_expression_emotion_random_{i}", False
                )
                # 「隨機」僅放在表情欄位標題的右側；輸入框本身維持完整寬度。
                _emotion_label, _emotion_random = st.columns([3, 1])
                with _emotion_label:
                    st.markdown(f"**{t('expression_field')}**")
                with _emotion_random:
                    emotion_random = st.checkbox(
                        t("random"),
                        key=f"v12_expression_emotion_random_{i}",
                    )
                st.text_input(
                    t("expression_field"),
                    key=f"v12_expression_emotion_{i}",
                    placeholder=t("expression_placeholder"),
                    disabled=emotion_random,
                    label_visibility="collapsed",
                )
                _expression_draft_item = _v12_expression_draft()[i]
                st.session_state.setdefault(
                    f"v12_expression_action_enabled_{i}",
                    bool(_expression_draft_item["action_enabled"]),
                )
                _action_toggle, _action_clear = st.columns([7, 3])
                with _action_toggle:
                    action_enabled = st.checkbox(
                        t("action_enable"),
                        key=f"v12_expression_action_enabled_{i}",
                    )
                with _action_clear:
                    st.button(
                        _v12_slot_clear_label(),
                        key=f"v12_clear_expression_slot_{i}",
                        use_container_width=False,
                        on_click=_v12_clear_expression_slot,
                        args=(i,),
                    )
                st.session_state.setdefault(
                    f"v12_expression_effect_action_prompt_{i}",
                    str(_expression_draft_item["effect_action_prompt"] or ""),
                )
                if action_enabled:
                    st.text_area(
                        t("action_prompt"),
                        key=f"v12_expression_effect_action_prompt_{i}",
                        placeholder=t("action_placeholder"),
                        max_chars=500,
                        height=68,
                    )
                _v12_write_expression_draft_slot(
                    i,
                    emotion=st.session_state.get(f"v12_expression_emotion_{i}", ""),
                    emotion_random=emotion_random,
                    action_enabled=action_enabled,
                    effect_action_prompt=st.session_state.get(
                        f"v12_expression_effect_action_prompt_{i}", ""
                    ),
                )
        if row_start == 0:
            st.markdown('<div class="v12-slot-row-divider"></div>', unsafe_allow_html=True)
    configured = sum(
        bool(
            spec["emotion"]
            or spec["emotion_random"]
            or spec["action_enabled"]
        )
        for spec in _v12_get_expression_specs()
    )
    st.info(t("expression_configured", configured=configured))


if (
    not _v12_step04_frontend_enabled
    and _sticker_mode == "text"
    and st.session_state.get("v12_text_source_panel") == "ai"
):
    st.caption(t("ai_result_intro"))
    _v12_render_ai_copy_assistant()


@st.fragment
def _v12_render_random_phrase_panel():
    _pool_names=list(V8_RANDOM_POOLS.keys())
    _pool_placeholder = "↓ 請選擇語詞池"
    _custom_pool = "⭐ 我的自定義語詞池"
    _all_builtin_pool = "全部內建語詞"
    _pool_choice=st.selectbox(
        t("random_pool"),
        [_pool_placeholder, _custom_pool, _all_builtin_pool] + _pool_names,
        key="v10_phrase_pool_choice",
        format_func=lambda value: {
            _pool_placeholder: t("choose_pool"),
            _custom_pool: t("custom_pool"),
            _all_builtin_pool: t("all_builtin_pools"),
        }.get(value, _v12_localized_option(value, V12_RANDOM_POOL_LABELS)),
    )

    if _pool_choice == _custom_pool:
        with st.expander(t("custom_pool"),expanded=True):
            _new_phrase=st.text_input(
                t("random_add_phrase"),
                key="v10_new_phrase",
                placeholder=t("random_phrase_placeholder"),
            )
            _pa,_pb,_pc=st.columns(3)
            with _pa:
                if st.button(t("random_add_to_pool"),key="v10_add_phrase",use_container_width=True):
                    if _new_phrase.strip():
                        V10_CUSTOM_PHRASE_POOL.append(_new_phrase.strip())
                        V10_CUSTOM_PHRASE_POOL[:]=list(dict.fromkeys(V10_CUSTOM_PHRASE_POOL))
                        if _save_v10_phrase_pool(V10_CUSTOM_PHRASE_POOL):
                            st.success(t("random_saved"))
                            st.rerun()
                        else:
                            st.error(t("random_save_failed"))
            with _pb:
                if st.button(t("random_draw_eight"),key="v10_random_my_pool",use_container_width=True):
                    if V10_CUSTOM_PHRASE_POOL:
                        vals=random.sample(V10_CUSTOM_PHRASE_POOL,min(8,len(V10_CUSTOM_PHRASE_POOL)))
                        while len(vals)<8:
                            vals.append(random.choice(V10_CUSTOM_PHRASE_POOL))
                        random.shuffle(vals)
                        set_texts(vals)
                        st.rerun()
                    else:
                        st.warning(t("random_custom_empty"))
            with _pc:
                if st.button(t("random_clear"),key="v10_clear_my_pool",use_container_width=True):
                    V10_CUSTOM_PHRASE_POOL.clear()
                    if _save_v10_phrase_pool(V10_CUSTOM_PHRASE_POOL):
                        st.success(t("random_cleared"))
                        st.rerun()

            if V10_CUSTOM_PHRASE_POOL:
                st.caption(t("random_total", count=len(V10_CUSTOM_PHRASE_POOL)))
                st.write("、".join(V10_CUSTOM_PHRASE_POOL))
            else:
                st.info(t("random_none"))

        _active_pool=V10_CUSTOM_PHRASE_POOL
    else:
        if _pool_choice == _pool_placeholder:
            _active_pool=[]
            st.info(t("random_select_first"))
        elif _pool_choice == _all_builtin_pool:
            _active_pool=[x for vals in V8_RANDOM_POOLS.values() for x in vals]
        else:
            _active_pool=V8_RANDOM_POOLS.get(_pool_choice,[])

        a,b,c=st.columns(3)
        with a:
            if st.button(t("random_fill_eight"),use_container_width=True,key="v10_random_v8"):
                if _active_pool:
                    vals=random.sample(_active_pool,min(8,len(_active_pool)))
                    while len(vals)<8:
                        vals.append(random.choice(_active_pool))
                    random.shuffle(vals)
                    set_texts(vals)
                    st.rerun()
        with b:
            st.write(t("random_current_pool", count=len(_active_pool)))
        with c:
            st.write(t("all_builtin_pools"))

    # 內建的「分類→語句→指定格」功能保留。
    if _pool_choice not in (_custom_pool, _pool_placeholder):
        p1,p2,p3=st.columns([1.2,2.4,0.8])
        with p1:
            _common_cat=st.selectbox(t("phrase_category"),_pool_names,key="v8_common_cat", format_func=lambda value: _v12_localized_option(value, V12_RANDOM_POOL_LABELS))
        with p2:
            _common_phrase=st.selectbox(t("phrase_reference"),V8_RANDOM_POOLS.get(_common_cat,[]),key="v8_common_phrase")
        with p3:
            _target_slot=st.selectbox(t("target_slot"),[f"{i:02d}" for i in range(1,9)],key="v8_target_slot")
        if st.button(t("insert_selected_slot"),key="v8_insert_phrase"):
            set_texts([
                _common_phrase if i==int(_target_slot)-1 else st.session_state.get(f"sticker_text_{i}","")
                for i in range(8)
            ])
            st.rerun()


if _v12_step04_frontend_enabled:
    _v12_frontend_pending = st.session_state.pop(
        "v12_step04_frontend_pending_event", None
    )
    if isinstance(_v12_frontend_pending, dict):
        _v12_event_kind = str(_v12_frontend_pending.get("kind", "") or "")
        if _v12_event_kind == "generate-ai":
            # Keep the assistant open after the server returns its 16 results.
            st.session_state["v12_step04_frontend_source_panel"] = "ai"
            _v12_ai_topic = str(
                st.session_state.get("v12_ai_copy_topic", "") or ""
            ).strip()
            _v12_ai_tone = str(
                st.session_state.get("v12_ai_copy_tone", "") or ""
            )
            _v12_feedback = ""
            if not _v12_ai_topic:
                _v12_feedback = "請先輸入想要的主題，再請 AI 幫想。"
            else:
                _v12_copy_session = _beta_current_user()
                if not _v12_copy_session:
                    _v12_feedback = "請先完成 Google 登入，才能使用會員 AI 幫想額度。"
                else:
                    _v12_copy_key = _beta_site_openai_key()
                    if not _v12_copy_key:
                        _v12_feedback = "站方 AI 幫想服務尚未完成設定，請稍後再試。"
                    else:
                        _v12_copy_reservation = None
                        try:
                            _v12_copy_reservation = _beta_reserve_copy_generation(
                                _v12_copy_session
                            )
                            if not _v12_copy_reservation.get("granted"):
                                _v12_feedback = "今日 AI 幫想額度已用完，將在台灣時間午夜重置。"
                            else:
                                _v12_phrases = _v12_ai_copy_generate(
                                    _v12_ai_topic, _v12_ai_tone, _v12_copy_key
                                )
                                _beta_finish_copy_generation(
                                    _v12_copy_session,
                                    _v12_copy_reservation.get("job_id"),
                                )
                                st.session_state["v12_ai_copy_candidates"] = _v12_phrases
                                st.session_state["v12_ai_copy_topic_used"] = _v12_ai_topic
                                st.session_state["v12_ai_copy_tone_used"] = _v12_ai_tone
                                st.session_state["v12_ai_copy_selected"] = []
                                for _v12_pick_i in range(16):
                                    st.session_state[
                                        f"v12_ai_copy_pick_{_v12_pick_i}"
                                    ] = False
                        except Exception:
                            if (
                                _v12_copy_reservation
                                and _v12_copy_reservation.get("job_id")
                            ):
                                try:
                                    _beta_finish_copy_generation(
                                        _v12_copy_session,
                                        _v12_copy_reservation["job_id"],
                                        "copy_generation_failed",
                                    )
                                except Exception:
                                    pass
                            _v12_feedback = "AI 幫想產生失敗，請稍後再試。"
            st.session_state["v12_step04_frontend_feedback"] = _v12_feedback
            st.session_state["v12_step04_frontend_revision"] += 1
            st.rerun()
else:
    if _sticker_mode == "text" and st.session_state.get("v12_text_source_panel") == "random":
        _v12_render_random_phrase_panel()

    if _sticker_mode == "text":
        _v12_render_text_slots()
    else:
        _v12_render_expression_sticker_slots()
        # 表情欄位在局部 fragment 內編輯；只在模式切換或完整頁面流程
        # （例如按下生成）時彙整成 persistent draft，減少不必要的更新。
        _v12_capture_expression_draft()

if _v12_step04_frontend_enabled:
    _v12_frontend_feedback = str(
        st.session_state.pop("v12_step04_frontend_feedback", "") or ""
    )
    if _v12_frontend_feedback:
        st.warning(_v12_frontend_feedback)
texts = get_texts()
expression_specs = _v12_get_expression_specs()
_prepared_expression_specs = st.session_state.get("v12_expression_generation_specs")
_prompt_expression_specs = (
    _v12_normalize_expression_specs(_prepared_expression_specs)
    if _sticker_mode == "expression" and isinstance(_prepared_expression_specs, list)
    else expression_specs
)

if _sticker_mode == "text":
    with st.expander(t("view_all_phrases"),expanded=False):
        for cat,vals in V8_RANDOM_POOLS.items():
            st.markdown(f"**{_v12_localized_option(cat, V12_RANDOM_POOL_LABELS)}**")
            st.write("、".join(vals))

if _sticker_mode == "text":
    st.divider()
    v10_section(t("font_section"), "#8e67d8")
    v10_subsection(t("font_outline"), "#8e67d8")
    st.caption(t("font_outline_caption"))
    _saved_text_style = str(st.session_state.get("v8_text_style", "") or "")
    if _saved_text_style not in V8_TEXT_STYLE_OPTIONS:
        st.session_state["v8_text_style"] = V8_TEXT_STYLE_LEGACY_MAP.get(
            _saved_text_style, "不使用外框"
        )
    text_style = st.selectbox(
        t("font_label"),
        V8_TEXT_STYLE_OPTIONS,
        index=0,
        key="v8_text_style",
        format_func=lambda value: _v12_localized_option(value, V12_TEXT_OUTLINE_LABELS),
    )
else:
    text_style = ""

_selected_font = st.session_state.get("v8_selected_font", None)
if _sticker_mode == "text":
    v10_subsection(t("font_catalog"), "#8e67d8")

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
    st.caption(t("font_pick_caption"))
    st.markdown('<div id="v10-font-result-anchor" style="scroll-margin-top: 72px;"></div>', unsafe_allow_html=True)
    _quick_font_choice = st.selectbox(
        t("font_quick_pick"),
        ["— 快速選擇字型｜可直接輸入數字 —"] + _font_labels,
        key="v12_font_quick_pick",
        format_func=_v12_font_choice_display,
    )
    if _quick_font_choice != "— 快速選擇字型｜可直接輸入數字 —":
        _quick_font_no = int(_quick_font_choice.split("｜", 1)[0])
        if st.session_state.get("v8_selected_font") != _quick_font_no:
            st.session_state["v8_selected_font"] = _quick_font_no
            st.session_state["v10_font_gallery_open"] = False
        _selected_font = _quick_font_no
    if _selected_font:
        st.success(t("font_selected", number=_selected_font, name=_v12_font_display_name(_selected_font)))
    else:
        st.info(t("font_none"))

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
    t("font_open_gallery") if not st.session_state.v10_font_gallery_open
    else t("font_close_gallery"),
    key="v10_font_gallery_toggle",
    use_container_width=True,
):
    st.session_state.v10_font_gallery_open = not st.session_state.v10_font_gallery_open
    st.rerun()

if _sticker_mode == "text":
    st.caption("來源出處：cz88422")

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

                st.caption(_v12_font_display_name(_i))
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
                        caption=f"{_selected_font:03d}｜{_v12_font_display_name(_selected_font)}",
                        use_container_width=True,
                    )
                st.caption("以上為參考效果；實際生成會由 AI 依人物、文字與整體風格重新詮釋。")
        else:
            st.write("尚未選擇。")

st.divider()
v10_section(t("section_background"), "#16a085")
st.markdown(f'<div class="v11-transparent-title">{t("background_transparent")}</div>', unsafe_allow_html=True)
with st.container(key="transparent_png_option"):
    transparent = st.checkbox(
        t("transparent_png"),
        value=bool(st.session_state.get("transparent_png_option", False)),
    )

_auto_crop_room = st.checkbox(
    _v12_crop_room_t("auto_crop_room"),
    value=False,
    key="v12_auto_crop_room_enabled",
    disabled=not transparent,
    help=_v12_crop_room_t("auto_crop_room_help"),
)
_v12_update_navigation_safe_creation_draft(
    transparent_png_option=bool(transparent),
    v12_auto_crop_room_enabled=bool(_auto_crop_room),
)

v10_subsection(t("settings_review"), "#ff9f43")
style_mode = st.session_state.get("v10_style_mode", "↓ 請選擇風格")

_prompt_color_segments = _v12_valid_color_segments(texts)
_prompt_sticker_action_prompts = _v12_get_sticker_action_prompts()
_prompt_character_features = list(selected_character)
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
    _style_reference_strength = _v12_normalize_style_reference_strength(
        st.session_state.get("v12_style_reference_strength")
    )
    _style_reference_strength_instruction = {
        "柔和融入": "保留第一張人物照片的可辨識臉部與人物特徵；僅輕度採用第二張圖的配色、線條與筆觸，不採用第二張圖的角色外觀。",
        "強烈融入": "只強烈採用第二張圖的視覺媒材與畫法（線條、筆觸、色塊、陰影、配色、留白）；絕不可採用其中的人物臉部、髮型、服裝、年齡、姿勢、道具或角色身份。第一張人物照片的身份與外觀永遠優先。",
    }.get(_style_reference_strength, "第二張圖只提供視覺媒材與畫法，不提供人物角色。")
    prompt += (
        "\n【人物身份優先規則】第一張圖是唯一的人物／臉部參考。八格必須是同一位第一張照片中的人，保留其臉型、五官、髮型、髮色、鬍鬚、年齡感、膚色與可辨識特徵。"
        "第二張圖僅供美術風格參考；即使圖中有人物，也禁止把那個人的臉、髮型、服裝、姿勢、年齡、性別、道具、身形或角色身份帶入結果。"
        "兩張圖的角色外觀若有衝突，無條件以第一張人物照片為準；只從第二張圖擷取線條、筆觸、色彩、材質、明暗、構圖留白與視覺節奏。"
        "\n【畫風參考模式】本次輸入的第二張圖片是畫風參考圖。"
        + _style_reference_strength_instruction
        + "請先辨識參考圖的高階視覺特徵，包括線條與筆觸、配色、陰影方式、畫面留白與整體情緒節奏，並把這些『畫風特徵』套用到第一張圖的人物。"
        "請將第一張人物完整重新繪製並轉譯進參考圖的視覺語言，但不可把第二張圖中的人物重新畫入結果。"
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
with st.expander(t("prompt_view")):
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
        st.info(t("prompt_unavailable"))



v10_section(t("section_generate"), "#e67e22")
st.markdown(f'<div class="v11-section-tip">{t("generate_intro")}</div>', unsafe_allow_html=True)

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
_generation_feedback = st.session_state.pop("v11_generation_release_feedback", None)

# ------------------------------------------------------------
# 第一階段：使用者按下按鈕
# 只做必要的輸入檢查，設定 pending lock，立即 rerun。
# 這樣下一次畫面重繪時，按鈕會先以 disabled=True 呈現，
# 然後才進入長時間 AI 工作。
# ------------------------------------------------------------
if _v12_step04_frontend_enabled:
    _v12_frontend_generate_request = render_frontend_generate_button(
        data={
            "storageKey": "sticker-studio-next.step04-formal.v1",
            "disabled": _generation_locked,
        },
        key="v12_step04_frontend_generate_formal",
    )
    _generate_clicked = isinstance(_v12_frontend_generate_request, dict)
    if _generate_clicked:
        _v12_apply_step04_frontend_snapshot(
            _v12_frontend_generate_request.get("state", {})
        )
        # The request supplied the current browser-only values.  All following
        # validation and generation code therefore consumes the same canonical
        # fields as the former Streamlit button.
        _sticker_mode = _v12_sticker_mode_value()
        texts = get_texts()
        expression_specs = _v12_get_expression_specs()
else:
    _generate_clicked = st.button(
        t("generating") if _generation_locked else t("generate"),
        type="primary",
        use_container_width=True,
        disabled=_generation_locked,
        key="v11_generate_4x2",
    )

if _generate_clicked:
    if not st.session_state.uploaded_image_bytes:
        st.warning(t("upload_first"))
        st.stop()

    # 文字模式至少需要一格文字；無文字表情模式則由每格的表情／特效方向生成，
    # 不應共用「必須填貼圖文字」的驗證。
    if _sticker_mode == "text" and not any(str(text or "").strip() for text in texts):
        st.warning(t("text_first"))
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

if isinstance(_generation_feedback, dict):
    if _generation_feedback.get("success"):
        st.success("🎉 生成完成，現在可以再次生成，或前往下方確認成果。")
    else:
        st.error("❌ 這次生成未完成，現在可以立即重新嘗試。")

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
    _generation_succeeded = False

    try:
        _beta_live_session = _beta_current_user()
        if not _beta_live_session or not _beta_job_id:
            raise RuntimeError("missing_beta_reservation")
        _server_openai_key = _beta_site_openai_key()
        if not _server_openai_key:
            raise RuntimeError("beta_generation_not_enabled")
        _generation_client = OpenAI(api_key=_server_openai_key)

        with st.spinner("🎬 貼圖小劇場開拍中，請稍候……"):
            ib = BytesIO(st.session_state.uploaded_image_bytes)
            # 這是使用者可理解的製作階段提示，不假裝是 AI 的精準完成百分比。
            # 180 秒後逾時；不自動 retry，沿用原本的例外／退款路徑。
            _showtime = st.empty()
            _status = st.empty()
            _started_at = time.monotonic()

            def _show_generation_scene(scene, title, note, percent):
                _stage_end = min(100, percent + (3 if percent < 96 else 0))
                _showtime.markdown(
                    f"""
                    <div class="v12-generation-showtime">
                      <div class="v12-generation-showtime-top">
                        <span>{title}</span><span class="v12-generation-showtime-scene">{scene}</span>
                      </div>
                      <div class="v12-generation-showtime-track"><div class="v12-generation-showtime-fill" style="--v12-stage-start:{percent}%;--v12-stage-end:{_stage_end}%"></div></div>
                      <div class="v12-generation-showtime-note"><span>{note}</span><span class="v12-generation-showtime-dots" aria-label="製作中"><i></i><i></i><i></i></span></div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            _show_generation_scene(
                "第 1 幕／5", "🎬 導演正在替八格貼圖排戲",
                "先把人物、文字和動作指令排成一場小劇場。", 8,
            )
            _status.info("🎨 正在分析你的照片……")

            try:
                _show_generation_scene(
                    "第 2 幕／5", "🎨 美術組正在調配這次的貼圖畫風",
                    "畫面已送進 AI 畫室，這一幕會需要稍微等候。", 31,
                )
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
                    _show_generation_scene(
                        "第 2 幕／5", "🖼️ 美術組正在對照人物與畫風參考",
                        "人物身份以你的照片為準，畫風只負責幫這場戲上色。", 38,
                    )
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
                _show_generation_scene(
                    "第 3 幕／5", "🪄 AI 正在畫布上施展貼圖魔法",
                    "角色、表情與小動作正在合成為 4×2 的完整畫面。", 57,
                )
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

                _show_generation_scene(
                    "第 4 幕／5", "📦 圖片快遞抵達，正在小心拆封",
                    "八格原始總圖已送回，正在確認每一格都完整到位。", 82,
                )
                _status.info("🖼️ 原始總圖處理中……")
                _show_generation_scene(
                    "第 5 幕／5", "✂️ 剪刀手正在幫每格安排裁切空間",
                    "最後整理完成後，就能直接進入裁切確認。", 96,
                )
                _status.info("✂️ 正在準備 8 張貼圖……")
                _show_generation_scene(
                    "殺青！", "🎉 八格貼圖小劇場完成",
                    "原始總圖已準備好，下一步可以檢視並微調裁切線。", 100,
                )
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
            _grid_bytes, _grid_crop_room_applied = _v12_prepare_generated_grid(
                raw,
                add_crop_room=bool(_auto_crop_room),
            )
            st.session_state["v12_grid_breathing_room_applied"] = bool(
                _grid_crop_room_applied
            )
            st.session_state["v12_grid_breathing_room_percent"] = (
                14 if _grid_crop_room_applied else 0
            )
            if _grid_crop_room_applied:
                _show_generation_scene(
                    "收工整理", "✂️ 剪刀手正在保留每格裁切空間",
                    "已替透明貼圖安排好後續手動微調所需的安全位置。", 100,
                )
                _status.info("✂️ 正在為每張貼圖保留裁切留白……")
            st.session_state.generated_4x2_bytes = _grid_bytes
            st.session_state.crop_boxes = None
            # A lower-page apply request belongs only to its original grid.
            # Never replay it when a new 4×2 image has just been generated.
            st.session_state.pop("v12_crop_apply_request_token", None)
            _generation_succeeded = True
            if _sticker_mode == "expression":
                _v12_commit_random_expression_recipes()
            # 每次成功的 4×2 都嘗試建立一卷私人的「創作膠卷」。
            # 這是背景工作；生成圖一完成就先交還給使用者，不等待轉檔與上傳。
            try:
                _v12_queue_current_creator_reel(_beta_live_session)
            except Exception as exc:
                st.session_state["v12_creator_reel_generation_feedback"] = {
                    "saved": False,
                    "reason": "save_retry",
                    "diagnostic": str(exc or "creator_reel_save_failed")[:100],
                }
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
            _creator_reel_feedback = st.session_state.get(
                "v12_creator_reel_generation_feedback", {}
            )
            if _creator_reel_feedback.get("saved"):
                st.caption(
                    "🎞️ 已自動保存一卷創作膠卷（"
                    f"{_creator_reel_feedback['active_count']}／"
                    f"{_creator_reel_feedback['limit']} 卷）。"
                )
            elif _creator_reel_feedback.get("reason") == "subscription_required":
                st.info(t("creator_reel_not_saved_subscription"))
            elif _creator_reel_feedback.get("reason") == "save_retry":
                st.warning(t("creator_reel_not_saved_retry"))
            elif _creator_reel_feedback.get("reason") == "saving":
                st.caption(t("creator_reel_saving"))

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
        st.session_state["v11_generation_release_feedback"] = {
            "success": _generation_succeeded,
        }
        # Streamlit rendered the disabled state before the long-running call.
        # Immediately redraw after the terminal result so the next generation
        # button becomes available in seconds instead of waiting for a manual
        # page interaction.
        st.rerun()

# ------------------------------------------------------------
# STEP 10C native component
# ------------------------------------------------------------
# ------------------------------------------------------------
if (
    not st.session_state.generated_4x2_bytes
    and _v12_get_sticker_basket()
):
    st.divider()
    v10_section(t("basket_section"), "#9b59b6")
    st.caption(t("basket_loaded_intro"))
    _v12_render_sticker_basket_workspace()


if st.session_state.generated_4x2_bytes:
    st.divider()
    v10_section(t("section_results"), "#32c6c0")
    st.caption(t("results_intro"))
    _v12_collect_creator_reel_save()
    _results_reel_feedback = st.session_state.get(
        "v12_creator_reel_generation_feedback", {}
    )
    if _results_reel_feedback.get("saved"):
        st.success(
            "🎞️ 本次 4×2 已自動保存為創作膠卷（"
            f"{_results_reel_feedback.get('active_count', 0)}／"
            f"{_results_reel_feedback.get('limit', 0)} 卷）。"
        )
    elif _results_reel_feedback.get("reason") == "subscription_required":
        st.info(t("creator_reel_not_saved_subscription"))
    elif _results_reel_feedback.get("reason") == "saving":
        st.info(t("creator_reel_saving"))
    elif _results_reel_feedback.get("reason") == "save_retry":
        st.warning(t("creator_reel_not_saved_retry"))
        st.caption(
            t(
                "creator_reel_diagnostic",
                stage=_v12_creator_reel_diagnostic_label(
                    _results_reel_feedback.get("diagnostic", "")
                ),
            )
        )
        if st.button(
            t("creator_reel_retry_button"),
            key="v12_creator_reel_retry_current_grid",
            type="primary",
        ):
            _retry_finished = False
            try:
                _retry_session = _beta_current_user() if _beta_config() else None
                if not _retry_session:
                    raise RuntimeError("creator_reel_not_authenticated")
                with st.spinner(t("creator_reel_retrying")):
                    _v12_save_current_creator_reel(_retry_session)
                _retry_finished = True
            except Exception as exc:
                st.session_state["v12_creator_reel_generation_feedback"] = {
                    "saved": False,
                    "reason": "save_retry",
                    "diagnostic": str(exc or "creator_reel_save_failed")[:100],
                }
                _retry_finished = True
            if _retry_finished:
                st.rerun()

    src = Image.open(BytesIO(st.session_state.generated_4x2_bytes)).convert("RGBA")
    w, h = src.size

    if st.session_state.crop_boxes is None:
        st.session_state.crop_boxes = base_boxes(w, h)

    # STEP 07 owns the source result preview; STEP 08 is now only for the
    # interactive crop guides and final outputs.
    v10_subsection(t("crop_original"), "#32c6c0")
    _orig_left, _orig_center, _orig_right = st.columns([1, 5, 1])
    with _orig_center:
        st.image(
            st.session_state.generated_4x2_bytes,
            caption=t("crop_original_caption", width=w, height=h),
            use_container_width=True,
        )

    st.divider()
    v10_section(t("section_crop"), "#e74c3c")
    st.info(t("crop_drag_help"))
    if st.session_state.pop("v12_crop_guides_saved", False):
        st.success("✂️ 裁切範圍已儲存；加入作品籃與後續下載都會使用這次調整。")

    st.caption(t("crop_canvas_caption"))
    if st.session_state.get("v12_grid_breathing_room_applied", False):
        st.caption(
            t(
                "crop_breathing",
                percent=int(st.session_state.get("v12_grid_breathing_room_percent", 15) or 15),
            )
        )

    _CROP_HTML_TEMPLATE = '<!doctype html>\n<html><head><meta charset="utf-8">\n<style>\n*{box-sizing:border-box}\nbody{margin:0;font-family:Arial,"Microsoft JhengHei",sans-serif;color:#333}\n.help{font-size:14px;line-height:1.6;background:#f5f7fa;padding:10px;border-radius:8px}\n.viewer{position:relative;width:720px;max-width:100%;margin:10px auto 0;border:1px solid #bbb;border-radius:8px;overflow:hidden;background:#eee}\n#cv{display:block;width:720px;max-width:100%;height:auto;touch-action:none;user-select:none}\n.status{font-size:28px;line-height:1.45;font-weight:700;padding:12px 0;color:#fff;text-align:center}\n.actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}\nbutton{border:0;border-radius:8px;padding:9px 13px;cursor:pointer}\nbutton.primary{background:#222;color:#fff}\nbutton.lock{background:#333;color:#fff}\nbutton:disabled{opacity:.45;cursor:not-allowed}\n.coords{font:20px/1.6 Consolas,monospace;white-space:pre-wrap;background:#1b2028;color:#fff;padding:14px;border-radius:10px;text-align:center}\n.legend{font-size:26px;line-height:1.5;font-weight:700;padding:10px 0;color:#fff;text-align:center}\n</style></head><body>\n<div class="help">\n<b>📍 定位點裁切</b><br>\n3 條上半部＋3 條下半部垂直定位線＋1 條水平定位線。\n上下 6 條藍色垂直線可各自獨立拖曳；按「🔒 鎖定定位」後產生 01～08 裁切框。\n</div>\n<div class="viewer"><canvas id="cv"></canvas></div>\n<div class="status" id="status">正在建立定位線……</div>\n<div class="legend">🔴 固定外框\u3000🔵 上下 6 條獨立垂直分隔線\u3000🟠 水平分隔線\u3000｜\u3000🔒 鎖定後可進行裁切</div>\n<div class="actions">\n<button onclick="resetGuides()">↩️ 恢復分隔線位置</button>\n<button id="lockBtn" class="lock" onclick="toggleLock()">🔒 鎖定定位</button>\n<button id="downloadBtn" class="primary" onclick="downloadAll()" disabled>✂️ 裁切並依序下載01～08(手機下載)</button>\n</div>\n\n<script>\nconst B64="__IMAGE_B64__", IW=__IW__, IH=__IH__;\nconst cv=document.getElementById("cv"),ctx=cv.getContext("2d");\nconst statusEl=document.getElementById("status"),coordsEl=document.getElementById("coords");\nconst lockBtn=document.getElementById("lockBtn"),downloadBtn=document.getElementById("downloadBtn");\nconst colors=["#ff4040","#3987ff","#32ad61","#ff922e"];\nlet scale=1,locked=false,active=-1,startX=0,startY=0,snap=null;\n\nfunction clamp(v,a,b){return Math.max(a,Math.min(b,v))}\nfunction guidesDefault(){\n  return {\n    xTop:[IW/4,IW/2,3*IW/4],\n    xBottom:[IW/4,IW/2,3*IW/4],\n    y:[0,IH/2,IH]\n  };\n}\nlet g=guidesDefault();\n\nconst image=new Image();\nimage.src="data:image/png;base64,"+B64;\n\nfunction resize(){\n  const viewer=cv.parentElement;\n  const mw=Math.min(720,Math.max(320,viewer.clientWidth||720));\n  cv.width=Math.round(mw);\n  cv.height=Math.round(mw*IH/IW);\n  scale=cv.width/IW;\n  draw();\n}\n\nfunction boxes(){\n  const out=[];\n  const xt=[0,g.xTop[0],g.xTop[1],g.xTop[2],IW];\n  const xb=[0,g.xBottom[0],g.xBottom[1],g.xBottom[2],IW];\n  for(let c=0;c<4;c++) out.push([xt[c],g.y[0],xt[c+1],g.y[1]]);\n  for(let c=0;c<4;c++) out.push([xb[c],g.y[1],xb[c+1],g.y[2]]);\n  return out;\n}\n\nfunction draw(){\n  ctx.clearRect(0,0,cv.width,cv.height);\n  ctx.drawImage(image,0,0,cv.width,cv.height);\n\n  // Slight guide shading.\n  ctx.save();\n  ctx.fillStyle="rgba(255,255,255,.08)";\n  ctx.fillRect(0,0,cv.width,cv.height);\n  ctx.restore();\n\n  // 上下各 3 條：六條垂直線完全獨立。\n  for(let i=0;i<3;i++){\n    const xt=g.xTop[i]*scale;\n    const xb=g.xBottom[i]*scale;\n    ctx.save();\n    ctx.strokeStyle="#3987ff";\n    ctx.lineWidth=2.5;\n    ctx.setLineDash([10,7]);\n    ctx.beginPath();ctx.moveTo(xt,0);ctx.lineTo(xt,g.y[1]*scale);ctx.stroke();\n    ctx.beginPath();ctx.moveTo(xb,g.y[1]*scale);ctx.lineTo(xb,cv.height);ctx.stroke();\n    ctx.setLineDash([]);\n    ctx.fillStyle="#3987ff";\n    ctx.fillRect(xt-5,4,10,18);\n    ctx.fillRect(xb-5,g.y[1]*scale+4,10,18);\n    ctx.restore();\n  }\n\n  // 左右外框固定。\n  ctx.save();\n  ctx.strokeStyle="#ff4040";\n  ctx.lineWidth=2.5;\n  ctx.setLineDash([10,7]);\n  ctx.beginPath();ctx.moveTo(0,0);ctx.lineTo(0,cv.height);ctx.stroke();\n  ctx.beginPath();ctx.moveTo(cv.width,0);ctx.lineTo(cv.width,cv.height);ctx.stroke();\n  ctx.setLineDash([]);\n  ctx.fillStyle="#ff4040";\n  ctx.fillRect(0,4,10,18);\n  ctx.fillRect(cv.width-10,4,10,18);\n  ctx.restore();  // Draw 3 horizontal guide lines.\n  for(let i=0;i<3;i++){\n    const y=g.y[i]*scale;\n    ctx.save();\n    ctx.strokeStyle="#ff922e";\n    ctx.lineWidth=i===1?2.5:2;\n    ctx.setLineDash([10,7]);\n    ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(cv.width,y);ctx.stroke();\n    ctx.setLineDash([]);\n    ctx.fillStyle="#ff922e";\n    ctx.fillRect(4,y-5,22,10);\n    ctx.restore();\n  }\n\n  // Number each derived cell; these are guide labels only.\n  const b=boxes();\n  const cellColors=["#ff4040","#3987ff","#32ad61","#a04bd0","#ff922e","#25aeb0","#db3f9b","#956c3c"];\n  for(let i=0;i<8;i++){\n    const x=b[i][0]*scale,y=b[i][1]*scale;\n    ctx.save();\n    ctx.font="bold 17px Arial";\n    ctx.lineWidth=3;\n    ctx.strokeStyle="rgba(255,255,255,.95)";\n    ctx.strokeText(String(i+1).padStart(2,"0"),x+8,y+21);\n    ctx.fillStyle=cellColors[i];\n    ctx.fillText(String(i+1).padStart(2,"0"),x+8,y+21);\n    ctx.restore();\n  }\n\n  updateCoords();\n}\n\nfunction updateCoords(){\n  // 座標資訊已從使用者介面移除；保留函式避免影響既有裁切流程。\n}\n\nfunction pointerPos(e){\n  const r=cv.getBoundingClientRect();\n  return {x:(e.clientX-r.left)*IW/r.width,y:(e.clientY-r.top)*IH/r.height};\n}\n\nfunction nearestGuide(p){\n  const tol=Math.max(18,18/scale);\n  let best=-1,bd=Infinity,type="";\n  const arr=p.y<g.y[1] ? g.xTop : g.xBottom;\n  const segType=p.y<g.y[1] ? "xTop" : "xBottom";\n  for(let i=0;i<3;i++){\n    const d=Math.abs(p.x-arr[i]);\n    if(d<tol&&d<bd){best=i;bd=d;type=segType;}\n  }\n  const dy=Math.abs(p.y-g.y[1]);\n  if(dy<tol&&dy<bd){best=1;bd=dy;type="y";}\n  return {index:best,type:type};\n}cv.addEventListener("pointerdown",e=>{\n  if(locked)return;\n  const p=pointerPos(e),h=nearestGuide(p);\n  if(h.index<0)return;\n  e.preventDefault();\n  try{cv.setPointerCapture(e.pointerId)}catch(_){}\n  active=h.index;startX=p.x;startY=p.y;snap={xTop:[...g.xTop],xBottom:[...g.xBottom],y:[...g.y],type:h.type};\n  statusEl.textContent="🖱️ 正在調整定位線……";\n});\n\ncv.addEventListener("pointermove",e=>{\n  if(active<0||locked)return;\n  e.preventDefault();\n  const p=pointerPos(e);\n  if(snap.type==="xTop" || snap.type==="xBottom"){\n    const arr=snap.type==="xTop" ? g.xTop : g.xBottom;\n    const snapArr=snap.type==="xTop" ? snap.xTop : snap.xBottom;\n    const min=30;\n    const lo=(active===0 ? 0 : arr[active-1])+min;\n    const hi=(active===2 ? IW : arr[active+1])-min;\n    arr[active]=clamp(snapArr[active]+(p.x-startX),lo,hi);\n  }else{\n    const min=30;\n    g.y[1]=clamp(snap.y[1]+(p.y-startY),g.y[0]+min,g.y[2]-min);\n  }\n  draw();\n});\n\ncv.addEventListener("pointerup",e=>{\n  if(active>=0){try{cv.releasePointerCapture(e.pointerId)}catch(_){}}\n  active=-1;\n  if(!locked)statusEl.textContent="✅ 定位線已更新";\n});\n\nfunction resetGuides(){\n  locked=false;\n  g=guidesDefault();\n  lockBtn.textContent="🔒 鎖定定位";\n  downloadBtn.disabled=true;\n  statusEl.textContent="↩️ 已恢復標準4×2定位";\n  draw();\n}\n\nfunction toggleLock(){\n  locked=!locked;\n  if(locked){\n    lockBtn.textContent="🔓 解鎖定位";\n    downloadBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，可以開始裁切";\n  }else{\n    lockBtn.textContent="🔒 鎖定定位";\n    downloadBtn.disabled=true;\n    statusEl.textContent="🛠️ 定位已解鎖，可以繼續微調";\n  }\n  draw();\n}\n\nfunction saveBlob(blob,name){\n  const u=URL.createObjectURL(blob),a=document.createElement("a");\n  a.href=u;a.download=name;document.body.appendChild(a);a.click();a.remove();\n  setTimeout(()=>URL.revokeObjectURL(u),1000);\n}\n\n// V10 STEP 10C.6：移植 V8「邊界連通背景移除」邏輯。\n// 只移除從圖片邊緣連通、且接近背景色的區域；不整片刪除人物/文字中的白色。\nfunction removeConnectedBackground(canvas, tolerance=30){\n  const ctx2=canvas.getContext("2d",{willReadFrequently:true});\n  const W=canvas.width,H=canvas.height;\n  const img=ctx2.getImageData(0,0,W,H);\n  const px=img.data;\n\n  const pts=[\n    [0,0],[W-1,0],[0,H-1],[W-1,H-1],\n    [Math.min(4,W-1),Math.min(4,H-1)],\n    [Math.max(0,W-5),Math.min(4,H-1)],\n    [Math.min(4,W-1),Math.max(0,H-5)],\n    [Math.max(0,W-5),Math.max(0,H-5)]\n  ];\n\n  let sr=0,sg=0,sb=0,count=0;\n  for(const [x,y] of pts){\n    const k=(y*W+x)*4;\n    if(px[k+3]===0) continue;\n    sr+=px[k]; sg+=px[k+1]; sb+=px[k+2]; count++;\n  }\n  if(count===0) return;\n\n  const bg=[sr/count,sg/count,sb/count];\n  const tol2=tolerance*tolerance;\n\n  function nearBg(k){\n    if(px[k+3]===0) return true;\n    const dr=px[k]-bg[0],dg=px[k+1]-bg[1],db=px[k+2]-bg[2];\n    return dr*dr+dg*dg+db*db<=tol2;\n  }\n\n  const seen=new Uint8Array(W*H);\n  const qx=[],qy=[];\n  let head=0;\n\n  function push(x,y){\n    if(x<0||x>=W||y<0||y>=H) return;\n    const n=y*W+x;\n    if(seen[n]) return;\n    seen[n]=1;\n    qx.push(x);qy.push(y);\n  }\n\n  for(let x=0;x<W;x++){push(x,0);push(x,H-1);}\n  for(let y=0;y<H;y++){push(0,y);push(W-1,y);}\n\n  while(head<qx.length){\n    const x=qx[head],y=qy[head++];\n    const k=(y*W+x)*4;\n    if(!nearBg(k)) continue;\n    px[k+3]=0;\n    push(x-1,y);push(x+1,y);push(x,y-1);push(x,y+1);\n  }\n\n  ctx2.putImageData(img,0,0);\n}\n\nfunction makeSticker(i){\n  const b=boxes()[i].map(v=>Math.round(v));\n  const x1=clamp(b[0],0,IW-2),y1=clamp(b[1],0,IH-2);\n  const x2=clamp(b[2],x1+2,IW),y2=clamp(b[3],y1+2,IH);\n  const cw=370,ch=320,sw=x2-x1,sh=y2-y1;\n  const rat=Math.min(cw/sw,ch/sh),nw=Math.max(1,Math.round(sw*rat)),nh=Math.max(1,Math.round(sh*rat));\n\n  const o=document.createElement("canvas");\n  o.width=cw;o.height=ch;\n  const octx=o.getContext("2d",{willReadFrequently:true});\n  octx.clearRect(0,0,cw,ch);\n\n  // 保留 V10 原本的定位點裁切、等比例縮放、置中。\n  octx.drawImage(\n    image,x1,y1,sw,sh,\n    Math.round((cw-nw)/2),Math.round((ch-nh)/2),nw,nh\n  );\n\n  // 只有勾選「透明背景 PNG」時才套用 V8 邏輯。\n  if(__TRANSPARENT__){\n    removeConnectedBackground(o,30);\n  }\n\n  return new Promise(r=>o.toBlob(r,"image/png"));\n}\n\nasync function downloadAll(){\n  if(!locked)return;\n  statusEl.textContent="⏳ 正在製作01～08 PNG……";\n  for(let i=0;i<8;i++){\n    saveBlob(await makeSticker(i),String(i+1).padStart(2,"0")+".png");\n    await new Promise(r=>setTimeout(r,180));\n  }\n  statusEl.textContent="🎉 01～08 已全部裁切並下載！";\n}\n\nimage.onload=()=>{\n  resize();\n  statusEl.textContent="✅ 定位線已建立：上 3 條＋下 3 條藍色垂直線可獨立拖曳";\n};\nwindow.addEventListener("resize",resize);\n</script></body></html>'
    _CROP_HTML_TEMPLATE = (
        _CROP_HTML_TEMPLATE.replace(
            ".actions{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0}\n"
            "button{border:0;border-radius:8px;padding:9px 13px;cursor:pointer}\n"
            "button.primary{background:#222;color:#fff}\n"
            "button.lock{background:#333;color:#fff}\n"
            "button:disabled{opacity:.45;cursor:not-allowed}",
            ".actions{display:flex;gap:10px;flex-wrap:wrap;margin:10px 0;align-items:stretch}\n"
            ".actions button{flex:1 1 190px;min-height:46px;border:0;border-radius:9px;padding:10px 16px;cursor:pointer;font-size:17px;font-weight:800;line-height:1.25}\n"
            "button.primary{background:#222;color:#fff}\n"
            "button.lock{background:#2f8edc;color:#fff}\n"
            "button.lock:not(:disabled):hover{background:#2479bd}\n"
            "#applyBtn{flex:1.35 1 300px;min-height:52px;background:#ff4f67;color:#fff;font-size:18px;box-shadow:0 4px 12px rgba(255,79,103,.28)}\n"
            "#applyBtn:not(:disabled):hover{background:#e53e57}\n"
            "button:disabled{opacity:.45;cursor:not-allowed}",
        ).replace(
            '<button id="lockBtn" class="lock" onclick="toggleLock()">🔒 鎖定定位</button>',
            '<button id="lockBtn" class="lock" onclick="toggleLock()">🔒（第一步）鎖定定位</button>',
        ).replace(
            'lockBtn.textContent="🔒 鎖定定位";',
            'lockBtn.textContent="🔒（第一步）鎖定定位";',
        )
    )

    # 本版不在 Canvas 直接下載；裁切結果會先加入下方作品暫存區，再一次打包。
    _CROP_HTML_TEMPLATE = _CROP_HTML_TEMPLATE.replace(
        '<button id="downloadBtn" class="primary" onclick="downloadAll()" disabled>✂️ 裁切並依序下載01～08(手機下載)</button>',
        '',
    ).replace(
        'const lockBtn=document.getElementById("lockBtn"),downloadBtn=document.getElementById("downloadBtn");',
        'const lockBtn=document.getElementById("lockBtn"),applyBtn=document.getElementById("applyBtn");',
    ).replace(
        '  downloadBtn.disabled=true;\n',
        '  applyBtn.disabled=true;\n',
    ).replace(
        '    downloadBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，可以開始裁切";',
        '    applyBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，請套用裁切範圍後加入作品籃";',
    ).replace(
        '<button id="lockBtn" class="lock" onclick="toggleLock()">🔒（第一步）鎖定定位</button>',
        '<button id="lockBtn" class="lock" onclick="toggleLock()">🔒（第一步）鎖定定位</button>\n<button id="applyBtn" class="primary" onclick="applyCrop()" disabled>✅（第二步）套用裁切並加入作品暫存區</button>',
    )
    _CROP_HTML_TEMPLATE = _CROP_HTML_TEMPLATE.replace(
        'let g=guidesDefault();',
        '''const INITIAL_BOXES=__BOXES__;
function guidesFromBoxes(raw){
  if(!Array.isArray(raw)||raw.length!==8)return guidesDefault();
  try{
    const n=raw.map(box=>box.map(Number));
    if(n.some(box=>box.length!==4||box.some(v=>!Number.isFinite(v))))return guidesDefault();
    return {
      xTop:[n[0][2],n[1][2],n[2][2]],
      xBottom:[n[4][2],n[5][2],n[6][2]],
      y:[n[0][1],n[0][3],n[4][3]]
    };
  }catch(_){return guidesDefault();}
}
let g=guidesFromBoxes(INITIAL_BOXES);
''',
    )
    _CROP_HTML_TEMPLATE = _CROP_HTML_TEMPLATE.replace(
        'let scale=1,locked=false,active=-1,startX=0,startY=0,snap=null;',
        'let scale=1,locked=false,active=-1,startX=0,startY=0,snap=null;',
    ).replace(
        '  g=guidesDefault();\n  lockBtn.textContent="🔒 鎖定定位";',
        '  g=guidesDefault();\n  lockBtn.textContent="🔒 鎖定定位";',
    ).replace(
        '    applyBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，請套用裁切範圍後加入作品籃";',
        '    applyBtn.disabled=false;\n    statusEl.textContent="🔒 定位已鎖定，請套用裁切範圍後加入作品籃";',
    ).replace(
        '    applyBtn.disabled=true;\n    statusEl.textContent="🛠️ 定位已解鎖，可以繼續微調";',
        '    applyBtn.disabled=true;\n    statusEl.textContent="🛠️ 定位已解鎖，可以繼續微調";',
    )
    _CROP_HTML_TEMPLATE = _CROP_HTML_TEMPLATE.replace(
        'function saveBlob(blob,name){',
        '''function applyCrop(){
  if(!locked)return;
  statusEl.textContent="🔒 正在套用裁切範圍並加入作品籃……";
  window.parent.postMessage({
    type:"v12-crop-save",
    value:JSON.stringify({action:"add_to_basket",gridHash:"__GRID_HASH__",boxes:boxes()})
  },"*");
}

window.addEventListener("message",event=>{
  if(event.data && event.data.type==="v12-crop-apply-current") applyCrop();
});

function saveBlob(blob,name){''',
    )
    for _crop_source, _crop_translation in _v12_crop_canvas_translations().items():
        _CROP_HTML_TEMPLATE = _CROP_HTML_TEMPLATE.replace(_crop_source, _crop_translation)
    _image_b64 = base64.b64encode(st.session_state.generated_4x2_bytes).decode("ascii")
    _boxes_json = __import__("json").dumps(st.session_state.crop_boxes, ensure_ascii=False)
    _crop_html = _CROP_HTML_TEMPLATE.replace("__IMAGE_B64__", _image_b64).replace("__IW__", str(int(w))).replace("__IH__", str(int(h))).replace("__BOXES__", _boxes_json).replace("__TRANSPARENT__", "true" if transparent else "false")
    _crop_html = _crop_html.replace(
        "__GRID_HASH__",
        hashlib.sha256(st.session_state.generated_4x2_bytes).hexdigest(),
    )
    _crop_saved_value = _V12_CROP_GUIDES_COMPONENT(
        html=_crop_html,
        frame_height=760,
        apply_request_token=str(
            st.session_state.get("v12_crop_apply_request_token", "") or ""
        ),
        key="v12_crop_guides_component",
        default=None,
    )
    if _crop_saved_value:
        try:
            _crop_saved_payload = (
                _crop_saved_value
                if isinstance(_crop_saved_value, dict)
                else json.loads(str(_crop_saved_value))
            )
            _crop_saved_hash = str(_crop_saved_payload.get("gridHash", "") or "")
            _crop_saved_boxes = _crop_saved_payload.get("boxes")
            _crop_saved_action = str(_crop_saved_payload.get("action", "") or "")
            _current_grid_hash = hashlib.sha256(
                st.session_state.generated_4x2_bytes
            ).hexdigest()
            _crop_saved_token = json.dumps(
                [_crop_saved_hash, _crop_saved_boxes],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if secrets.compare_digest(_crop_saved_hash, _current_grid_hash):
                _applied_crop_boxes = _v12_valid_crop_boxes(
                    _crop_saved_boxes, w, h
                )
                _needs_rerun = False
                if _crop_saved_token != st.session_state.get("v12_crop_last_saved_token"):
                    st.session_state.crop_boxes = _applied_crop_boxes
                    st.session_state["v12_crop_last_saved_token"] = _crop_saved_token
                    st.session_state["v12_crop_guides_saved"] = True
                    _needs_rerun = True
                _crop_action_token = f"{_crop_saved_action}:{_crop_saved_token}"
                if (
                    _crop_saved_action == "add_to_basket"
                    and _crop_action_token
                    != st.session_state.get("v12_crop_last_handled_action_token")
                ):
                    # Custom component values persist across Streamlit reruns.
                    # Consume each browser action exactly once so the rerun
                    # needed to show the basket cannot trigger an infinite
                    # add/rerun loop.
                    st.session_state[
                        "v12_crop_last_handled_action_token"
                    ] = _crop_action_token
                    _added, _message = _v12_add_current_grid_to_sticker_basket(
                        st.session_state.get("generated_4x2_bytes"),
                        _applied_crop_boxes,
                    )
                    st.session_state["v12_crop_basket_feedback"] = {
                        "added": _added,
                        "message": _message,
                    }
                    _needs_rerun = True
                if _needs_rerun:
                    st.rerun()
        except (TypeError, ValueError, json.JSONDecodeError):
            pass

    st.divider()
    v10_section(t("basket_section"), "#9b59b6")
    _crop_basket_feedback = st.session_state.pop("v12_crop_basket_feedback", None)
    if isinstance(_crop_basket_feedback, dict):
        if _crop_basket_feedback.get("added"):
            st.success(str(_crop_basket_feedback.get("message") or ""))
        else:
            st.info(str(_crop_basket_feedback.get("message") or ""))
    st.markdown(
        '<div style="font-size:22px;line-height:1.55;font-weight:700;'
        'text-align:center;color:#fff;margin:10px 0 16px;">'
        f'{t("basket_intro")}'
        '</div>',
        unsafe_allow_html=True,
    )
    st.caption(t("basket_session_only"))
    _v12_render_sticker_basket_workspace()
    _basket_library_col = st.columns([1, 2, 1])[1]
    with _basket_library_col:
        if st.button(
            t("basket_go_library"),
            key="v12_basket_library",
            type="primary",
            use_container_width=True,
        ):
            _v12_preserve_creation_draft_for_navigation()
            _public02a_sync_style_bank_from_widgets()
            _save_v10_presets()
            st.session_state["v12_view"] = "library"
            st.rerun()

    # 舊版 01～08 立即打包區保留在原始碼中作為回溯參考，但不再執行。
    # 新版以作品暫存區作為唯一的裁切後工作流程。
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
        t("nav_library"),
        key="v12_footer_library",
        use_container_width=True,
    ):
        _v12_preserve_creation_draft_for_navigation()
        _public02a_sync_style_bank_from_widgets()
        _save_v10_presets()
        st.session_state["v12_view"] = "library"
        st.rerun()

    st.markdown('<div style="height: 10px;"></div>', unsafe_allow_html=True)

    st.link_button(
        t("line_creator_link"),
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
        f"""
        <div class="v12-settings-shell">
            <div class="v12-settings-title">{t("settings_title")}</div>
            <div class="v12-settings-subtitle">
                {t("settings_subtitle")}
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

        /* 匯出／匯入收合為兩個無色的全寬外框按鈕。 */
        div.st-key-v12_settings_export_card,
        div.st-key-v12_settings_import_card{
            width:min(620px, calc(100vw - 2rem));
            max-width:620px;
            margin:0 auto 14px;
            padding:0;
            box-sizing:border-box;
        }

        div.st-key-v12_settings_export_card [data-testid="stExpander"],
        div.st-key-v12_settings_import_card [data-testid="stExpander"]{
            border:1px solid #4b5563 !important;
            border-radius:12px !important;
            background:#1c222c !important;
            overflow:hidden;
        }

        div.st-key-v12_settings_export_card [data-testid="stExpander"] summary,
        div.st-key-v12_settings_import_card [data-testid="stExpander"] summary{
            min-height:52px !important;
            display:flex !important;
            align-items:center !important;
            justify-content:center !important;
            padding:0 18px !important;
            font-size:18px !important;
            font-weight:800 !important;
        }

        div.st-key-v12_settings_export_card [data-testid="stExpander"] summary p,
        div.st-key-v12_settings_import_card [data-testid="stExpander"] summary p{
            color:#ffffff !important;
            font-size:18px !important;
            font-weight:800 !important;
            text-align:center !important;
        }

        div.st-key-v12_settings_export_card [data-testid="stExpanderDetails"],
        div.st-key-v12_settings_import_card [data-testid="stExpanderDetails"]{
            padding:0 16px 16px !important;
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
                margin-bottom:12px;
            }

            div.st-key-v12_settings_export_card [data-testid="stExpander"] summary,
            div.st-key-v12_settings_import_card [data-testid="stExpander"] summary{
                min-height:50px !important;
                font-size:17px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # ============================================================
    # 📤 匯出按鈕
    # ============================================================
    with st.container(key="v12_settings_export_card"):
        with st.expander(t("export_custom"), expanded=False):
            st.download_button(
                label=t("export_custom"),
                data=export_data,
                file_name="LINE貼圖工具_自定義設定.json",
                mime="application/json",
                use_container_width=True,
                key="public02a_export_settings",
            )

    # ============================================================
    # 📥 匯入按鈕
    # ============================================================
    with st.container(key="v12_settings_import_card"):
        with st.expander(t("import_custom"), expanded=False):
            # 使用原生 uploader，不再對它的內部 button 做文字或定位覆蓋。
            imported = st.file_uploader(
                t("import_custom"),
                type=["json"],
                key="public02a_import_settings",
                help=t("import_help"),
                label_visibility="collapsed",
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
                st.success(t("import_success"))

        except Exception as exc:
            st.error(t("import_failed", error=exc))


# PUBLIC STEP 02A｜顯示個人設定匯出／匯入
_public02a_settings_panel()


# A shortcut selected from My Works returns through Streamlit's normal
# in-page navigation, then scrolls to the requested creation step.
_v12_scroll_target = str(st.session_state.pop("v12_scroll_target", "") or "")
if re.fullmatch(r"v12-step-0[1-8]", _v12_scroll_target):
    components.html(
        f"""
        <script>
        (() => {{
          const targetId = {json.dumps(_v12_scroll_target)};
          let attempts = 0;
          const scrollToTarget = () => {{
            const target = window.parent.document.getElementById(targetId);
            if (target) {{
              target.scrollIntoView({{behavior: "smooth", block: "start"}});
              return;
            }}
            if (attempts++ < 20) window.setTimeout(scrollToTarget, 100);
          }};
          window.setTimeout(scrollToTarget, 100);
        }})();
        </script>
        """,
        height=0,
    )
