"""Standalone, no-backend test bed for the STEP 04 frontend editor.

This file intentionally does not import the production app, Supabase helpers,
or generation code.  It is safe to deploy as a separate Streamlit app for
testing browser-side mode switching and draft persistence only.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st
import streamlit.components.v2 as components


st.set_page_config(
    page_title="STEP 04 前端互動測試｜貼圖工作室",
    page_icon="🧪",
    layout="wide",
)

st.markdown(
    """
    <style>
      .block-container { max-width: 1180px; padding-top: 2.3rem; }
      .step04-test-note {
        padding: .9rem 1rem; border-radius: 12px;
        border: 1px solid rgba(87, 182, 255, .42);
        background: rgba(53, 139, 205, .13); color: #d9efff;
      }
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🧪 STEP 04 前端互動測試版")
st.markdown(
    """
    <div class="step04-test-note">
      這是獨立測試頁。文字／表情模式切換、欄位輸入與勾選都只保存在目前瀏覽器；
      不會登入、不會生成、不會扣額度，也不會寫入「我的作品」。<br>
      只有按下「同步測試設定」時，才會將目前草稿回傳給這個測試頁顯示。
    </div>
    """,
    unsafe_allow_html=True,
)


EDITOR_HTML = '<div class="step04-editor-root"></div>'

EDITOR_CSS = r"""
:host { display:block; width:100%; }
.step04-editor-root { color:#eef5ff; font-family:inherit; }
.s4-shell { margin-top:18px; padding:20px; border:1px solid #365470; border-radius:18px;
  background:linear-gradient(145deg, rgba(19,34,54,.96), rgba(25,35,56,.96)); }
.s4-mode-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-bottom:15px; }
.s4-mode { min-height:55px; border:1px solid #55708d; border-radius:12px; cursor:pointer;
  background:#1d2b40; color:#d8e6f6; font-size:18px; font-weight:800; }
.s4-mode.active { background:#ff5878; border-color:#ff9aab; color:white; box-shadow:0 5px 18px rgba(255,88,120,.24); }
.s4-heading { padding:13px 16px; border:1px solid #258fda; border-radius:12px; color:#eaf7ff;
  background:rgba(25,109,172,.17); font-size:20px; font-weight:850; }
.s4-caption { color:#a9b9cd; margin:10px 2px 18px; line-height:1.55; }
.s4-source-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:14px 0 10px; }
.s4-source { border:1px solid #54708c; border-radius:10px; min-height:45px; background:#24354b; color:#dceafa; font:inherit; font-weight:800; cursor:pointer; }
.s4-source.active { border-color:#65c7ff; background:rgba(37,124,186,.48); color:#fff; }
.s4-source-panel { margin:0 0 18px; padding:14px; border:1px solid rgba(96,169,220,.38); border-radius:12px; background:rgba(28,56,82,.45); }
.s4-panel-title { color:#e9f7ff; font-size:16px; font-weight:850; margin-bottom:9px; }
.s4-panel-grid { display:grid; grid-template-columns:1.3fr 1fr; gap:10px; }
.s4-small-button { border:1px solid #5aaee2; border-radius:8px; background:#286eaa; color:#fff; padding:8px 11px; font:inherit; font-weight:800; cursor:pointer; }
.s4-candidates { display:grid; grid-template-columns:repeat(2, minmax(0,1fr)); gap:7px; margin-top:10px; }
.s4-candidate { display:flex; gap:7px; align-items:flex-start; padding:8px; border:1px solid rgba(111,148,181,.36); border-radius:8px; color:#dceafa; font-size:13px; cursor:pointer; }
.s4-candidate input { margin-top:2px; accent-color:#ff5878; }
.s4-message { margin-top:9px; color:#ffd4dc; font-size:13px; font-weight:700; }
.s4-pool-list { margin:8px 0 0; color:#b8cbe0; font-size:13px; line-height:1.55; }
.s4-grid { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:14px; }
.s4-card { min-width:0; padding:13px; border:1px solid rgba(104,136,168,.55); border-radius:12px;
  background:rgba(20,34,54,.82); }
.s4-card-title { margin-bottom:9px; font-size:15px; font-weight:850; color:#f2f7ff; }
.s4-label-row { display:flex; justify-content:space-between; align-items:center; gap:8px; margin:8px 0 5px; color:#d8e7f7; font-size:13px; font-weight:700; }
.s4-input, .s4-area { width:100%; box-sizing:border-box; border:1px solid #496582; border-radius:8px;
  background:#243248; color:#f4f8ff; padding:9px 10px; font:inherit; }
.s4-area { min-height:64px; resize:vertical; }
.s4-toggle { display:flex; gap:7px; align-items:center; color:#d9e8f7; font-size:13px; font-weight:700; cursor:pointer; }
.s4-toggle input { width:17px; height:17px; accent-color:#ff5878; }
.s4-subfield { margin-top:10px; padding-top:9px; border-top:1px solid rgba(126,157,186,.26); }
.s4-action-row { display:flex; gap:10px; margin-top:20px; align-items:center; flex-wrap:wrap; }
.s4-sync, .s4-reset { border-radius:10px; padding:11px 17px; font:inherit; font-weight:850; cursor:pointer; }
.s4-sync { border:1px solid #45b4ff; background:#237cba; color:#fff; }
.s4-reset { border:1px solid #667c96; background:#27374d; color:#dce8f4; }
.s4-local { color:#9fb2c9; font-size:13px; }
.s4-count { margin-top:15px; border-radius:10px; padding:10px 12px; background:rgba(57,136,205,.15); color:#9fd8ff; font-weight:750; }
@media (max-width: 820px) { .s4-grid { grid-template-columns:repeat(2, minmax(0,1fr)); } .s4-panel-grid { grid-template-columns:1fr; } }
@media (max-width: 520px) { .s4-shell { padding:13px; } .s4-grid { grid-template-columns:1fr; } .s4-mode { font-size:15px; } }
"""

EDITOR_JS = r"""
export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  const root = parentElement.querySelector('.step04-editor-root') || parentElement;
  const storageKey = data.storageKey;
  const emptySlots = () => Array.from({length: 8}, () => ({
    text:'', actionEnabled:false, action:'', colorEnabled:false, colorText:'',
    emotion:'', random:false, expressionActionEnabled:false, expressionAction:''
  }));
  const AI_ENDINGS = [
    '，今天也要好好加油！', '，先深呼吸一下～', '，我來陪你一起撐住。', '，今天的你已經很棒了！',
    '，笑一個就會更好。', '，慢慢來也沒有關係。', '，給自己一個大大的讚！', '，事情會一步一步變好的。',
    '，先休息一下再出發。', '，有我在，別擔心！', '，今天也要保持可愛。', '，一起把煩惱甩開吧！',
    '，這次一定沒問題。', '，辛苦了，抱一下！', '，現在就出發吧！', '，你值得被好好鼓勵。'
  ];
  const RANDOM_POOLS = {
    '日常': ['早安！','午安～','晚安！','收到！','沒問題！','等等我～','辛苦了！','謝謝你！','我懂！','太好了！','先這樣～','交給我吧！','等等見！','加油！','好耶！','原來如此！'],
    '搞笑': ['我先笑一下。','太扯了吧！','你認真的嗎？','等等，讓我消化。','我投降！','這合理嗎？','笑不活了～','先不要鬧！','好像哪裡怪怪的。','我看不懂，但我大受震撼。','真的假的？','這次換我傻眼。','有夠荒謬！','我先躲一下。','再說一次？','好，算你贏。'],
    '療癒': ['慢慢來就好。','今天也辛苦了。','抱抱你。','你已經做得很好。','休息一下吧。','一切都會好的。','別忘了照顧自己。','我在這裡。','給你一點溫柔。','沒事的～','好好吃飯喔。','先喝口水。','明天再繼續。','請對自己好一點。','陪你一起。','你不孤單。']
  };
  const defaultState = () => ({
    mode:'text', sourcePanel:'', aiTopic:'', aiTone:'溫暖鼓勵', aiCandidates:[], aiSelected:[],
    randomPool:'日常', randomCustomDraft:'', randomCustomPhrases:[], message:'', slots:emptySlots(), version:2
  });
  const normalize = (raw) => {
    const base = defaultState();
    if (!raw || typeof raw !== 'object') return base;
    Object.assign(base, raw);
    base.mode = raw.mode === 'expression' ? 'expression' : 'text';
    base.sourcePanel = raw.sourcePanel === 'ai' || raw.sourcePanel === 'random' ? raw.sourcePanel : '';
    base.aiTopic = String(raw.aiTopic || '');
    base.aiTone = ['溫暖鼓勵','自然日常','輕鬆搞笑'].includes(raw.aiTone) ? raw.aiTone : '溫暖鼓勵';
    base.aiCandidates = Array.isArray(raw.aiCandidates) ? raw.aiCandidates.map(value => String(value)).slice(0, 16) : [];
    base.aiSelected = Array.isArray(raw.aiSelected) ? [...new Set(raw.aiSelected.map(Number).filter(index => Number.isInteger(index) && index >= 0 && index < base.aiCandidates.length))].slice(0, 8) : [];
    base.randomPool = Object.prototype.hasOwnProperty.call(RANDOM_POOLS, raw.randomPool) || raw.randomPool === '自訂' ? raw.randomPool : '日常';
    base.randomCustomDraft = String(raw.randomCustomDraft || '');
    base.randomCustomPhrases = Array.isArray(raw.randomCustomPhrases) ? raw.randomCustomPhrases.map(value => String(value).trim()).filter(Boolean).slice(0, 50) : [];
    base.message = String(raw.message || '');
    if (Array.isArray(raw.slots)) base.slots = base.slots.map((slot, i) => ({...slot, ...(raw.slots[i] || {})}));
    return base;
  };
  let state;
  try { state = normalize(JSON.parse(localStorage.getItem(storageKey) || 'null')); }
  catch (_) { state = defaultState(); }
  const save = () => localStorage.setItem(storageKey, JSON.stringify(state));
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const checked = value => value ? 'checked' : '';

  const textCard = (slot, index) => `
    <section class="s4-card">
      <div class="s4-card-title">圖片 ${String(index + 1).padStart(2, '0')}</div>
      <div class="s4-label-row"><span>貼圖文字</span></div>
      <input class="s4-input" data-field="text" data-index="${index}" value="${esc(slot.text)}" placeholder="輸入貼圖文字">
      <div class="s4-subfield">
        <label class="s4-toggle"><input type="checkbox" data-field="actionEnabled" data-index="${index}" ${checked(slot.actionEnabled)}> 🎬 啟用動作指令</label>
        ${slot.actionEnabled ? `<div class="s4-label-row"><span>輸入希望動作指令</span></div><textarea class="s4-area" data-field="action" data-index="${index}" placeholder="例如：揮手、比讚、跳起來">${esc(slot.action)}</textarea>` : ''}
      </div>
      <div class="s4-subfield">
        <label class="s4-toggle"><input type="checkbox" data-field="colorEnabled" data-index="${index}" ${checked(slot.colorEnabled)}> 🎨 啟用局部文字上色</label>
        ${slot.colorEnabled ? `<div class="s4-label-row"><span>要上色的文字片段</span></div><input class="s4-input" data-field="colorText" data-index="${index}" value="${esc(slot.colorText)}" placeholder="例如：加油">` : ''}
      </div>
    </section>`;

  const expressionCard = (slot, index) => `
    <section class="s4-card">
      <div class="s4-card-title">圖片 ${String(index + 1).padStart(2, '0')}</div>
      <div class="s4-label-row"><span>表情欄位</span><label class="s4-toggle"><input type="checkbox" data-field="random" data-index="${index}" ${checked(slot.random)}> 隨機</label></div>
      <input class="s4-input" data-field="emotion" data-index="${index}" value="${esc(slot.emotion)}" placeholder="例如：驚喜、害羞" ${slot.random ? 'disabled' : ''}>
      <div class="s4-subfield">
        <label class="s4-toggle"><input type="checkbox" data-field="expressionActionEnabled" data-index="${index}" ${checked(slot.expressionActionEnabled)}> 🎬 啟用動作指令</label>
        ${slot.expressionActionEnabled ? `<div class="s4-label-row"><span>輸入希望動作指令</span></div><textarea class="s4-area" data-field="expressionAction" data-index="${index}" placeholder="例如：雙手抱頭、指向前方">${esc(slot.expressionAction)}</textarea>` : ''}
      </div>
    </section>`;

  const shuffle = values => {
    const copy = [...values];
    for (let index = copy.length - 1; index > 0; index -= 1) {
      const target = Math.floor(Math.random() * (index + 1));
      [copy[index], copy[target]] = [copy[target], copy[index]];
    }
    return copy;
  };
  const fillTextSlots = phrases => {
    const choices = [];
    while (choices.length < 8 && phrases.length) choices.push(...shuffle(phrases));
    state.slots.forEach((slot, index) => { slot.text = choices[index] || ''; });
  };
  const makeMockCandidates = () => {
    const topic = state.aiTopic.trim() || '今天';
    const tonePrefix = state.aiTone === '輕鬆搞笑' ? ['我宣布','等等，','這樣也行？','先別急，'][Math.floor(Math.random() * 4)] : '';
    const toneEnding = state.aiTone === '自然日常' ? ['～','！','。',''][Math.floor(Math.random() * 4)] : '';
    state.aiCandidates = AI_ENDINGS.map(ending => `${tonePrefix}${topic}${ending}${toneEnding}`);
    state.aiSelected = [];
    state.message = '已在前端建立 16 句模擬候選句；請選擇剛好 8 句再套用。';
  };
  const aiPanel = () => `
    <section class="s4-source-panel">
      <div class="s4-panel-title">✨ 主題 AI 幫想（純前端模擬）</div>
      <div class="s4-panel-grid">
        <input class="s4-input" data-root-field="aiTopic" value="${esc(state.aiTopic)}" placeholder="輸入主題，例如：上班、下雨天、生日">
        <select class="s4-input" data-root-field="aiTone">
          ${['溫暖鼓勵','自然日常','輕鬆搞笑'].map(tone => `<option value="${tone}" ${state.aiTone === tone ? 'selected' : ''}>${tone}</option>`).join('')}
        </select>
      </div>
      <div class="s4-action-row"><button class="s4-small-button" data-action="mock-ai">✨ 產生 16 句測試候選</button><span class="s4-local">不會呼叫 AI，也不會扣額度。</span></div>
      ${state.aiCandidates.length ? `
        <div class="s4-caption">請選擇剛好 8 句（目前 ${state.aiSelected.length} / 8）。</div>
        <div class="s4-candidates">${state.aiCandidates.map((sentence, index) => `<label class="s4-candidate"><input type="checkbox" data-candidate-index="${index}" ${checked(state.aiSelected.includes(index))}> <span>${esc(sentence)}</span></label>`).join('')}</div>
        <div class="s4-action-row"><button class="s4-small-button" data-action="apply-ai">套用選取的 8 句到 01～08</button></div>` : ''}
      ${state.message ? `<div class="s4-message">${esc(state.message)}</div>` : ''}
    </section>`;
  const randomPanel = () => `
    <section class="s4-source-panel">
      <div class="s4-panel-title">🎲 隨機用語（純前端模擬）</div>
      <div class="s4-panel-grid">
        <select class="s4-input" data-root-field="randomPool">
          ${['日常','搞笑','療癒','自訂'].map(pool => `<option value="${pool}" ${state.randomPool === pool ? 'selected' : ''}>${pool}用語</option>`).join('')}
        <button class="s4-small-button" data-action="random-fill">🎲 隨機填入 8 格</button>
      </div>
      ${state.randomPool === '自訂' ? `
        <div class="s4-label-row"><span>新增自訂用語（一次一則）</span></div>
        <div class="s4-panel-grid"><input class="s4-input" data-root-field="randomCustomDraft" value="${esc(state.randomCustomDraft)}" placeholder="輸入後加入自訂清單"><button class="s4-small-button" data-action="add-custom-phrase">加入清單</button></div>
        <div class="s4-pool-list">${state.randomCustomPhrases.length ? `目前 ${state.randomCustomPhrases.length} 則：${state.randomCustomPhrases.map(esc).join('、')}` : '尚未新增自訂用語。'}</div>` : `<div class="s4-pool-list">會從「${esc(state.randomPool)}」的測試用語中，隨機挑選 8 句填入文字欄位。</div>`}
      ${state.message ? `<div class="s4-message">${esc(state.message)}</div>` : ''}
    </section>`;
  const sourcePanel = () => `
    <div class="s4-source-row">
      <button class="s4-source ${state.sourcePanel === 'ai' ? 'active' : ''}" data-source="ai">✨ 主題 AI 幫想</button>
      <button class="s4-source ${state.sourcePanel === 'random' ? 'active' : ''}" data-source="random">🎲 隨機用語</button>
    </div>
    ${state.sourcePanel === 'ai' ? aiPanel() : state.sourcePanel === 'random' ? randomPanel() : ''}`;

  const updateCounter = () => {
    const filled = state.mode === 'text'
      ? state.slots.filter(s => s.text.trim()).length
      : state.slots.filter(s => s.random || s.emotion.trim() || s.expressionActionEnabled).length;
    const counter = root.querySelector('.s4-count');
    if (counter) counter.textContent = `已設定 ${filled} / 8 格；目前只保存在此瀏覽器。`;
  };

  const render = () => {
    const textMode = state.mode === 'text';
    root.innerHTML = `
      <div class="s4-shell">
        <div class="s4-mode-row">
          <button class="s4-mode ${textMode ? 'active' : ''}" data-mode="text">${textMode ? '✓ ' : ''}💬 使用文字貼圖</button>
          <button class="s4-mode ${!textMode ? 'active' : ''}" data-mode="expression">${!textMode ? '✓ ' : ''}😶 使用表情貼圖（無文字）</button>
        </div>
        <div class="s4-heading">${textMode ? '💬 使用文字貼圖' : '😶 使用表情貼圖（無文字）'}</div>
        <p class="s4-caption">${textMode ? '切換模式與輸入文字不會讀取頁面。動作指令與局部上色會跟著各自的文字草稿保留。' : '切換模式與設定表情不會讀取頁面。回到文字模式後，先前的 01～08 文字仍會保留。'}</p>
        ${textMode ? sourcePanel() : ''}
        <div class="s4-grid">${state.slots.map(textMode ? textCard : expressionCard).join('')}</div>
        <div class="s4-count"></div>
        <div class="s4-action-row">
          <button class="s4-sync" data-action="sync">✅ 同步測試設定</button>
          <button class="s4-reset" data-action="reset">↩️ 清除本機測試草稿</button>
          <span class="s4-local">切換與輸入不會傳送資料；只有同步按鈕會觸發一次 Streamlit 更新。</span>
        </div>
      </div>`;
    updateCounter();
  };

  root.addEventListener('input', event => {
    const element = event.target;
    const rootField = element.dataset.rootField;
    if (rootField) {
      state[rootField] = element.value;
      save();
      return;
    }
    const index = Number(element.dataset.index);
    const field = element.dataset.field;
    if (!Number.isInteger(index) || !field || !state.slots[index]) return;
    state.slots[index][field] = element.type === 'checkbox' ? element.checked : element.value;
    save();
    updateCounter();
  });
  root.addEventListener('change', event => {
    const element = event.target;
    const rootField = element.dataset.rootField;
    if (rootField) {
      state[rootField] = element.value;
      state.message = '';
      save();
      render();
      return;
    }
    const candidateIndex = Number(element.dataset.candidateIndex);
    if (Number.isInteger(candidateIndex) && candidateIndex >= 0 && candidateIndex < state.aiCandidates.length) {
      const selected = new Set(state.aiSelected);
      if (element.checked && selected.size >= 8) {
        state.message = '一次只能選擇 8 句；請先取消其中一句。';
      } else if (element.checked) {
        selected.add(candidateIndex);
        state.message = '';
      } else {
        selected.delete(candidateIndex);
        state.message = '';
      }
      state.aiSelected = [...selected];
      save();
      render();
      return;
    }
    const index = Number(element.dataset.index);
    const field = element.dataset.field;
    if (!Number.isInteger(index) || !field || !state.slots[index] || element.type !== 'checkbox') return;
    state.slots[index][field] = element.checked;
    save();
    render();
  });
  root.addEventListener('click', event => {
    const button = event.target.closest('button');
    if (!button) return;
    if (button.dataset.source) {
      state.sourcePanel = state.sourcePanel === button.dataset.source ? '' : button.dataset.source;
      state.message = '';
      save();
      render();
      return;
    }
    if (button.dataset.mode) {
      state.mode = button.dataset.mode;
      save();
      render();
      return;
    }
    if (button.dataset.action === 'mock-ai') {
      makeMockCandidates();
      save();
      render();
      return;
    }
    if (button.dataset.action === 'apply-ai') {
      if (state.aiSelected.length !== 8) {
        state.message = `請選擇剛好 8 句後再套用（目前 ${state.aiSelected.length} 句）。`;
      } else {
        fillTextSlots(state.aiSelected.map(index => state.aiCandidates[index]));
        state.message = '已將選取的 8 句套用到圖片 01～08。';
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'add-custom-phrase') {
      const phrase = state.randomCustomDraft.trim();
      if (!phrase) {
        state.message = '請先輸入一則自訂用語。';
      } else if (state.randomCustomPhrases.includes(phrase)) {
        state.message = '這則用語已在自訂清單中。';
      } else {
        state.randomCustomPhrases.push(phrase);
        state.randomCustomDraft = '';
        state.message = '已加入自訂用語清單。';
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'random-fill') {
      const phrases = state.randomPool === '自訂' ? state.randomCustomPhrases : RANDOM_POOLS[state.randomPool];
      if (!phrases.length) {
        state.message = '自訂清單尚未有用語，請先新增至少一則。';
      } else {
        fillTextSlots(phrases);
        state.message = `已從「${state.randomPool}」測試用語隨機填入圖片 01～08。`;
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'reset') {
      state = defaultState();
      save();
      render();
    }
    if (button.dataset.action === 'sync') {
      state.syncedAt = new Date().toISOString();
      save();
      setTriggerValue('sync', state);
    }
  });
  render();
}
"""

step04_editor = components.component(
    "step04_frontend_editor_test",
    html=EDITOR_HTML,
    css=EDITOR_CSS,
    js=EDITOR_JS,
    isolate_styles=False,
)

result = step04_editor(
    data={"storageKey": "sticker-studio-next.step04-frontend-test.v2"},
    key="step04_frontend_editor_test",
)

sync_payload = getattr(result, "sync", None)
if isinstance(sync_payload, dict):
    st.success("✅ 已收到測試草稿。這只是驗證資料，沒有送去生成或保存。")
    st.caption(
        "最後同步時間："
        + datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    )
    st.json(sync_payload, expanded=False)
else:
    st.info("請在上方任意切換、輸入與勾選；完成後按「同步測試設定」檢查資料。")
