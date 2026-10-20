"""Standalone, no-backend test bed for the STEP 04 frontend editor.

This file intentionally does not import the production app, Supabase helpers,
or generation code.  It is safe to deploy as a separate Streamlit app for
testing browser-side mode switching and draft persistence only.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st
import streamlit.components.v2 as components


EDITOR_HTML = '<div class="step04-editor-root"></div>'

EDITOR_CSS = r"""
:host { display:block; width:100%; }
.step04-editor-root { color:#eef5ff; font-family:inherit; }
.s4-shell { margin-top:12px; padding:0; border:0; background:transparent; }
.s4-mode-row { display:grid; grid-template-columns:1fr 1fr; gap:46px; margin:0 16px 32px; }
.s4-mode { min-height:40px; border:1px solid #526a86; border-radius:8px; cursor:pointer;
  background:rgba(29,43,64,.82); color:#d8e6f6; font-size:15px; font-weight:760; }
.s4-mode.active { background:#ff5878; border-color:#ff7893; color:white; box-shadow:none; }
.s4-heading { max-width:900px; margin:0 auto; padding:10px 16px; border:2px solid #279fe7; border-radius:11px; color:#f0f8ff;
  background:rgba(21,52,79,.46); font-size:20px; font-weight:850; text-align:center; }
.s4-caption { color:#a9b9cd; margin:10px 0 22px; line-height:1.55; font-size:14px; }
.s4-source-row { display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:14px 0 10px; }
.s4-source { border:1px solid #54708c; border-radius:10px; min-height:45px; background:#24354b; color:#dceafa; font:inherit; font-weight:800; cursor:pointer; }
.s4-source.active { border-color:#65c7ff; background:rgba(37,124,186,.48); color:#fff; }
.s4-ai-status { margin:0 0 14px; padding:10px 12px; border:1px solid rgba(104,177,231,.4); border-radius:9px; background:rgba(26,78,120,.25); color:#d8eeff; font-size:14px; font-weight:750; }
.s4-ai-login { margin-top:5px; color:#ffd4a3; font-size:13px; }
.s4-source-panel { margin:0 0 22px; padding:14px; border:1px solid rgba(96,169,220,.35); border-radius:10px; background:rgba(21,44,70,.32); }
.s4-panel-title { color:#e9f7ff; font-size:16px; font-weight:850; margin-bottom:9px; }
.s4-panel-grid { display:grid; grid-template-columns:1.3fr 1fr; gap:10px; }
.s4-small-button { border:1px solid #5aaee2; border-radius:8px; background:#286eaa; color:#fff; padding:8px 11px; font:inherit; font-weight:800; cursor:pointer; }
.s4-small-button:disabled { cursor:wait; opacity:.78; }
.s4-ai-progress { margin:10px 0 2px; padding:9px 10px; border:1px solid rgba(108,213,255,.54); border-radius:9px; background:rgba(28,91,132,.22); }
.s4-ai-progress-label { display:flex; justify-content:space-between; gap:10px; margin-bottom:7px; color:#d9f4ff; font-size:13px; font-weight:800; }
.s4-ai-progress-track { height:9px; overflow:hidden; border-radius:999px; background:rgba(8,24,43,.72); box-shadow:inset 0 0 0 1px rgba(132,211,255,.22); }
.s4-ai-progress-fill { height:100%; border-radius:inherit; background:linear-gradient(90deg,#5bd6ff,#9a8cff,#ff6d9d); box-shadow:0 0 12px rgba(106,207,255,.72); transition:width .2s ease; }
.s4-candidates { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:7px; margin-top:10px; }
.s4-candidate { display:flex; gap:7px; align-items:flex-start; padding:8px; border:1px solid rgba(111,148,181,.36); border-radius:8px; color:#dceafa; font-size:13px; cursor:pointer; }
.s4-candidate input { margin-top:2px; accent-color:#ff5878; }
.s4-random-phrases { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:7px; margin-top:12px; }
.s4-phrase { min-height:37px; padding:7px 8px; border:1px solid rgba(111,148,181,.38); border-radius:8px;
  background:rgba(30,53,79,.54); color:#e5f2ff; font:inherit; font-size:13px; text-align:left; cursor:pointer; }
.s4-phrase:hover { border-color:#6dbcf0; background:rgba(54,111,157,.43); }
.s4-picked-list { display:grid; grid-template-columns:repeat(4, minmax(0,1fr)); gap:7px; margin-top:10px; }
.s4-picked-item { display:flex; min-width:0; align-items:center; gap:6px; padding:5px 5px 5px 9px; border:1px solid rgba(111,148,181,.38); border-radius:8px; background:rgba(30,53,79,.54); color:#e5f2ff; font-size:13px; }
.s4-picked-text { min-width:0; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.s4-picked-remove { flex:0 0 auto; width:29px; height:29px; border:1px solid #ff7893; border-radius:7px; background:#d84262; color:#fff; font:inherit; font-size:18px; font-weight:900; line-height:1; cursor:pointer; }
.s4-picked-remove:hover { background:#ff5878; transform:scale(1.04); }
.s4-message { margin-top:9px; color:#ffd4dc; font-size:13px; font-weight:700; }
.s4-pool-list { margin:8px 0 0; color:#b8cbe0; font-size:13px; line-height:1.55; }
.s4-color-row { display:grid; grid-template-columns:minmax(0,1fr) 105px 28px; gap:6px; margin:7px 0; align-items:center; }
.s4-remove-color { border:0; border-radius:7px; min-height:32px; background:rgba(255,91,120,.28); color:#fff; font-size:20px; cursor:pointer; }
.s4-grid { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); column-gap:16px; row-gap:12px; }
.s4-card { min-width:0; padding:0 4px; border:0; border-radius:0; background:transparent; }
.s4-card-title { margin:0 0 10px; font-size:16px; font-weight:850; color:#f2f7ff; }
.s4-label-row { display:flex; justify-content:space-between; align-items:center; gap:8px; margin:8px 0 5px; color:#d8e7f7; font-size:13px; font-weight:700; }
.s4-input, .s4-area { width:100%; box-sizing:border-box; border:1px solid #496582; border-radius:8px;
  background:#243248; color:#f4f8ff; padding:9px 10px; font:inherit; }
.s4-area { min-height:64px; resize:vertical; }
.s4-toggle { display:flex; gap:7px; align-items:center; color:#d9e8f7; font-size:13px; font-weight:700; cursor:pointer; }
.s4-toggle input { width:17px; height:17px; accent-color:#ff5878; }
.s4-inline-tools { display:flex; align-items:center; justify-content:space-between; gap:8px; }
.s4-clear { flex:0 0 auto; border:1px solid rgba(103,184,242,.72); border-radius:7px;
  background:rgba(62,139,196,.20); color:#d9f1ff; padding:5px 9px; font:inherit; font-size:13px; font-weight:750; cursor:pointer; }
.s4-clear:hover { background:rgba(65,151,211,.38); }
.s4-subfield { margin-top:10px; padding-top:9px; border-top:1px solid rgba(126,157,186,.26); }
.s4-row-divider { height:1px; margin:24px 0; background:rgba(151,177,207,.3); }
.s4-action-row { display:flex; gap:10px; margin-top:20px; align-items:center; flex-wrap:wrap; }
.s4-sync, .s4-reset { border-radius:10px; padding:11px 17px; font:inherit; font-weight:850; cursor:pointer; }
.s4-sync { border:1px solid #45b4ff; background:#237cba; color:#fff; }
.s4-reset { border:1px solid #667c96; background:#27374d; color:#dce8f4; }
.s4-local { color:#9fb2c9; font-size:13px; }
.s4-count { margin-top:15px; border-radius:10px; padding:10px 12px; background:rgba(57,136,205,.15); color:#9fd8ff; font-weight:750; }
@media (max-width: 820px) { .s4-mode-row { gap:12px; margin:0 0 20px; } .s4-grid, .s4-candidates, .s4-random-phrases, .s4-picked-list { grid-template-columns:repeat(2, minmax(0,1fr)); } .s4-panel-grid { grid-template-columns:1fr; } }
@media (max-width: 520px) { .s4-grid { grid-template-columns:1fr; } .s4-mode { font-size:14px; } }
"""

EDITOR_JS = r"""
export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  const root = parentElement.querySelector('.step04-editor-root') || parentElement;
  const storageKey = data.storageKey;
  const formal = Boolean(data.formal);
  const revision = String(data.revision || '');
  const initial = data.initial && typeof data.initial === 'object' ? data.initial : {};
  const aiQuotaText = String(initial.aiQuotaText || 'AI幫想 今日額度（台灣時間）：登入後可查看');
  const aiLoginNotice = String(initial.aiLoginNotice || '');
  const COLOR_OPTIONS = [
    ['紅色', '#ff5c75'], ['橘色', '#ff9f43'], ['黃色', '#ffd166'], ['綠色', '#55d68b'],
    ['藍色', '#57b7ff'], ['紫色', '#ad7dff'], ['粉色', '#ff8eb7'], ['白色', '#ffffff']
  ];
  const defaultColorSegments = () => [{text:'', color_name:'紅色', hex:'#ff5c75'}];
  const emptySlots = () => Array.from({length: 8}, () => ({
    text:'', actionEnabled:false, action:'', colorEnabled:false, colorSegments:defaultColorSegments(),
    emotion:'', random:false, expressionActionEnabled:false, expressionAction:''
  }));
  const AI_ENDINGS = [
    '，今天也要好好加油！', '，先深呼吸一下～', '，我來陪你一起撐住。', '，今天的你已經很棒了！',
    '，笑一個就會更好。', '，慢慢來也沒有關係。', '，給自己一個大大的讚！', '，事情會一步一步變好的。',
    '，先休息一下再出發。', '，有我在，別擔心！', '，今天也要保持可愛。', '，一起把煩惱甩開吧！',
    '，這次一定沒問題。', '，辛苦了，抱一下！', '，現在就出發吧！', '，你值得被好好鼓勵。'
  ];
  const FALLBACK_RANDOM_POOLS = {
    '日常': ['早安！','午安～','晚安！','收到！','沒問題！','等等我～','辛苦了！','謝謝你！','我懂！','太好了！','先這樣～','交給我吧！','等等見！','加油！','好耶！','原來如此！'],
    '搞笑': ['我先笑一下。','太扯了吧！','你認真的嗎？','等等，讓我消化。','我投降！','這合理嗎？','笑不活了～','先不要鬧！','好像哪裡怪怪的。','我看不懂，但我大受震撼。','真的假的？','這次換我傻眼。','有夠荒謬！','我先躲一下。','再說一次？','好，算你贏。'],
    '療癒': ['慢慢來就好。','今天也辛苦了。','抱抱你。','你已經做得很好。','休息一下吧。','一切都會好的。','別忘了照顧自己。','我在這裡。','給你一點溫柔。','沒事的～','好好吃飯喔。','先喝口水。','明天再繼續。','請對自己好一點。','陪你一起。','你不孤單。']
  };
  const RANDOM_POOLS = initial.randomPools && typeof initial.randomPools === 'object'
    ? initial.randomPools : FALLBACK_RANDOM_POOLS;
  const ALL_RANDOM_POOL = '__all_builtin__';
  const CUSTOM_RANDOM_POOL = '__custom_pool__';
  const allRandomPhrases = () => Object.values(RANDOM_POOLS).flat().map(value => String(value));
  const AI_TONES = ['😂 搞笑自然','😤 憤憤不平','😈 嘲諷吐槽','☁️ 委屈可愛','👑 霸氣有梗','💬 台灣口語','✨ 溫暖療癒'];
  const defaultState = () => ({
    mode:'text', sourcePanel:'', aiTopic:'', aiTone:'😂 搞笑自然', aiCandidates:[], aiSelected:[], aiPicked:[], aiTarget:'0', aiLoading:false, aiProgress:0, aiLoadingText:'',
    randomPool:ALL_RANDOM_POOL, randomTarget:'0', randomCustomDraft:'', randomCustomPhrases:[], message:'', slots:emptySlots(), version:2
  });
  const normalize = (raw) => {
    const base = defaultState();
    if (!raw || typeof raw !== 'object') return base;
    Object.assign(base, raw);
    base.mode = raw.mode === 'expression' ? 'expression' : 'text';
    base.sourcePanel = raw.sourcePanel === 'ai' || raw.sourcePanel === 'random' ? raw.sourcePanel : '';
    base.aiTopic = String(raw.aiTopic || '');
    base.aiTone = AI_TONES.includes(raw.aiTone) ? raw.aiTone : AI_TONES[0];
    base.aiCandidates = Array.isArray(raw.aiCandidates) ? raw.aiCandidates.map(value => String(value)).slice(0, 16) : [];
    base.aiSelected = Array.isArray(raw.aiSelected) ? [...new Set(raw.aiSelected.map(Number).filter(index => Number.isInteger(index) && index >= 0 && index < base.aiCandidates.length))] : [];
    base.aiPicked = Array.isArray(raw.aiPicked) ? [...new Set(raw.aiPicked.map(value => String(value).trim()).filter(Boolean))].slice(0, 8) : [];
    base.aiTarget = String(Number.isInteger(Number(raw.aiTarget)) && Number(raw.aiTarget) >= 0 && Number(raw.aiTarget) < 8 ? Number(raw.aiTarget) : 0);
    // The animation is only for the current click; never restore a stale
    // loading indicator after a browser refresh or a completed server action.
    base.aiLoading = false;
    base.aiProgress = 0;
    base.aiLoadingText = '';
    base.randomPool = Object.prototype.hasOwnProperty.call(RANDOM_POOLS, raw.randomPool) || raw.randomPool === ALL_RANDOM_POOL || raw.randomPool === CUSTOM_RANDOM_POOL || raw.randomPool === '自訂' ? (raw.randomPool === '自訂' ? CUSTOM_RANDOM_POOL : raw.randomPool) : ALL_RANDOM_POOL;
    base.randomTarget = String(Number.isInteger(Number(raw.randomTarget)) && Number(raw.randomTarget) >= 0 && Number(raw.randomTarget) < 8 ? Number(raw.randomTarget) : 0);
    base.randomCustomDraft = String(raw.randomCustomDraft || '');
    base.randomCustomPhrases = Array.isArray(raw.randomCustomPhrases) ? raw.randomCustomPhrases.map(value => String(value).trim()).filter(Boolean).slice(0, 50) : [];
    base.message = String(raw.message || '');
    if (Array.isArray(raw.slots)) base.slots = base.slots.map((slot, i) => {
      const saved = raw.slots[i] || {};
      const colorSegments = Array.isArray(saved.colorSegments) ? saved.colorSegments.slice(0, 3).map(segment => {
        const matching = COLOR_OPTIONS.find(option => option[1] === String(segment?.hex || '')) || COLOR_OPTIONS[0];
        return {text:String(segment?.text || ''), color_name:String(segment?.color_name || matching[0]), hex:matching[1]};
      }) : defaultColorSegments();
      return {...slot, ...saved, colorSegments: colorSegments.length ? colorSegments : defaultColorSegments()};
    });
    return base;
  };
  let state;
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) || 'null');
    // A loaded project (or a completed server action such as AI copywriting)
    // deliberately has a new revision.  In that case the server snapshot wins
    // over a previous browser draft, so one work never leaks into another.
    state = saved && String(saved.__revision || '') === revision
      ? normalize(saved)
      : normalize(data.initial || null);
  }
  catch (_) { state = normalize(data.initial || null); }
  const save = () => {
    state.__revision = revision;
    localStorage.setItem(storageKey, JSON.stringify(state));
  };
  save();
  const esc = (value) => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
  const checked = value => value ? 'checked' : '';

  const colorRows = (slot, index) => slot.colorSegments.map((segment, segmentIndex) => `
    <div class="s4-color-row">
      <input class="s4-input" data-segment-index="${segmentIndex}" data-segment-field="text" data-index="${index}" value="${esc(segment.text)}" placeholder="要上色的文字片段">
      <select class="s4-input" data-segment-index="${segmentIndex}" data-segment-field="hex" data-index="${index}">
        ${COLOR_OPTIONS.map(option => `<option value="${option[1]}" ${segment.hex === option[1] ? 'selected' : ''}>${option[0]}</option>`).join('')}
      </select>
      ${slot.colorSegments.length > 1 ? `<button class="s4-remove-color" data-action="remove-color" data-index="${index}" data-segment-index="${segmentIndex}" aria-label="移除這段上色">×</button>` : ''}
    </div>`).join('');
  const textCard = (slot, index) => `
    <section class="s4-card">
      <div class="s4-card-title">圖片 ${String(index + 1).padStart(2, '0')}</div>
      <div class="s4-label-row"><span>貼圖文字</span></div>
      <input class="s4-input" data-field="text" data-index="${index}" value="${esc(slot.text)}" placeholder="輸入貼圖文字">
      <div class="s4-subfield">
        <div class="s4-inline-tools"><label class="s4-toggle"><input type="checkbox" data-field="actionEnabled" data-index="${index}" ${checked(slot.actionEnabled)}> 🎬 啟用動作指令</label><button class="s4-clear" data-action="clear-slot" data-index="${index}">清空</button></div>
        ${slot.actionEnabled ? `<div class="s4-label-row"><span>輸入希望動作指令</span></div><textarea class="s4-area" data-field="action" data-index="${index}" placeholder="例如：揮手、比讚、跳起來">${esc(slot.action)}</textarea>` : ''}
      </div>
      <div class="s4-subfield">
        <label class="s4-toggle"><input type="checkbox" data-field="colorEnabled" data-index="${index}" ${checked(slot.colorEnabled)}> 🎨 啟用局部文字上色</label>
        ${slot.colorEnabled ? `<div class="s4-label-row"><span>局部文字上色（最多 3 段）</span></div>${colorRows(slot, index)}${slot.colorSegments.length < 3 ? `<button class="s4-small-button" data-action="add-color" data-index="${index}">＋ 新增上色片段</button>` : ''}` : ''}
      </div>
    </section>`;

  const expressionCard = (slot, index) => `
    <section class="s4-card">
      <div class="s4-card-title">圖片 ${String(index + 1).padStart(2, '0')}</div>
      <div class="s4-label-row"><span>表情欄位</span><label class="s4-toggle"><input type="checkbox" data-field="random" data-index="${index}" ${checked(slot.random)}> 隨機</label></div>
      <input class="s4-input" data-field="emotion" data-index="${index}" value="${esc(slot.emotion)}" placeholder="例如：驚喜、害羞" ${slot.random ? 'disabled' : ''}>
      <div class="s4-subfield">
        <div class="s4-inline-tools"><label class="s4-toggle"><input type="checkbox" data-field="expressionActionEnabled" data-index="${index}" ${checked(slot.expressionActionEnabled)}> 🎬 啟用動作指令</label><button class="s4-clear" data-action="clear-expression-slot" data-index="${index}">清空</button></div>
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
    const tonePrefix = state.aiTone === '😂 搞笑自然' ? ['我宣布','等等，','這樣也行？','先別急，'][Math.floor(Math.random() * 4)] : '';
    const toneEnding = state.aiTone === '💬 台灣口語' ? ['～','！','。',''][Math.floor(Math.random() * 4)] : '';
    state.aiCandidates = AI_ENDINGS.map(ending => `${tonePrefix}${topic}${ending}${toneEnding}`);
    state.aiSelected = [];
    state.message = '已在前端建立 16 句模擬候選句；請選擇剛好 8 句再套用。';
  };
  const aiPanel = () => `
    <section class="s4-source-panel">
      <div class="s4-panel-title">✨ 主題 AI 幫想</div>
      <div class="s4-panel-grid">
        <input class="s4-input" data-root-field="aiTopic" value="${esc(state.aiTopic)}" placeholder="輸入主題，例如：上班、下雨天、生日">
        <select class="s4-input" data-root-field="aiTone">
          ${AI_TONES.map(tone => `<option value="${tone}" ${state.aiTone === tone ? 'selected' : ''}>${tone}</option>`).join('')}
        </select>
      </div>
      <div class="s4-action-row"><button class="s4-small-button" data-action="generate-ai" ${state.aiLoading ? 'disabled' : ''}>${state.aiLoading ? '✨ AI 正在構思中…' : '✨ 產生 16 句貼圖文字'}</button></div>
      ${state.aiLoading ? `<div class="s4-ai-progress"><div class="s4-ai-progress-label"><span>${esc(state.aiLoadingText || '🧠 正在收集靈感…')}</span><span>${Math.max(0, Math.min(99, Number(state.aiProgress) || 0))}%</span></div><div class="s4-ai-progress-track"><div class="s4-ai-progress-fill" style="width:${Math.max(0, Math.min(99, Number(state.aiProgress) || 0))}%"></div></div></div>` : ''}
      ${state.aiCandidates.length ? `
        <div class="s4-caption">可勾選任意數量；本批已勾選 ${state.aiSelected.length} 句，待帶入清單 ${state.aiPicked.length} / 8 句。</div>
        <div class="s4-candidates">${state.aiCandidates.map((sentence, index) => `<label class="s4-candidate"><input type="checkbox" data-candidate-index="${index}" ${checked(state.aiSelected.includes(index))}> <span>${esc(sentence)}</span></label>`).join('')}</div>
        <div class="s4-action-row"><button class="s4-small-button" data-action="stash-ai">加入勾選句到待帶入清單</button><span class="s4-local">不需要湊滿 8 句；可再產生下一批繼續挑選。</span></div>` : ''}
      ${state.aiPicked.length ? `
        <div class="s4-caption">待帶入清單（點選句子可移除）：</div>
        <div class="s4-picked-list">${state.aiPicked.map((sentence, index) => `<div class="s4-picked-item"><span class="s4-picked-text">${esc(sentence)}</span><button class="s4-picked-remove" data-action="remove-ai-picked" data-picked-index="${index}" aria-label="移除：${esc(sentence)}">×</button></div>`).join('')}</div>
        <div class="s4-panel-grid"><select class="s4-input" data-root-field="aiTarget">${Array.from({length:8}, (_, index) => `<option value="${index}" ${Number(state.aiTarget) === index ? 'selected' : ''}>從圖片 ${String(index+1).padStart(2,'0')} 開始帶入</option>`).join('')}</select><button class="s4-small-button" data-action="apply-ai-picked">帶入已挑選的 ${state.aiPicked.length} 句</button></div>
        <div class="s4-pool-list">會依序填入指定圖片起的欄位，不會改動其他圖片文字。</div>` : ''}
      ${state.message ? `<div class="s4-message">${esc(state.message)}</div>` : ''}
    </section>`;
  const randomPanel = () => {
    const isAllPool = state.randomPool === ALL_RANDOM_POOL;
    const isCustomPool = state.randomPool === CUSTOM_RANDOM_POOL;
    const phrases = isAllPool ? allRandomPhrases() : (isCustomPool ? state.randomCustomPhrases : (RANDOM_POOLS[state.randomPool] || []));
    return `
    <section class="s4-source-panel">
      <div class="s4-panel-title">🎲 隨機用語</div>
      <div class="s4-panel-grid">
        <select class="s4-input" data-root-field="randomPool">
          <option value="${ALL_RANDOM_POOL}" ${state.randomPool === ALL_RANDOM_POOL ? 'selected' : ''}>🎲 所有用語隨機</option>
          <option value="${CUSTOM_RANDOM_POOL}" ${state.randomPool === CUSTOM_RANDOM_POOL ? 'selected' : ''}>⭐ 我的自定義詞語池</option>
          ${Object.keys(RANDOM_POOLS).map(pool => `<option value="${pool}" ${state.randomPool === pool ? 'selected' : ''}>${pool}</option>`).join('')}
        </select>
        <select class="s4-input" data-root-field="randomTarget">
          ${Array.from({length:8}, (_, index) => `<option value="${index}" ${Number(state.randomTarget) === index ? 'selected' : ''}>指定圖片 ${String(index+1).padStart(2,'0')}</option>`).join('')}
        </select>
      </div>
      <div class="s4-action-row"><button class="s4-small-button" data-action="random-fill">🎲 隨機填入 8 格</button><span class="s4-local">或點選下方任一句，直接放入指定圖片。</span></div>
      ${isCustomPool ? `
        <div class="s4-label-row"><span>新增自訂用語（一次一則）</span></div>
        <div class="s4-panel-grid"><input class="s4-input" data-root-field="randomCustomDraft" value="${esc(state.randomCustomDraft)}" placeholder="輸入後加入自訂清單"><button class="s4-small-button" data-action="add-custom-phrase">加入清單</button></div>
        <div class="s4-pool-list">${state.randomCustomPhrases.length ? `目前 ${state.randomCustomPhrases.length} 則` : '尚未新增自訂用語。'}</div>` : `<div class="s4-pool-list">${isAllPool ? `所有內建用語共 ${phrases.length} 則；此模式只供隨機填入，不展開全部內容。` : `「${esc(state.randomPool)}」目前共有 ${phrases.length} 則用語。`}</div>`}
      ${phrases.length && !isAllPool ? `<div class="s4-random-phrases">${phrases.map((phrase, index) => `<button class="s4-phrase" data-action="insert-random-phrase" data-phrase-index="${index}">${esc(phrase)}</button>`).join('')}</div>` : ''}
      ${state.message ? `<div class="s4-message">${esc(state.message)}</div>` : ''}
    </section>`;
  };
  const sourcePanel = () => `
    <div class="s4-source-row">
      <button class="s4-source ${state.sourcePanel === 'ai' ? 'active' : ''}" data-source="ai">✨ 主題 AI 幫想</button>
      <button class="s4-source ${state.sourcePanel === 'random' ? 'active' : ''}" data-source="random">🎲 隨機用語</button>
    </div>
    <div class="s4-ai-status">${esc(aiQuotaText)}${aiLoginNotice ? `<div class="s4-ai-login">${esc(aiLoginNotice)}</div>` : ''}</div>
    ${state.sourcePanel === 'ai' ? aiPanel() : state.sourcePanel === 'random' ? randomPanel() : ''}`;

  const updateCounter = () => {
    const filled = state.mode === 'text'
      ? state.slots.filter(s => s.text.trim()).length
      : state.slots.filter(s => s.random || s.emotion.trim() || s.expressionActionEnabled).length;
    const counter = root.querySelector('.s4-count');
    if (counter) counter.textContent = formal
      ? `已設定 ${filled} / 8 格；按下 AI 產生或生成總圖時會自動帶入。`
      : `已設定 ${filled} / 8 格；目前只保存在此瀏覽器。`;
  };

  const render = () => {
    const textMode = state.mode === 'text';
    const cardRenderer = textMode ? textCard : expressionCard;
    const firstRow = state.slots.slice(0, 4).map(cardRenderer).join('');
    const secondRow = state.slots.slice(4).map((slot, index) => cardRenderer(slot, index + 4)).join('');
    root.innerHTML = `
      <div class="s4-shell">
        <div class="s4-mode-row">
          <button class="s4-mode ${textMode ? 'active' : ''}" data-mode="text">${textMode ? '✓ ' : ''}💬 使用文字貼圖</button>
          <button class="s4-mode ${!textMode ? 'active' : ''}" data-mode="expression">${!textMode ? '✓ ' : ''}😶 使用表情貼圖（無文字）</button>
        </div>
        <div class="s4-heading">${textMode ? '💬 使用文字貼圖' : '😶 使用表情貼圖（無文字）'}</div>
        ${textMode ? sourcePanel() : '<p class="s4-caption">切換模式與設定表情不會讀取頁面。回到文字模式後，先前的 01～08 文字仍會保留。</p>'}
        <div class="s4-grid">${firstRow}</div>
        <div class="s4-row-divider"></div>
        <div class="s4-grid">${secondRow}</div>
        <div class="s4-count"></div>
        ${formal ? '' : `<div class="s4-action-row">
          <button class="s4-sync" data-action="sync">✅ 同步測試設定</button>
          <button class="s4-reset" data-action="reset">↩️ 清除本機測試草稿</button>
          <span class="s4-local">切換與輸入不會傳送資料；只有同步按鈕會觸發一次 Streamlit 更新。</span>
        </div>`}
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
    const segmentIndex = Number(element.dataset.segmentIndex);
    const segmentField = element.dataset.segmentField;
    if (Number.isInteger(index) && Number.isInteger(segmentIndex) && segmentField && state.slots[index]?.colorSegments?.[segmentIndex]) {
      state.slots[index].colorSegments[segmentIndex][segmentField] = element.value;
      save();
      return;
    }
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
    const colorSlotIndex = Number(element.dataset.index);
    const colorSegmentIndex = Number(element.dataset.segmentIndex);
    const colorSegmentField = element.dataset.segmentField;
    if (Number.isInteger(colorSlotIndex) && Number.isInteger(colorSegmentIndex) && colorSegmentField && state.slots[colorSlotIndex]?.colorSegments?.[colorSegmentIndex]) {
      const segment = state.slots[colorSlotIndex].colorSegments[colorSegmentIndex];
      segment[colorSegmentField] = element.value;
      if (colorSegmentField === 'hex') {
        segment.color_name = (COLOR_OPTIONS.find(option => option[1] === element.value) || COLOR_OPTIONS[0])[0];
      }
      save();
      render();
      return;
    }
    const candidateIndex = Number(element.dataset.candidateIndex);
    if (Number.isInteger(candidateIndex) && candidateIndex >= 0 && candidateIndex < state.aiCandidates.length) {
      const selected = new Set(state.aiSelected);
      if (element.checked) {
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
    if (button.dataset.action === 'generate-ai') {
      const loadingStages = [
        [12, '🧠 正在收集靈感…'],
        [29, '🎲 正在翻找好用的貼圖句…'],
        [47, '✨ 正在把語氣調得更有梗…'],
        [66, '📝 正在整理 16 句候選文案…'],
        [79, '🚀 靈感準備送達…'],
      ];
      state.aiLoading = true;
      state.aiProgress = loadingStages[0][0];
      state.aiLoadingText = loadingStages[0][1];
      state.message = '';
      save();
      render();
      let stageIndex = 0;
      if (root._aiLoadingTimer) clearInterval(root._aiLoadingTimer);
      root._aiLoadingTimer = setInterval(() => {
        stageIndex += 1;
        if (stageIndex < loadingStages.length) {
          state.aiProgress = loadingStages[stageIndex][0];
          state.aiLoadingText = loadingStages[stageIndex][1];
          render();
          return;
        }
        clearInterval(root._aiLoadingTimer);
        root._aiLoadingTimer = null;
        setTriggerValue('event', {kind:'generate-ai', state});
      }, 170);
      return;
    }
    if (button.dataset.action === 'stash-ai') {
      const selectedSentences = state.aiSelected
        .map(index => state.aiCandidates[index])
        .filter(Boolean);
      const combined = [...state.aiPicked, ...selectedSentences]
        .map(value => String(value).trim())
        .filter(Boolean);
      state.aiPicked = [...new Set(combined)].slice(0, 8);
      state.aiSelected = [];
      state.message = selectedSentences.length
        ? `已加入 ${selectedSentences.length} 句；目前待帶入 ${state.aiPicked.length} / 8 句。`
        : '請先勾選至少一句喜歡的文案。';
      save();
      render();
      return;
    }
    if (button.dataset.action === 'apply-ai-picked') {
      const start = Number(state.aiTarget);
      const count = Math.max(0, Math.min(state.aiPicked.length, 8 - start));
      if (!count) {
        state.message = '沒有可帶入的句子，或指定的位置已超出範圍。';
      } else {
        state.aiPicked.slice(0, count).forEach((sentence, offset) => {
          state.slots[start + offset].text = sentence;
        });
        const used = state.aiPicked.slice(0, count);
        state.aiPicked = state.aiPicked.slice(count);
        state.message = `已將 ${used.length} 句依序帶入圖片 ${String(start + 1).padStart(2, '0')} 起的欄位。`;
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'remove-ai-picked') {
      const pickedIndex = Number(button.dataset.pickedIndex);
      if (Number.isInteger(pickedIndex) && pickedIndex >= 0 && pickedIndex < state.aiPicked.length) {
        state.aiPicked.splice(pickedIndex, 1);
        state.message = '已從待帶入清單移除。';
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
      const phrases = state.randomPool === ALL_RANDOM_POOL ? allRandomPhrases() : (state.randomPool === CUSTOM_RANDOM_POOL ? state.randomCustomPhrases : (RANDOM_POOLS[state.randomPool] || []));
      if (!phrases.length) {
        state.message = '自訂清單尚未有用語，請先新增至少一則。';
      } else {
        fillTextSlots(phrases);
        state.message = `已從「${state.randomPool === ALL_RANDOM_POOL ? '所有用語' : state.randomPool === CUSTOM_RANDOM_POOL ? '我的自定義詞語池' : state.randomPool}」隨機填入圖片 01～08。`;
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'insert-random-phrase') {
      const phrases = state.randomPool === CUSTOM_RANDOM_POOL ? state.randomCustomPhrases : (RANDOM_POOLS[state.randomPool] || []);
      const phraseIndex = Number(button.dataset.phraseIndex);
      const target = Number(state.randomTarget);
      if (Number.isInteger(phraseIndex) && phrases[phraseIndex] && Number.isInteger(target) && state.slots[target]) {
        state.slots[target].text = String(phrases[phraseIndex]);
        state.message = `已將「${phrases[phraseIndex]}」放入圖片 ${String(target + 1).padStart(2, '0')}。`;
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'add-color') {
      const index = Number(button.dataset.index);
      if (state.slots[index] && state.slots[index].colorSegments.length < 3) {
        state.slots[index].colorSegments.push({text:'', color_name:'紅色', hex:'#ff5c75'});
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'clear-slot') {
      const index = Number(button.dataset.index);
      if (state.slots[index]) {
        state.slots[index].text = '';
        state.slots[index].actionEnabled = false;
        state.slots[index].action = '';
        state.slots[index].colorEnabled = false;
        state.slots[index].colorSegments = defaultColorSegments();
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'clear-expression-slot') {
      const index = Number(button.dataset.index);
      if (state.slots[index]) {
        state.slots[index].emotion = '';
        state.slots[index].random = false;
        state.slots[index].expressionActionEnabled = false;
        state.slots[index].expressionAction = '';
      }
      save();
      render();
      return;
    }
    if (button.dataset.action === 'remove-color') {
      const index = Number(button.dataset.index);
      const segmentIndex = Number(button.dataset.segmentIndex);
      if (state.slots[index]?.colorSegments?.length > 1) {
        state.slots[index].colorSegments.splice(segmentIndex, 1);
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

def render_frontend_editor(*, data, key, component_name="step04_frontend_editor_test"):
    """Render the browser-side editor and return its explicit browser event."""
    editor = components.component(
        component_name,
        html=EDITOR_HTML,
        css=EDITOR_CSS,
        js=EDITOR_JS,
        isolate_styles=False,
    )
    result = editor(data=data, key=key)
    return {
        "sync": getattr(result, "sync", None),
        "event": getattr(result, "event", None),
    }


GENERATOR_HTML = '<div class="step04-generator-root"></div>'
GENERATOR_CSS = r"""
:host { display:block; width:100%; }
.step04-generator-root button { width:100%; min-height:42px; border:0; border-radius:9px;
  background:#ff5878; color:#fff; font:inherit; font-size:16px; font-weight:850; cursor:pointer; }
.step04-generator-root button:disabled { opacity:.62; cursor:wait; }
"""
GENERATOR_JS = r"""
export default function(component) {
  const { parentElement, data, setTriggerValue } = component;
  const root = parentElement.querySelector('.step04-generator-root') || parentElement;
  const readDraft = () => {
    try { return JSON.parse(localStorage.getItem(data.storageKey) || 'null'); }
    catch (_) { return null; }
  };
  root.innerHTML = `<button ${data.disabled ? 'disabled' : ''}>${data.disabled ? '🔄 AI 正在生成，請稍候……' : '✨ 生成 4×2 八格總圖'}</button>`;
  const button = root.querySelector('button');
  button.addEventListener('click', () => {
    const state = readDraft();
    if (!state) return;
    button.disabled = true;
    button.textContent = '🔄 正在準備生成…';
    setTriggerValue('request', {state});
  });
}
"""


def render_frontend_generate_button(*, data, key):
    """Render the real STEP 06 button, carrying the local STEP 04 draft once."""
    generator = components.component(
        "v12_step04_frontend_generate_button",
        html=GENERATOR_HTML,
        css=GENERATOR_CSS,
        js=GENERATOR_JS,
        isolate_styles=False,
    )
    result = generator(data=data, key=key)
    return getattr(result, "request", None)


def run_test_page():
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
    editor_result = render_frontend_editor(
        data={"storageKey": "sticker-studio-next.step04-frontend-test.v3"},
        key="step04_frontend_editor_test",
    )
    sync_payload = editor_result.get("sync") if isinstance(editor_result, dict) else None
    if isinstance(sync_payload, dict):
        st.success("✅ 已收到測試草稿。這只是驗證資料，沒有送去生成或保存。")
        st.caption("最後同步時間：" + datetime.now().strftime("%Y/%m/%d %H:%M:%S"))
        st.json(sync_payload, expanded=False)
    else:
        st.info("請在上方任意切換、輸入與勾選；完成後按「同步測試設定」檢查資料。")


if __name__ == "__main__":
    run_test_page()
