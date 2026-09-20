/* WB Workbench — native, local-only client. Measurements are supplied by the backend. */
(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const app = $('#app'), dialog = $('#dialog');
  const roles = ['pho', 'total'];
  const roleNames = {pho: '磷酸化蛋白', total: '对应总蛋白'};
  const svgPaths = {
    logo: '<path d="M4 5h5M4 12h7M4 19h5M15 5h5M15 12h5M15 19h4"/>',
    plus: '<path d="M12 5v14M5 12h14"/>',
    minus: '<path d="M5 12h14"/>',
    experiment: '<path d="M9 3h6M10 3v6L5 18a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3M8 14h8"/>',
    upload: '<path d="M12 16V3m-4 4 4-4 4 4M4 15v5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-5"/>',
    download: '<path d="M12 3v13m-4-4 4 4 4-4M4 16v5h16v-5"/>',
    image: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="m3 16 5-5 4 4 4-6 5 7"/><circle cx="8" cy="8" r="1"/>',
    crop: '<path d="M7 3v14h14M3 7h14v14"/>',
    suggest: '<path d="m5 19 11-11 3 3-11 11M14 10l3 3M6 3v4M4 5h4M18 2v4M16 4h4M21 16v4M19 18h4"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
    chevron: '<path d="m9 5 7 7-7 7"/>',
    down: '<path d="m6 9 6 6 6-6"/>',
    close: '<path d="m6 6 12 12M6 18 18 6"/>',
    fit: '<path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/>',
    edit: '<path d="m4 16-1 5 5-1L20 8l-4-4L4 16Zm10-10 4 4"/>',
    eye: '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10h.01"/>',
    warning: '<path d="m12 3 10 18H2L12 3Zm0 6v5m0 3h.01"/>',
    shield: '<path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6l-8-3Z"/><path d="m8 12 3 3 5-6"/>',
    table: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M9 4v16M15 10v10M3 15h18"/>',
    inspect: '<circle cx="10" cy="10" r="6"/><path d="m15 15 6 6M7 10h6M10 7v6"/>',
    book: '<path d="M3 4h6a4 4 0 0 1 3 2 4 4 0 0 1 3-2h6v15h-6a4 4 0 0 0-3 2 4 4 0 0 0-3-2H3V4Zm9 2v15"/>',
    arrow: '<path d="M4 12h16m-6-6 6 6-6 6"/>',
    refresh: '<path d="M20 7v5h-5M4 17v-5h5M19 11a7 7 0 0 0-12-6l-3 3m1 5a7 7 0 0 0 12 6l3-3"/>',
    folder: '<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z"/>',
  };
  const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${svgPaths[name] || svgPaths.info}</svg>`;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const clone = obj => JSON.parse(JSON.stringify(obj));
  const formatNumber = value => {
    if (value == null || !Number.isFinite(Number(value))) return '—';
    const n = Number(value);
    if (n !== 0 && (Math.abs(n) < .0001 || Math.abs(n) >= 1e9)) return n.toExponential(3);
    return n.toLocaleString('en-US', {maximumFractionDigits: 4});
  };
  const shortDate = value => {
    const d = new Date(value);
    return Number.isNaN(d.valueOf()) ? '' : d.toLocaleDateString('zh-CN', {month:'2-digit',day:'2-digit'}).replace('/', '.');
  };
  const uid = () => active()?.id;
  const imageCache = new Map();
  const view = {
    pho: {zoom:1,panX:0,panY:0,showOverlay:true,kind:'band'},
    total: {zoom:1,panX:0,panY:0,showOverlay:true,kind:'band'},
  };
  const state = {experiments:[],activeId:null,selectedId:null,results:null,busy:false,error:null,save:'saved',regionRole:null,drag:null,exportScope:'current'};
  const active = () => state.experiments.find(e => e.id === state.activeId);
  const sample = () => active()?.samples.find(s => s.id === state.selectedId);
  const selectedRow = () => state.results?.rows?.find(r => r.sampleId === state.selectedId);
  const roiFor = (role, id = state.selectedId) => active()?.rois?.[role]?.find(r => r.sampleId === id);
  const storeActive = () => { try { localStorage.setItem('wb-workbench.active', state.activeId || ''); } catch (_) {} };
  const restoreActive = () => { try { return localStorage.getItem('wb-workbench.active'); } catch (_) { return null; } };
  let toastTimer;
  function toast(message, error = false) {
    clearTimeout(toastTimer);
    const t = $('#toast');
    t.textContent = message;
    t.className = `toast visible${error ? ' error' : ''}`;
    toastTimer = setTimeout(() => { t.classList.remove('visible'); }, error ? 6500 : 3600);
  }
  async function api(path, options = {}) {
    const response = await fetch(path, {cache:'no-store', ...options, headers:{'Content-Type':'application/json', ...(options.headers || {})}});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error(`服务返回了无法读取的响应（${response.status}）。`); }
    if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
    return data;
  }
  function replaceExperiment(experiment) {
    const index = state.experiments.findIndex(e => e.id === experiment.id);
    if (index === -1) state.experiments.unshift(experiment);
    else state.experiments[index] = experiment;
    if (state.activeId === experiment.id && !experiment.samples.some(s => s.id === state.selectedId)) state.selectedId = experiment.samples[0]?.id || null;
  }
  async function getResults() {
    const id = uid();
    if (!id) { state.results = null; return; }
    const results = await api(`/api/experiments/${encodeURIComponent(id)}/results`);
    if (uid() === id) state.results = results;
  }
  async function refreshState(keepSelection = true) {
    const data = await api('/api/state');
    state.experiments = data.experiments || [];
    if (!state.experiments.some(e => e.id === state.activeId)) state.activeId = state.experiments[0]?.id || null;
    if (!keepSelection || !active()?.samples.some(s => s.id === state.selectedId)) state.selectedId = active()?.samples[0]?.id || null;
    await getResults();
    storeActive();
  }
  function showSaveStatus() {
    const el = $('#save-status');
    if (!el) return;
    el.className = `save-status ${state.save === 'saving' ? 'saving' : state.save === 'error' ? 'error' : ''}`;
    el.innerHTML = `<span class="dot${state.save === 'error' ? ' red' : ''}"></span>${state.save === 'saving' ? '正在保存到本机…' : state.save === 'error' ? '保存失败，请重试' : '已保存到本机'}`;
  }
  async function mutation(fn, message = '', options = {}) {
    if (state.busy) { toast('正在保存上一步，请稍候。'); return null; }
    state.busy = true;
    state.save = 'saving';
    showSaveStatus();
    try {
      const result = await fn();
      if (result?.id && result.samples) {
        replaceExperiment(result);
        if (options.select) {
          state.activeId = result.id;
          state.selectedId = result.samples[0]?.id || null;
          state.regionRole = null;
          roles.forEach(role => { view[role].zoom = 1; view[role].panX = 0; view[role].panY = 0; });
          storeActive();
        }
      }
      await getResults();
      state.save = 'saved';
      state.error = null;
      if (message) toast(message);
      return result;
    } catch (error) {
      state.save = 'error';
      state.error = error.message;
      toast(error.message, true);
      try { await refreshState(); } catch (_) { /* preserve the last visible state */ }
      return null;
    } finally {
      state.busy = false;
      render();
    }
  }
  async function updateExperiment(edit, message = '') {
    const id = uid();
    return mutation(async () => {
      const copy = clone(active());
      edit(copy);
      return api(`/api/experiments/${encodeURIComponent(id)}`, {method:'PUT', body:JSON.stringify({experiment:copy})});
    }, message);
  }
  const steps = () => {
    const exp = active();
    const imageReady = exp && roles.every(role => exp.images[role]);
    const proposed = exp && roles.every(role => exp.rois[role]?.length);
    const reviewed = exp && exp.samples.every(s => roles.every(role => exp.rois[role]?.find(r => r.sampleId === s.id)?.confirmed));
    const index = !imageReady ? 0 : !proposed ? 1 : !reviewed ? 2 : 3;
    return `<div class="steps">${['导入与配对','定位条带','逐项复核','导出记录'].map((label,i) => `${i ? '<span class="step-line"></span>' : ''}<div class="step ${i < index ? 'done' : i === index ? 'current' : ''}"><span class="step-number">${i < index ? '✓' : i + 1}</span>${label}</div>`).join('')}</div>`;
  };
  function sidebar() {
    return `<aside class="sidebar"><div class="brand"><div class="brand-mark">${icon('logo')}</div><div><div class="brand-name">WB Workbench</div><div class="brand-sub">LOCAL ANALYSIS / 01</div></div></div>
      <div class="sidebar-section-head"><span>实验工作区</span><span class="mono">${String(state.experiments.length).padStart(2,'0')}</span></div>
      <button class="button new-experiment" data-action="new">${icon('plus')}新建实验</button>
      <nav class="experiment-list" aria-label="实验列表">${state.experiments.length ? state.experiments.map(e => `<button class="experiment-item${e.id === uid() ? ' active' : ''}" data-action="select-experiment" data-id="${esc(e.id)}" ${e.id === uid() ? 'aria-current="page"' : ''}>${icon('experiment')}<div><div class="experiment-title">${esc(e.name)}</div><div class="experiment-info">${shortDate(e.createdAt)} <span aria-hidden="true">·</span> ${e.samples.length} 个样本 <span aria-hidden="true">·</span> ${roles.filter(r => e.images[r]).length}/2 图像</div></div></button>`).join('') : '<div class="sidebar-empty">还没有实验记录</div>'}</nav>
      <div class="sidebar-bottom"><button class="sidebar-link" data-action="example">${icon('folder')}导入 WBTEST 样例</button><button class="sidebar-link" data-action="method">${icon('book')}计算方法与边界</button><div class="local-note"><span class="dot"></span>图像与数据均保存在本机</div></div></aside>`;
  }
  function topbar() {
    return `<header class="topbar"><div class="crumb">实验工作区 <span class="slash">/</span> <b>${active() ? '条带定量' : '概览'}</b></div><div class="topbar-right"><span id="save-status" class="save-status"><span class="dot"></span>已保存到本机</span><button class="button" data-action="export"${state.experiments.length ? '' : ' disabled'}>${icon('download')}导出${icon('down')}</button></div></header>`;
  }
  function emptyPage() {
    return `<div class="empty-workspace"><div class="empty-heading"><div class="eyebrow">WESTERN BLOT · REVIEWABLE BY DESIGN</div><h1>每一个结果，<br>都能回到原来的条带。</h1><p>导入两张蛋白图像，确认样本对应关系。从条带选区到背景校正，保留每一步测量与人工确认记录。</p><div class="empty-action-row"><button class="button primary" data-action="new">${icon('plus')}创建第一个实验</button><button class="button" data-action="example">${icon('folder')}用 WBTEST 样例开始</button></div></div>
      <div class="empty-visual"><div class="empty-visual-head"><span>成对图像 · 同一样本编号</span><span class="mono">PHOSPHO / TOTAL</span></div><div class="empty-pair"><div class="empty-image">${icon('image')}<span class="empty-image-label">磷酸化蛋白图</span></div><div class="empty-image">${icon('image')}<span class="empty-image-label">对应总蛋白图</span></div></div><div class="empty-visual-foot"><span>TIFF / PNG / JPEG / BMP</span><span>原图留存 · 坐标可追溯 · 本机处理</span></div></div>
      <div class="empty-features"><div class="empty-feature"><div class="empty-feature-number">01 /</div><h3>你指定目标，软件建议位置</h3><p>圈定目标条带区域，自动建议泳道、条带框和背景框。</p></div><div class="empty-feature"><div class="empty-feature-number">02 /</div><h3>确认之后，才发布比值</h3><p>逐项核对配对选区；调整任一测量框后，需要重新确认。</p></div><div class="empty-feature"><div class="empty-feature-number">03 /</div><h3>带着计算依据一起导出</h3><p>Excel、CSV 与标记图像，保留原始来源和实验内对照。</p></div></div></div>`;
  }
  function imageCard(role) {
    const exp = active(), img = exp.images[role], v = view[role];
    const region = exp.settings[role].region;
    const warningList = img?.warnings || [];
    return `<article class="image-card${state.regionRole === role ? ' region-active' : ''}"><div class="image-card-head"><div class="image-role"><span class="role-marker ${role}"></span>${roleNames[role]}<span class="role-short">${role === 'pho' ? 'PHO' : 'TOTAL'}</span></div><div style="display:flex;align-items:center;gap:5px"><span class="image-filename" title="${esc(img?.filename || '')}">${esc(img?.filename || '')}</span><button class="icon-button" data-action="upload" data-role="${role}" aria-label="${img ? '替换' : '上传'}${roleNames[role]}图" title="${img ? '替换图像会清除本图选区' : '上传图像'}">${icon('upload')}</button></div></div>
      ${img ? `<div class="image-card-tools"><div class="tool-actions"><button class="button small ${state.regionRole === role ? 'orange' : 'plain'}" data-action="region" data-role="${role}" title="在图像内拖动，圈定整排目标条带">${icon('crop')}${state.regionRole === role ? '取消圈选' : '圈选区域'}</button><button class="button small soft" data-action="suggest" data-role="${role}"${region ? '' : ' disabled'} title="${region ? '根据实验样本数建议条带与背景框' : '请先圈选目标条带所在区域'}">${icon('suggest')}自动建议</button></div><div class="segmented" aria-label="${roleNames[role]}信号模式"><button class="${exp.settings[role].polarity === 'dark' ? 'active' : ''}" data-action="polarity" data-role="${role}" data-value="dark" title="浅色背景、深色条带">暗条带</button><button class="${exp.settings[role].polarity === 'bright' ? 'active' : ''}" data-action="polarity" data-role="${role}" data-value="bright" title="深色背景、亮色条带">亮条带</button></div></div>
      <div class="canvas-wrap"><canvas class="image-canvas" data-role="${role}" aria-label="${roleNames[role]}图像与可调整选区；也可在样本检查器输入像素坐标" tabindex="0"></canvas><div class="canvas-hint${state.regionRole === role ? ' drawing' : ''}">${state.regionRole === role ? '拖动圈定整排目标条带 · Esc 取消' : !exp.rois[role]?.length ? '先圈定目标区域，再自动建议' : `${v.showOverlay ? '选区可拖动 · 拖动边角调整大小' : '原图预览 · 选区暂时隐藏'}`}</div><div class="canvas-zoom"><button class="icon-button" data-action="zoom" data-role="${role}" data-value="out" aria-label="缩小">${icon('minus')}</button><span class="zoom-label" id="zoom-${role}">${Math.round(v.zoom * 100)}%</span><button class="icon-button" data-action="zoom" data-role="${role}" data-value="in" aria-label="放大">${icon('plus')}</button><button class="icon-button" data-action="zoom" data-role="${role}" data-value="fit" aria-label="适应画布" title="适应画布">${icon('fit')}</button></div></div>
      <div class="image-card-foot"><span class="chip">${esc(img.format)} · ${img.bitDepth} bit</span><span class="chip">${img.width} × ${img.height} px</span><span class="chip${exp.rois[role]?.length ? ' green' : ''}">${exp.rois[role]?.length || 0} 个条带框</span><button class="button small plain" style="margin-left:auto;font-size:10px;padding:3px 5px;min-height:21px" data-action="overlay" data-role="${role}">${icon('eye')}${v.showOverlay ? '查看原图' : '显示标记'}</button></div>
      ${warningList.length ? `<div class="source-warning">${icon('warning')}<span>${esc(warningList[0])}</span></div>${warningList.length > 1 ? `<details class="source-details"><summary>另 ${warningList.length - 1} 条图像来源提示</summary><ul>${warningList.slice(1).map(w => `<li>${esc(w)}</li>`).join('')}</ul></details>` : ''}` : ''}` : `<div class="upload-empty"><div class="upload-icon">${icon('image')}</div><strong>导入${roleNames[role]}图像</strong><p>TIFF、PNG、JPEG 或 BMP<br>原始文件会保留在本机实验记录中</p><button class="button small" data-action="upload" data-role="${role}">${icon('upload')}选择图像</button></div>`}
      <input class="file-input" id="file-${role}" type="file" data-role="${role}" accept=".tif,.tiff,.jpg,.jpeg,.png,.bmp,image/tiff,image/jpeg,image/png,image/bmp"></article>`;
  }
  function resultsPanel() {
    const exp = active(), results = state.results;
    const count = exp.samples.filter(s => roles.every(role => roiFor(role,s.id)?.confirmed)).length;
    const rows = results?.rows || [];
    return `<section class="results-panel"><div class="panel-head"><div><div class="section-title">${icon('table')}样本定量表 <span class="chip">${exp.samples.length} SAMPLES</span></div><div class="section-subtitle">按样本编号配对；点击任一行，在双图中核对选区。</div></div><button class="button small" data-action="confirm-all">${icon('checkCircle')}全部复核确认</button></div>
      <div class="results-meta"><span>对照基准 <strong>${formatNumber(results?.controlMean)}</strong> <span class="muted">· ${exp.samples.filter(s => s.control).length} 个指定对照</span></span><span>${results?.controlReady ? '实验内对照已就绪' : '对照未就绪，相对值暂不发布'}</span></div>
      <div class="table-scroll"><table aria-label="样本定量结果"><thead><tr><th>样本</th><th>分组</th><th style="text-align:right" title="磷酸化条带背景校正后的积分信号">PHO 净信号</th><th style="text-align:right" title="对应总蛋白条带背景校正后的积分信号">TOTAL 净信号</th><th style="text-align:right">p/total</th><th style="text-align:right">相对对照</th><th>计算状态</th><th>确认</th></tr></thead><tbody>${exp.samples.map((s,i) => {
        const r = rows.find(row => row.sampleId === s.id), checked = roles.every(role => roiFor(role,s.id)?.confirmed);
        const status = r?.status || '等待图像与选区';
        return `<tr data-action="select-sample" data-id="${esc(s.id)}" class="${s.id === state.selectedId ? 'selected' : ''}" tabindex="0" aria-selected="${s.id === state.selectedId}"><td><div class="sample-cell"><span class="sample-index">${String(i+1).padStart(2,'0')}</span><span class="sample-name">${esc(s.name)}</span></div></td><td><span class="group-tag${s.control ? ' control' : ''}">${esc(s.group || (s.control ? '对照组' : '未分组'))}${s.control ? ' · C' : ''}</span></td><td class="numeric">${formatNumber(r?.pho?.net)}</td><td class="numeric">${formatNumber(r?.total?.net)}</td><td class="numeric">${formatNumber(r?.ratio)}</td><td class="numeric strong">${formatNumber(r?.relative)}</td><td><span class="status-pill${r?.relative != null ? ' verified' : r?.warnings?.length ? ' warning' : ''}" title="${esc([status,...(r?.warnings || [])].join('；'))}">${r?.relative != null ? icon('check') : ''}${esc(status)}</span></td><td><span class="review-check${checked ? ' checked' : ''}" title="${checked ? '双图已人工确认' : '尚未人工确认'}">${checked ? icon('check') : '·'}</span></td></tr>`;
      }).join('')}</tbody></table></div><div class="table-footer"><span>${count}/${exp.samples.length} 个样本已确认 <span class="muted">· 净信号可预览，比值须确认后发布</span></span><button class="button small plain" data-action="export">${icon('download')}导出</button></div><div class="method-note"><b>计算路径</b>　背景校正积分信号 → p/total → 除以本实验有效对照的均值。<br>不同膜的 p/total 是图像信号指标；仅凭成对图片不能确认上样量与转膜差异已经控制。</div></section>`;
  }
  function roiEditor(role) {
    const roi = roiFor(role), kind = view[role].kind, measurement = selectedRow()?.[role];
    return `<details class="roi-editor" open><summary><span>${roleNames[role]} <span class="mono tiny" style="margin-left:4px;color:#a0ac94">${role.toUpperCase()}</span></span></summary>${roi ? `<div class="roi-tabs"><button class="roi-tab${kind === 'band' ? ' active' : ''}" data-action="roi-kind" data-role="${role}" data-value="band">条带框</button><button class="roi-tab background${kind === 'background' ? ' active' : ''}" data-action="roi-kind" data-role="${role}" data-value="background">背景框</button><span class="chip${roi.confirmed ? ' green' : ''}" style="margin-left:auto;font-size:8px">${roi.confirmed ? '已确认' : '待确认'}</span></div><div class="coord-grid">${['x','y','w','h'].map(k => `<div><label for="coord-${role}-${k}">${{x:'X',y:'Y',w:'宽 W',h:'高 H'}[k]}</label><input id="coord-${role}-${k}" class="coord-input" type="number" step="1" min="${k === 'w' || k === 'h' ? '1' : '0'}" value="${roi[kind]?.[k] ?? 0}" data-role="${role}" data-kind="${kind}" data-key="${k}" aria-label="${roleNames[role]}${kind === 'band' ? '条带' : '背景'}${k} 像素"></div>`).join('')}</div><div class="roi-detail-line"><span>背景校正积分信号</span><strong>${formatNumber(measurement?.net)}</strong></div>${measurement && !measurement.valid ? '<div class="result-warnings">测量暂不可用于比值，请检查选区与背景。</div>' : ''}` : '<div class="roi-missing">尚无此样本的选区。<br>在上方图像圈定目标区域，再点击自动建议。</div>'}</details>`;
  }
  function inspector() {
    const exp = active(), s = sample();
    if (!s) return '';
    const hasPair = roles.every(role => roiFor(role)), confirmed = roles.every(role => roiFor(role)?.confirmed);
    const index = exp.samples.findIndex(item => item.id === s.id);
    return `<aside class="inspector"><div class="inspector-head"><div class="inspector-title">${icon('inspect')}样本检查器</div><div style="display:flex;align-items:center;gap:4px"><button class="icon-button" data-action="sample-prev" aria-label="上一个样本"${index === 0 ? ' disabled' : ''} style="transform:rotate(180deg)">${icon('chevron')}</button><span class="inspector-counter">${String(index+1).padStart(2,'0')} / ${String(exp.samples.length).padStart(2,'0')}</span><button class="icon-button" data-action="sample-next" aria-label="下一个样本"${index >= exp.samples.length-1 ? ' disabled' : ''}>${icon('chevron')}</button></div></div><div class="inspector-content"><div><div class="sample-edit-grid"><div class="field"><label for="sample-name">样本编号</label><input id="sample-name" data-field="name" value="${esc(s.name)}" maxlength="80"></div><div class="field"><label for="sample-group">实验分组</label><input id="sample-group" data-field="group" value="${esc(s.group)}" maxlength="80" list="sample-groups"><datalist id="sample-groups">${[...new Set(['对照组','实验组',...exp.samples.map(i => i.group).filter(Boolean)])].map(g => `<option value="${esc(g)}"></option>`).join('')}</datalist></div></div><label class="check-label"><input type="checkbox" id="sample-control" data-field="control"${s.control ? ' checked' : ''}>将本样本用作本实验对照</label></div>${roles.map(roiEditor).join('')}</div><div class="confirm-area"><button class="button ${confirmed ? 'soft' : 'primary'}" data-action="confirm"${hasPair ? '' : ' disabled'}>${icon('checkCircle')}${confirmed ? '已确认此样本的双图选区' : '确认此样本的双图选区'}</button><div class="confirm-hint">确认配对身份、条带框与背景框。<br>修改选区或信号模式后，需重新确认。</div></div></aside>`;
  }
  function measurementDetails() {
    const row = selectedRow(), s = sample();
    if (!s) return '';
    return `<details class="details-section" id="measurement-details"><summary><span style="font:inherit;color:inherit">测量与来源明细 · ${esc(s.name)}</span><span>${esc(row?.recordId || '等待测量记录')}${icon('down')}</span></summary><div class="measurement-grid">${roles.map(role => {
      const m = row?.[role], img = active().images[role], roi = roiFor(role);
      return `<div class="measurement-card"><h3>${roleNames[role]}</h3>${m ? `<div class="measurement-list">${[['条带像素数',m.area],['原始像素和',m.rawSum],['背景像素数',m.backgroundArea],['背景均值',m.backgroundMean],['最小像素值',m.min],['最大像素值',m.max],['端点像素数',m.endpointCount],['净积分信号',m.net]].map(([key,value]) => `<div class="measurement-line"><span>${key}</span><span>${formatNumber(value)}</span></div>`).join('')}</div><div class="result-warnings">${(m.warnings || []).map(w => esc(w)).join('<br>')}</div>` : '<p class="tiny muted">尚无可显示的测量值。</p>'}<div class="audit-fields">图像：${esc(img?.filename || '未导入')}<br>选区状态：${roi?.confirmed ? '已人工确认' : '未确认'}<br>SHA-256：${esc(img?.sha256 || '—')}${img?.originalUrl ? `<br><a href="${esc(img.originalUrl)}" target="_blank" rel="noopener">查看保存的原始图像</a>` : ''}</div></div>`;
    }).join('')}</div>${row?.warnings?.length ? `<div class="result-warnings" style="padding:0 0 15px">${row.warnings.map(w => esc(w)).join('<br>')}</div>` : ''}</details><details class="details-section"><summary>实验备注与来源说明${icon('down')}</summary><textarea class="notes-input" id="experiment-notes" placeholder="记录样本来源、两块膜的对应关系、曝光条件或截图来源。">${esc(active().notes || '')}</textarea></details>`;
  }
  function experimentPage() {
    const exp = active(), checked = exp.samples.filter(s => roles.every(role => roiFor(role,s.id)?.confirmed)).length;
    return `<div class="page-heading"><div><div class="eyebrow">EXPERIMENT / ${esc(exp.id.slice(-8).toUpperCase())}</div><div class="heading-edit"><h1>${esc(exp.name)}</h1><button class="icon-button" data-action="rename" aria-label="编辑实验名称">${icon('edit')}</button></div><p>逐项核对图像选区、样本配对与背景校正，让结果有据可查。</p></div><div class="progress-summary"><div class="stat"><div class="stat-label">样本</div><div class="stat-value">${String(exp.samples.length).padStart(2,'0')}<span>个</span></div></div><div class="stat"><div class="stat-label">双图配对</div><div class="stat-value">${roles.filter(r => exp.images[r]).length}<span>/ 2</span></div></div><div class="stat"><div class="stat-label">人工确认</div><div class="stat-value green">${String(checked).padStart(2,'0')}<span>/ ${exp.samples.length}</span></div></div></div></div>${steps()}
      <div class="stage-toolbar"><div><h2 class="section-title">${icon('image')}成对图像工作台</h2><p class="section-subtitle">两张图分别定位，按同一样本编号配对。</p></div><div class="legend"><span><i class="legend-box"></i>条带</span><span><i class="legend-box selected"></i>当前样本</span><span><i class="legend-box background"></i>背景</span></div></div><div class="image-grid">${roles.map(imageCard).join('')}</div><div class="image-instructions"><span><b>操作</b>　圈选整排目标区域 → 自动建议 → 点击条带或表格检查 → 确认</span><span>所有坐标均为原图像素 · 缩放只影响显示</span></div><div class="bottom-grid">${resultsPanel()}${inspector()}</div>${measurementDetails()}<div class="bottom-note"><span>${icon('shield')}本地测量 · 原图不可变保存 · 修改有记录</span><span>WB Workbench / v1.0 · ${shortDate(exp.updatedAt || exp.createdAt)} 更新</span></div>`;
  }
  function render() {
    const detailsOpen = $('#measurement-details')?.open;
    app.innerHTML = `<div class="app-shell">${sidebar()}<div class="workspace">${topbar()}<main class="page">${state.error ? `<div class="error-banner"><span>${icon('warning')} ${esc(state.error)}</span><button class="button small" data-action="retry">${icon('refresh')}重新读取</button></div>` : ''}${active() ? experimentPage() : emptyPage()}</main></div></div>`;
    if (detailsOpen && $('#measurement-details')) $('#measurement-details').open = true;
    showSaveStatus();
    setupCanvases();
  }

  /* Canvas coordinates are original image pixels. DPR/zoom affect display only. */
  function imageFor(role) {
    const metadata = active()?.images[role];
    if (!metadata) return null;
    const key = metadata.previewUrl;
    if (!imageCache.has(key)) {
      const image = new Image();
      const item = {image,ready:false,error:false};
      imageCache.set(key,item);
      image.onload = () => { item.ready = true; drawCanvas(role); };
      image.onerror = () => { item.error = true; drawCanvas(role); };
      image.src = key;
    }
    return imageCache.get(key);
  }
  function transformFor(canvas, role) {
    const im = active()?.images[role];
    if (!im) return null;
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const scale = Math.min((w - 34) / im.width, (h - 60) / im.height) * view[role].zoom;
    const v = view[role];
    const maxX = Math.max(0, (im.width * scale - w) / 2 + 17), maxY = Math.max(0, (im.height * scale - h) / 2 + 30);
    v.panX = Math.max(-maxX, Math.min(maxX, v.panX));
    v.panY = Math.max(-maxY, Math.min(maxY, v.panY));
    return {scale, x:(w-im.width*scale)/2+v.panX, y:(h-im.height*scale)/2+v.panY, w, h};
  }
  function canvasPoint(event, canvas, role, clamp = true) {
    const box = canvas.getBoundingClientRect(), t = transformFor(canvas,role), im = active().images[role];
    if (!t) return null;
    let x = (event.clientX-box.left-t.x)/t.scale, y = (event.clientY-box.top-t.y)/t.scale;
    if (clamp) { x = Math.max(0,Math.min(im.width,x)); y = Math.max(0,Math.min(im.height,y)); }
    return {x,y};
  }
  function drawCanvas(role) {
    const canvas = $(`canvas[data-role="${role}"]`), exp = active(), im = exp?.images[role];
    if (!canvas || !im) return;
    const dpr = window.devicePixelRatio || 1, t = transformFor(canvas,role), item = imageFor(role);
    canvas.width = Math.round(t.w*dpr); canvas.height = Math.round(t.h*dpr);
    const ctx = canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,t.w,t.h);
    if (!item?.ready) {
      ctx.fillStyle = '#85967b'; ctx.textAlign = 'center'; ctx.font = '11px sans-serif';
      ctx.fillText(item?.error ? '预览无法读取，请重新导入图像。' : '正在加载图像…',t.w/2,t.h/2); return;
    }
    ctx.save();
    ctx.shadowColor = '#3143281a'; ctx.shadowBlur = 10; ctx.shadowOffsetY = 3;
    ctx.fillStyle = '#fff';ctx.fillRect(t.x-1,t.y-1,im.width*t.scale+2,im.height*t.scale+2);
    ctx.restore();
    ctx.imageSmoothingEnabled = view[role].zoom < 2;
    ctx.drawImage(item.image,t.x,t.y,im.width*t.scale,im.height*t.scale);
    const toBox = r => ({x:t.x+r.x*t.scale,y:t.y+r.y*t.scale,w:r.w*t.scale,h:r.h*t.scale});
    if (view[role].showOverlay) {
      const region = exp.settings[role].region;
      if (region) {
        const b=toBox(region);ctx.strokeStyle='#5b8d7575';ctx.lineWidth=1;ctx.setLineDash([4,4]);ctx.strokeRect(b.x,b.y,b.w,b.h);ctx.setLineDash([]);
      }
      const roiList = [...(exp.rois[role] || [])].sort((a,b) => Number(a.sampleId === state.selectedId)-Number(b.sampleId === state.selectedId));
      for (const roi of roiList) {
        const isSelected = roi.sampleId === state.selectedId;
        for (const kind of ['background','band']) {
          const r=roi[kind];if (!r) continue;
          const b=toBox(r), color=kind==='background' ? '#8b6aa6' : isSelected ? '#d47631' : '#1e8979';
          ctx.fillStyle = `${color}${isSelected ? '18' : '07'}`;ctx.fillRect(b.x,b.y,b.w,b.h);
          ctx.strokeStyle=color;ctx.lineWidth=isSelected?1.7:1.2;ctx.setLineDash(kind==='background'?[3,2]:[]);ctx.strokeRect(b.x,b.y,b.w,b.h);ctx.setLineDash([]);
          if (isSelected && view[role].kind === kind) {
            for (const [hx,hy] of [[b.x,b.y],[b.x+b.w,b.y],[b.x,b.y+b.h],[b.x+b.w,b.y+b.h]]) {
              ctx.fillStyle='#fffefa';ctx.fillRect(hx-2.5,hy-2.5,5,5);ctx.strokeStyle=color;ctx.lineWidth=1;ctx.strokeRect(hx-2.5,hy-2.5,5,5);
            }
          }
        }
        const s=exp.samples.find(s=>s.id===roi.sampleId), box=toBox(roi.band);
        const label=s?.name || '', lx=box.x+box.w/2, ly=Math.max(10,box.y-7);
        ctx.font=`${isSelected?'600 ':' '}10px -apple-system, sans-serif`;ctx.textAlign='center';
        const width=ctx.measureText(label).width+9;
        ctx.fillStyle=isSelected?'#fff0ddf2':'#f8faf3e8';ctx.fillRect(lx-width/2,ly-10,width,14);
        ctx.fillStyle=isSelected?'#a96a31':'#52866f';ctx.fillText(label,lx,ly);
        if(roi.confirmed) {ctx.fillStyle='#2c896b';ctx.beginPath();ctx.arc(box.x+box.w-3,box.y+3,2.5,0,Math.PI*2);ctx.fill();}
      }
    }
    if (state.drag?.type === 'region' && state.drag.role === role) {
      const b=toBox(state.drag.rect);ctx.fillStyle='#d77c3620';ctx.fillRect(b.x,b.y,b.w,b.h);ctx.strokeStyle='#cf7a37';ctx.lineWidth=1.5;ctx.setLineDash([5,3]);ctx.strokeRect(b.x,b.y,b.w,b.h);ctx.setLineDash([]);
    }
    canvas.style.cursor=state.regionRole===role ? 'crosshair' : state.drag?.role===role ? 'grabbing' : 'default';
    const label=$(`#zoom-${role}`);if(label)label.textContent=`${Math.round(view[role].zoom*100)}%`;
  }
  function hitTest(p, role, scale) {
    if (!view[role].showOverlay) return null;
    const rois = [...(active().rois[role] || [])].sort((a,b) => Number(b.sampleId===state.selectedId)-Number(a.sampleId===state.selectedId));
    const margin=5/scale;
    for(const roi of rois) {
      const kinds = roi.sampleId===state.selectedId ? [view[role].kind,view[role].kind==='band'?'background':'band'] : ['band','background'];
      for(const kind of kinds) {
        const r=roi[kind];if(!r)continue;
        if(p.x<r.x-margin || p.x>r.x+r.w+margin || p.y<r.y-margin || p.y>r.y+r.h+margin)continue;
        let handle='';
        if(Math.abs(p.y-r.y)<margin)handle+='n';else if(Math.abs(p.y-r.y-r.h)<margin)handle+='s';
        if(Math.abs(p.x-r.x)<margin)handle+='w';else if(Math.abs(p.x-r.x-r.w)<margin)handle+='e';
        return {roi,kind,handle:handle || 'move'};
      }
    }
    return null;
  }
  function geometryValid(rect,img) {
    return !!rect && ['x','y','w','h'].every(k => Number.isInteger(rect[k])) && rect.x>=0 && rect.y>=0 && rect.w>0 && rect.h>0 && rect.x+rect.w<=img.width && rect.y+rect.h<=img.height;
  }
  function setupCanvases() {
    $$('canvas.image-canvas').forEach(canvas => {
      const role=canvas.dataset.role;
      drawCanvas(role);
      canvas.addEventListener('pointerdown',event => {
        if(state.busy || event.button!==0)return;
        const p=canvasPoint(event,canvas,role), t=transformFor(canvas,role);
        if(state.regionRole===role) {
          state.drag={type:'region',role,start:p,rect:{x:Math.round(p.x),y:Math.round(p.y),w:0,h:0}};
          canvas.setPointerCapture(event.pointerId);event.preventDefault();return;
        }
        const hit=hitTest(p,role,t.scale);
        if(view[role].zoom > 1 && (event.shiftKey || !hit)) {
          state.drag={type:'pan',role,screenX:event.clientX,screenY:event.clientY,panX:view[role].panX,panY:view[role].panY};
          canvas.setPointerCapture(event.pointerId);event.preventDefault();return;
        }
        if(!hit)return;
        state.selectedId=hit.roi.sampleId;view[role].kind=hit.kind;
        state.drag={type:'roi',role,start:p,kind:hit.kind,handle:hit.handle,original:clone(hit.roi[hit.kind]),originalConfirmed:hit.roi.confirmed,sampleId:hit.roi.sampleId,moved:false};
        canvas.setPointerCapture(event.pointerId);event.preventDefault();
        roles.forEach(drawCanvas);
      });
      canvas.addEventListener('pointermove',event => {
        const p=canvasPoint(event,canvas,role), t=transformFor(canvas,role), drag=state.drag;
        if(!drag || drag.role!==role) {
          if(state.regionRole===role){canvas.style.cursor='crosshair';return;}
          const hit=hitTest(p,role,t.scale);
          const cursors={move:'grab',n:'ns-resize',s:'ns-resize',e:'ew-resize',w:'ew-resize',nw:'nwse-resize',se:'nwse-resize',ne:'nesw-resize',sw:'nesw-resize'};
          canvas.style.cursor=hit?cursors[hit.handle] || 'grab':'default';return;
        }
        if(drag.type==='pan') {
          view[role].panX=drag.panX+event.clientX-drag.screenX;
          view[role].panY=drag.panY+event.clientY-drag.screenY;
          drawCanvas(role);return;
        }
        if(drag.type==='region') {
          drag.rect={x:Math.round(Math.min(p.x,drag.start.x)),y:Math.round(Math.min(p.y,drag.start.y)),w:Math.round(Math.abs(p.x-drag.start.x)),h:Math.round(Math.abs(p.y-drag.start.y))};
          drawCanvas(role);return;
        }
        const im=active().images[role], r=clone(drag.original), dx=Math.round(p.x-drag.start.x),dy=Math.round(p.y-drag.start.y);
        if(dx===0 && dy===0 && !drag.moved)return;
        drag.moved=true;
        if(drag.handle==='move') {r.x=Math.max(0,Math.min(im.width-r.w,r.x+dx));r.y=Math.max(0,Math.min(im.height-r.h,r.y+dy));}
        else {
          const right=r.x+r.w,bottom=r.y+r.h;
          if(drag.handle.includes('w')){r.x=Math.max(0,Math.min(right-1,r.x+dx));r.w=right-r.x;}
          if(drag.handle.includes('e'))r.w=Math.max(1,Math.min(im.width-r.x,r.w+dx));
          if(drag.handle.includes('n')){r.y=Math.max(0,Math.min(bottom-1,r.y+dy));r.h=bottom-r.y;}
          if(drag.handle.includes('s'))r.h=Math.max(1,Math.min(im.height-r.y,r.h+dy));
        }
        const roi=roiFor(role,drag.sampleId);roi[drag.kind]=r;roi.confirmed=false;
        state.save='saving';showSaveStatus();drawCanvas(role);
        ['x','y','w','h'].forEach(k=>{const input=$(`#coord-${role}-${k}`);if(input && input.dataset.kind===drag.kind)input.value=r[k];});
      });
      canvas.addEventListener('pointerup',async event => {
        const drag=state.drag;if(!drag || drag.role!==role)return;
        if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
        state.drag=null;
        if(drag.type==='pan') { drawCanvas(role); return; }
        if(drag.type==='region') {
          const im=active().images[role],r=drag.rect;
          r.w=Math.min(r.w,im.width-r.x);r.h=Math.min(r.h,im.height-r.y);
          if(r.w<3 || r.h<3){toast('请圈选更大的目标区域，覆盖整排需要分析的条带。');drawCanvas(role);return;}
          state.regionRole=null;
          await updateExperiment(e=>{e.settings[role].region=r;e.rois[role].forEach(roi=>{roi.confirmed=false;});},'目标区域已保存，可以自动建议条带。');
        } else if(drag.moved) {
          await updateExperiment(()=>{},'选区已保存；此图的对应样本需重新确认。');
        } else render();
      });
      canvas.addEventListener('pointercancel',()=>{
        const drag=state.drag;if(!drag)return;
        if(drag.type==='roi'){const roi=roiFor(role,drag.sampleId);roi[drag.kind]=drag.original;roi.confirmed=drag.originalConfirmed;}
        state.drag=null;state.save='saved';render();
      });
    });
  }

  function closeDialog() { if(dialog.open)dialog.close();dialog.innerHTML=''; }
  function openDialog(title,body,footer,submit) {
    dialog.innerHTML=`<form id="dialog-form"><div class="dialog-head"><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" type="button" data-action="close-dialog" aria-label="关闭">${icon('close')}</button></div><div class="dialog-body">${body}<div class="dialog-error" id="dialog-error" role="alert"></div></div><div class="dialog-footer">${footer}</div></form>`;
    if(!dialog.open)dialog.showModal();
    $('#dialog-form').addEventListener('submit',async event=>{event.preventDefault();await submit?.(event);});
  }
  const cancelButton='<button type="button" class="button" data-action="close-dialog">取消</button>';
  function newExperimentDialog() {
    const date=new Date().toLocaleDateString('zh-CN').replaceAll('/','.');
    openDialog('新建实验',`<div class="field"><label for="new-name">实验名称</label><input id="new-name" name="name" value="WB 实验 · ${date}" maxlength="120" required></div><div class="dialog-fields"><div class="field"><label for="new-count">样本 / 泳道数</label><input id="new-count" name="sampleCount" type="number" min="1" max="32" step="1" value="6" required></div><div class="field"><label for="new-controls">前几个样本为对照</label><input id="new-controls" name="controlCount" type="number" min="0" max="32" step="1" value="3" required></div></div><div class="dialog-note">每个泳道对应一份样本，两张图使用相同样本编号。默认生成 Ctr / Trt 编号，之后可在检查器中修改。没有同期对照时填写 0。</div>`,`${cancelButton}<button class="button primary" type="submit">创建实验${icon('arrow')}</button>`,async()=>{
      const name=$('#new-name').value.trim(),sampleCount=Number($('#new-count').value),controlCount=Number($('#new-controls').value);
      if(!name || !Number.isInteger(sampleCount) || sampleCount<1 || sampleCount>32 || !Number.isInteger(controlCount) || controlCount<0 || controlCount>sampleCount){$('#dialog-error').textContent='请填写实验名称；样本数为 1–32，对照数不能超过样本数。';return;}
      const result=await mutation(()=>api('/api/experiments',{method:'POST',body:JSON.stringify({name,sampleCount,controlCount})}),'实验已创建。',{select:true});
      if(result)closeDialog();else if($('#dialog-error'))$('#dialog-error').textContent=state.error || '创建失败。';
    });
  }
  function exportDialog() {
    openDialog('导出可复核记录',`<div class="field"><label for="export-scope">导出范围</label><select id="export-scope"><option value="current"${active()?'':' disabled'}>当前实验${active()?` · ${esc(active().name)}`:''}</option><option value="all">全部实验 · ${state.experiments.length} 次</option></select></div><div class="field"><label for="export-format">文件类型</label><select id="export-format"><option value="zip">完整记录包 ZIP · 表格、原图、标记图、操作记录</option><option value="xlsx">Excel 工作簿 XLSX · 含测量与来源明细</option><option value="csv">CSV 数据表 · 含测量与来源明细</option></select></div><div class="dialog-note">未确认或无有效对照的结果会保留状态说明，数值保持空白。多个实验分别使用自己的对照；合并导出不会跨实验借用对照。</div>`,`${cancelButton}<button class="button primary" type="submit">${icon('download')}下载文件</button>`,async()=>{
      const scope=$('#export-scope').value,format=$('#export-format').value;
      const params=new URLSearchParams({format});if(scope==='current' && uid())params.set('experiment',uid());
      const button=$('#dialog-form button[type="submit"]');button.disabled=true;button.textContent='正在生成…';
      try {
        const response=await fetch(`/api/export?${params}`);
        if(!response.ok){let error;try{error=await response.json();}catch(_){}throw new Error(error?.error || `导出失败（${response.status}）`);}
        const blob=await response.blob();
        const disposition=response.headers.get('Content-Disposition') || '';
        let filename=`WB_Workbench_${scope==='all'?'all':active().name}.${format}`;
        const utf=disposition.match(/filename\*=UTF-8''([^;]+)/i),plain=disposition.match(/filename="?([^";]+)"?/i);
        if(utf)try{filename=decodeURIComponent(utf[1]);}catch(_){}else if(plain)filename=plain[1];
        const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),30000);
        closeDialog();toast('导出文件已生成。');
      } catch(error){$('#dialog-error').textContent=error.message;button.disabled=false;button.innerHTML=`${icon('download')}重新下载`;}
    });
  }
  function showMethod() {
    openDialog('计算方法与适用边界',`<div class="method-body"><h3>1 / 像素测量</h3><p>在原始图像数值上测量，预览缩放不参与计算。暗条带：<code>净信号 = 条带面积 × 背景均值 − 条带像素和</code>；亮条带方向相反。背景框须与条带框分离，并代表局部背景。</p><h3>2 / 配对与实验内归一化</h3><p>同一样本的磷酸化净信号除以对应总蛋白净信号，得到 p/total 图像信号比。再除以本实验所有指定对照的有效比值的算术均值，得到相对对照指标。仅在双图选区已确认且测量有效时发布比值。</p><h3>3 / 保留不确定性</h3><ul><li>净信号非正、框超界或背景与条带重叠，需修正后确认。</li><li>任一指定对照尚未就绪时，相对值暂不发布。</li><li>像素达到数字范围端点是复核提示，不能据此证明仪器检测已经饱和。</li><li>截图、JPEG 压缩、未知曝光和图像处理历史影响定量适用性，软件不能从图片恢复这些实验事实。</li><li>跨膜 p/total 不能单独证明上样量或转膜差异已经得到控制。</li></ul><h3>4 / 自动建议的范围</h3><p>根据你圈定的区域和指定泳道数建议选区。软件不能识别蛋白身份，也不会把自动建议当作人工确认。请逐个检查条带与背景；必要时拖动或输入坐标调整。</p><h3>5 / 本地保存</h3><p>实验和原始图像保存在本机服务的数据目录。浏览器刷新后会从服务恢复；导出的完整 ZIP 包保留计算依据。文件导入后不改写桌面上的原图。</p></div>`,`<button type="button" class="button primary" data-action="close-dialog">了解</button>`);
  }
  async function selectSample(id) {
    state.selectedId=id;state.regionRole=null;render();
  }
  async function confirmSamples(all) {
    const exp=active();
    const targets=all?exp.samples:[sample()];
    const eligible=targets.filter(s=>roles.every(role=>roiFor(role,s.id) && state.results?.rows.find(r=>r.sampleId===s.id)?.[role]?.valid));
    if(!eligible.length){toast('没有可确认的成对有效选区。请先检查两张图的条带、背景及测量状态。',true);return;}
    if(!all) {
      await updateExperiment(e=>{roles.forEach(role=>{const roi=e.rois[role].find(r=>r.sampleId===state.selectedId);roi.confirmed=true;});},'已确认当前样本的双图选区。');return;
    }
    const skipped=targets.length-eligible.length;
    openDialog('确认全部有效样本',`<p>即将确认 <strong>${eligible.length} 个样本</strong>的双图选区。请确认你已经逐一核对了样本配对、条带位置和背景区域。</p><div class="dialog-note">${skipped?`${skipped} 个样本存在缺失或无效测量，将保持未确认。`:'所有样本都有成对且有效的测量。'}<br>自动建议本身不代表条带身份或实验条件已被验证。</div>`,`${cancelButton}<button type="submit" class="button primary">${icon('checkCircle')}我已复核，确认 ${eligible.length} 个样本</button>`,async()=>{
      const ids=new Set(eligible.map(s=>s.id));
      const result=await updateExperiment(e=>{roles.forEach(role=>e.rois[role].forEach(roi=>{if(ids.has(roi.sampleId))roi.confirmed=true;}));},`已确认 ${eligible.length} 个样本。`);
      if(result)closeDialog();
    });
  }

  document.addEventListener('click',async event=>{
    const target=event.target.closest('[data-action]');if(!target || target.disabled)return;
    const action=target.dataset.action,role=target.dataset.role,value=target.dataset.value;
    if(action==='close-dialog'){closeDialog();return;}
    if(state.busy && !['method','overlay','zoom'].includes(action)){toast('正在保存上一步，请稍候。');return;}
    try {
      switch(action) {
        case 'new':newExperimentDialog();break;
        case 'example':await mutation(()=>api('/api/examples',{method:'POST',body:'{}'}),'已载入 WBTEST 截图样例；自动建议仍需人工确认。',{select:true});break;
        case 'method':showMethod();break;
        case 'export':exportDialog();break;
        case 'retry':state.busy=true;try{await refreshState();state.error=null;state.save='saved';}finally{state.busy=false;render();}break;
        case 'select-experiment':state.activeId=target.dataset.id;state.selectedId=active().samples[0]?.id;state.regionRole=null;state.results=null;roles.forEach(r=>{view[r].zoom=1;view[r].panX=0;view[r].panY=0;view[r].showOverlay=true;});storeActive();render();await getResults();render();break;
        case 'select-sample':await selectSample(target.dataset.id);break;
        case 'sample-prev':case 'sample-next': {const samples=active().samples,index=samples.findIndex(s=>s.id===state.selectedId),next=samples[index+(action==='sample-next'?1:-1)];if(next)await selectSample(next.id);break;}
        case 'upload':$(`#file-${role}`).click();break;
        case 'region':state.regionRole=state.regionRole===role?null:role;view[role].showOverlay=true;render();break;
        case 'suggest':await mutation(()=>api(`/api/experiments/${encodeURIComponent(uid())}/suggest/${role}`,{method:'POST',body:JSON.stringify({region:active().settings[role].region,polarity:active().settings[role].polarity})}),'已生成建议选区。请逐个检查后确认。');break;
        case 'polarity':if(active().settings[role].polarity!==value)await updateExperiment(e=>{e.settings[role].polarity=value;e.rois[role].forEach(roi=>{roi.confirmed=false;});},'信号模式已更新；此图选区需重新确认。');break;
        case 'overlay':view[role].showOverlay=!view[role].showOverlay;render();break;
        case 'zoom':view[role].zoom=value==='fit'?1:Math.max(.5,Math.min(8,view[role].zoom*(value==='in'?1.25:.8)));if(value==='fit'){view[role].panX=0;view[role].panY=0;}drawCanvas(role);{const hint=$(`canvas[data-role="${role}"]`).parentNode.querySelector('.canvas-hint');if(hint && !state.regionRole)hint.textContent=view[role].zoom>1?'选区可拖动 · Shift + 拖动平移图像':'选区可拖动 · 拖动边角调整大小';}break;
        case 'roi-kind':view[role].kind=value;view[role].showOverlay=true;render();break;
        case 'confirm':await confirmSamples(false);break;
        case 'confirm-all':await confirmSamples(true);break;
        case 'rename':openDialog('编辑实验名称',`<div class="field"><label for="rename-experiment">实验名称</label><input id="rename-experiment" value="${esc(active().name)}" maxlength="120" required></div>`,`${cancelButton}<button type="submit" class="button primary">保存名称</button>`,async()=>{const name=$('#rename-experiment').value.trim();if(!name)return;const result=await updateExperiment(e=>{e.name=name;},'实验名称已保存。');if(result)closeDialog();});break;
      }
    } catch(error){state.error=error.message;toast(error.message,true);render();}
  });
  document.addEventListener('change',async event=>{
    const el=event.target;
    if(el.matches('.file-input')) {
      const file=el.files[0];if(!file)return;
      if(state.busy){toast('正在保存上一步，请稍候。');el.value='';return;}
      const role=el.dataset.role;
      if(file.size>45*1024*1024){toast('这张文件超过 45 MB，请选择较小的单帧图像。',true);el.value='';return;}
      const performUpload=async()=>{
        const result=await mutation(async()=>{
          const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(new Error('无法读取所选文件。'));reader.readAsDataURL(file);});
          return api(`/api/experiments/${encodeURIComponent(uid())}/images/${role}`,{method:'POST',body:JSON.stringify({filename:file.name,data})});
        },`${roleNames[role]}图已导入，原始文件已保留。`);
        if(result){view[role].zoom=1;view[role].panX=0;view[role].panY=0;view[role].showOverlay=true;state.regionRole=null;closeDialog();render();}
      };
      if(active().images[role])openDialog(`替换${roleNames[role]}图`, `<p>新图像：<strong>${esc(file.name)}</strong></p><div class="dialog-note">此实验当前图像的条带框、背景框及确认状态将被清除，需要重新定位。已保存的原图仍保留在本地数据目录。</div>`,`${cancelButton}<button type="submit" class="button primary">替换图像</button>`,performUpload);else await performUpload();
    } else if(el.matches('[data-field]')) {
      if(state.busy){toast('正在保存上一步，请稍候。');render();return;}
      const key=el.dataset.field,value=key==='control'?el.checked:el.value.trim();
      if(key==='name' && !value){toast('样本编号不能为空。',true);render();return;}
      if(key==='name' && active().samples.some(s=>s.id!==state.selectedId && s.name===value)){toast('本实验中已有相同样本编号，请使用唯一编号。',true);render();return;}
      await updateExperiment(e=>{e.samples.find(s=>s.id===state.selectedId)[key]=value;});
    } else if(el.matches('.coord-input')) {
      if(state.busy){toast('正在保存上一步，请稍候。');render();return;}
      const {role,kind,key}=el.dataset,value=Number(el.value),roi=roiFor(role),rect={...roi[kind],[key]:value};
      if(!geometryValid(rect,active().images[role])){toast('坐标必须为整数；选框须完整位于图像内，宽高至少为 1 像素。',true);render();return;}
      await updateExperiment(e=>{const r=e.rois[role].find(r=>r.sampleId===state.selectedId);r[kind]=rect;r.confirmed=false;});
    } else if(el.id==='experiment-notes') {
      if(state.busy){toast('正在保存上一步，请稍候。');return;}
      await updateExperiment(e=>{e.notes=el.value;});
    }
  });
  document.addEventListener('keydown',event=>{
    if(event.key==='Escape' && state.regionRole){state.regionRole=null;state.drag=null;render();}
    if((event.key==='Enter' || event.key===' ') && event.target.matches('tr[data-action="select-sample"]')){event.preventDefault();selectSample(event.target.dataset.id);}
  });
  dialog.addEventListener('click',event=>{if(event.target===dialog){const box=dialog.getBoundingClientRect();if(event.clientX<box.left || event.clientX>box.right || event.clientY<box.top || event.clientY>box.bottom)closeDialog();}});
  let resizeTimer;
  window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>roles.forEach(drawCanvas),50);});
  window.addEventListener('beforeunload',event=>{if(state.busy || state.drag?.moved){event.preventDefault();event.returnValue='';}});
  async function init() {
    state.activeId=restoreActive();
    try {await refreshState(false);state.error=null;}catch(error){state.error=`无法连接本机分析服务：${error.message}`;state.save='error';}
    render();
  }
  init();
})();
