/* All values come from the local API; nothing on this page is a placeholder number. */
const state = {tab: 'dashboard', data: {}, job: null, kind: 'variance', window: 300, predictJson: null, predictResult: null};
const WINDOWS = {300:'5분', 3600:'1시간', 21600:'6시간', 86400:'24시간'};
const ago = ts => { const s = Math.max(0, Date.now()/1000-ts); return s < 60 ? `${Math.round(s)}초 전` : s < 3600 ? `${Math.round(s/60)}분 전` : `${Math.round(s/3600)}시간 전`; };
const $ = id => document.getElementById(id);
const escape = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v, d=1) => v === null || v === undefined || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('ko-KR', {maximumFractionDigits:d});
// Prices: USD/lb (~1.3) needs 3 decimals, EUR/100kg (~430) needs 1.
const price = v => num(v, Math.abs(Number(v)) < 10 ? 3 : 1);
const pct = (v, d=2) => v === null || v === undefined ? '—' : `${num(v, d)}%`;
const day = v => v ? String(v).slice(0, 10) : '—';
const badge = (text, color='') => `<span class="badge ${color}">${escape(text)}</span>`;
const empty = (title, detail='') => `<div class="empty"><strong>${escape(title)}</strong>${escape(detail)}</div>`;
const panel = (title, body, right='') => `<section class="panel"><div class="panel-head"><h2>${title}</h2>${right ? `<div class="panel-actions">${right}</div>` : ''}</div>${body}</section>`;
const table = (headers, rows) => `<div class="table-wrap"><table><thead><tr>${headers.map(h=>`<th>${escape(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
const definition = (name, value) => `<div class="definition"><span>${escape(name)}</span><strong>${value}</strong></div>`;
const JOB = {queued:['대기','amber'], running:['실행 중','amber'], succeeded:['완료','green'], failed:['실패','red'], cancelled:['취소','']};
const KIND_LABEL = {none:'정상 입력', level_ramp:'이상 입력 (파인튜닝 X)', variance:'이상 입력 (파인튜닝)'};
const KIND_NOTE = {none:'평소와 같은 변동', level_ramp:'4주에 걸쳐 가격 +15% 후 유지 — 일시적 충격', variance:'주간 변동폭 3배 — 지속적인 체제 변화'};
const LEVEL = [['0단계 (일반)', 'green'], ['1단계 (감시)', 'amber'], ['2단계 (파인튜닝)', 'red']];

async function api(path, options={}) {
  const response = await fetch(`/api${path}`, options);
  const body = await response.json();
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail));
  return body;
}
const post = (path, body={}) => api(path, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').hidden = true, 6000); }

/* Inline SVG line chart. Non-finite values are gaps; `marks` draws dashed verticals at indexes. */
function chart(series, {height=220, labels=[], marks=[], markName='재학습'}={}) {
  const all = series.flatMap(s => s.values).filter(Number.isFinite);
  if (!all.length) return empty('표시할 값이 없습니다');
  const width = 760, left = 54, right = 14, top = 12, bottom = 28, n = Math.max(...series.map(s => s.values.length));
  const min = Math.min(...all), max = Math.max(...all), span = max-min || Math.abs(max)*.05 || 1;
  const low = min >= 0 ? Math.max(0, min-span*.1) : min-span*.1, high = max+span*.1;
  const x = i => left+i*(width-left-right)/Math.max(n-1, 1), y = v => top+(high-v)/(high-low)*(height-top-bottom);
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(series.map(s=>s.name).join(', '))}">`;
  for (let i = 0; i < 4; i++) { const v = low+(high-low)*i/3; svg += `<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" stroke="var(--grid)"/><text x="${left-8}" y="${y(v)+4}" text-anchor="end">${num(v, span < 10 ? 2 : 0)}</text>`; }
  // Event marks (fine-tune moments) are solid purple verticals so they never read as a threshold line.
  marks.forEach(i => { svg += `<line x1="${x(i)}" y1="${top}" x2="${x(i)}" y2="${height-bottom}" stroke="#7b5ea7" stroke-width="2" opacity=".8"/>`; });
  series.forEach(s => {
    const points = s.values.map((v, i) => Number.isFinite(v) ? `${x(i)},${y(v)}` : null).filter(Boolean);
    svg += `<polyline points="${points.join(' ')}" fill="none" stroke="${s.color||'var(--blue)'}" stroke-width="${s.width||2.4}" ${s.dash?'stroke-dasharray="5 5"':''} stroke-linejoin="round"/>`;
    s.values.forEach((v, i) => { if (s.dots && Number.isFinite(v)) svg += `<circle cx="${x(i)}" cy="${y(v)}" r="4" fill="${s.color||'var(--blue)'}"/>`; });
  });
  if (labels.length) [0, Math.floor((labels.length-1)/2), labels.length-1].forEach((index, i) => { svg += `<text x="${x(index)}" y="${height-6}" text-anchor="${['start','middle','end'][i]}">${escape(labels[index])}</text>`; });
  return svg+'</svg>'+`<div class="legend">${series.map(s=>`<span><i style="background:${s.color||'var(--blue)'}"></i>${escape(s.name)}</span>`).join('')}${marks.length?`<span><i style="background:#7b5ea7"></i>${escape(markName)} (세로선)</span>`:''}</div>`;
}

function kpi(label, value, unit, note, color='') {
  return `<div class="kpi"><div class="kpi-label">${escape(label)}</div><div class="kpi-value ${color}">${value}<small>${escape(unit)}</small></div><div class="kpi-note">${note}</div></div>`;
}

// Gate = WAPE ≤ 6 · RMSE/평균가 ≤ 8 · 방향 정확도 ≥ 60 (rules.json). Older records only carry WAPE.
const OPS = {'<=':'≤', '>=':'≥', '<':'<'};
const gateText = g => !g ? '—' : g.checks ? g.checks.map(c => `${c.label} ${pct(c.value, 1)}${OPS[c.op]}${c.limit}${c.passed ? '' : ' ✗'}`).join(' · ') : `WAPE ${pct(g.value)} ≤ ${g.max_percent}%`;
const check = (g, name) => g?.checks?.find(c => c.name === name)?.value;

const GATE_HEADERS = ['WAPE (파인튜닝은 최근 13주)', 'RMSE/평균가', '방향 정확도', '운영 모델 대비'];
// vs_champion: same validation weeks scored by the model that was serving when this one was trained.
const champ = g => { const c = g?.checks?.find(x => x.name === 'vs_champion'); if (!c) return '첫 모델'; if (c.applicable === false) return `${badge('비교 불가', 'amber')} 새 주 ${c.weeks}주`; const strict = c.op === '<'; return `${badge(c.passed ? (strict ? '더 나음' : '같거나 나음') : (strict ? '나아지지 않음' : '더 나쁨'), c.passed ? 'green' : 'red')} ${escape(c.champion)} ${pct(c.champion_value)}`; };
const gateCells = g => [pct(g?.value), pct(check(g, 'rmse_pct')), pct(check(g, 'direction'), 1), champ(g)];
const trainedFrom = base => base ? `파인튜닝 ← ${escape(base)}` : '처음부터';

function activeModel(d) { return d.models.models.find(m => m.version === d.models.active); }

function pipeline(d) {
  const latest = d.models.models.at(-1), active = activeModel(d), jobs = d.jobs?.jobs || [];
  const running = kind => jobs.some(j => j.kind === kind && ['queued','running'].includes(j.status));
  const openPerf = d.alerts.some(a => a.kind === 'performance' && a.status === 'open');
  const lastRetrain = jobs.find(j => j.kind === 'retraining');
  const stages = [
    ['데이터', '⛁', 'done', `${d.dataset.quality.rows}주 · ${d.dataset.rows[0].unit}`],
    ['학습', '◎', running('training') ? 'active' : latest ? 'done' : '', latest ? `${latest.epochs_run} epoch` : '미실행'],
    ['게이트', '✓', !latest ? '' : latest.gate?.passed ? 'done' : 'error', latest?.gate ? (latest.gate.checks ? `조건 ${latest.gate.checks.filter(c => c.passed).length}/${latest.gate.checks.length} 통과` : gateText(latest.gate)) : '—'],
    ['배포', '⇪', active ? 'done' : '', active ? `자동 · MLflow @production` : '미배포'],
    ['감시', '◉', !active ? '' : openPerf ? 'error' : 'done', openPerf ? '성능 경보' : active ? 'WAPE·분포 감시' : '—'],
    ['재학습', '↻', running('retraining') ? 'active' : lastRetrain ? (lastRetrain.status === 'succeeded' ? 'done' : 'error') : '', lastRetrain ? JOB[lastRetrain.status][0] : '대기'],
  ];
  return `<div class="pipeline">${stages.map(([name, icon, cls, note]) => `<div class="stage ${cls}"><div class="stage-icon">${icon}</div>${name}<small>${escape(note)}</small></div>`).join('')}</div>`;
}

function alertList(alerts, limit=6) {
  if (!alerts.length) return empty('경보 없음');
  return alerts.slice(0, limit).map(a => `<div class="alert-row">${badge(a.status === 'open' ? (a.severity === 'error' ? '오류' : '경보') : '해제', a.status === 'open' ? (a.severity === 'error' ? 'red' : 'amber') : 'green')}<div><p>${escape(a.message)}</p><small>${escape(a.kind)} · ${escape(day(a.at))}</small></div></div>`).join('');
}

// Course Day3 serving metrics + model error + drift score (the example dashboard's five cards).
function service(d) {
  const s = d.metrics.service, m = d.metrics, rule = d.system?.service || {max_mean_latency_ms:500, min_requests:20};
  const n = s.request_count.value, slow = n >= rule.min_requests && s.latency_mean.value > rule.max_mean_latency_ms;
  const active = activeModel(d), op = m.model.wape, live = op.value !== null && op.value !== undefined && op.n >= 13;
  const error = live ? op.value : active?.gate?.value, ks = m.drift.ks;
  const series = d.series || [], labels = series.map(x => new Date(x.minute*1000).toLocaleTimeString('ko-KR', {hour:'2-digit', minute:'2-digit', hour12:false}));
  const kpis = `<div class="kpis five">${
    kpi('요청 수', num(n, 0), '건', `최근 ${WINDOWS[state.window]} 업무 요청 · ${num(s.rps.value, 3)} req/s`)}${
    kpi('평균 응답시간', num(s.latency_mean.value, 0), 'ms', `p95 ${num(s.latency_p95.value, 0)}ms · 경계 ${rule.max_mean_latency_ms}ms`, slow ? 'bad' : '')}${
    kpi('성공률', num(s.success_rate.value, 1), '%', `2xx 응답 비율`)}${
    kpi('모델 오차 (WAPE)', num(error, 2), '%', live ? `최근 정답 확정 ${op.n}건 · 경계 ${num(op.threshold, 2)}%` : `${escape(d.models.active || '—')} ${active?.training_mode === 'finetune_recent' ? '파인튜닝 판정 13주' : '검증 구간'} 기준 (운영 정답 확정 전)`, live && op.threshold && op.value > op.threshold ? 'bad' : '')}${
    kpi('드리프트 점수 (KS)', num(ks.value, 2), '', ks.threshold ? `주간 수익률 분포 · 경계 ${num(ks.threshold, 2)}` : '보정 전', ks.status === 'exceeded' ? 'bad' : '')}</div>`;
  const charts = `<div class="grid-two"><div>${chart([
    {name:'분당 평균 응답시간 (ms)', values:series.map(x => x.latency_mean ?? NaN), dots:true, width:1.6},
    {name:`경계 ${rule.max_mean_latency_ms}ms`, values:series.map(() => rule.max_mean_latency_ms), color:'var(--red)', dash:true, width:1.2}], {height:150, labels})}</div><div>${chart([
    {name:'분당 요청 수', values:series.map(x => x.requests)}], {height:150, labels})}</div></div>`;
  const toggle = `<div class="segmented">${Object.entries(WINDOWS).map(([w, label]) => `<button data-window="${w}" class="${Number(w) === state.window ? 'selected' : ''}">${label}</button>`).join('')}</div>`;
  return panel(`운영 지표 요약 · 최근 ${WINDOWS[state.window]}`, `${kpis}${charts}`, toggle);
}

// Retraining history like the course example: every registered version, newest first, with its stage.
function history(d, limit = 10) {
  const active = d.models.active, deployed = new Set(d.models.deployments.map(x => x.version));
  const models = [...d.models.models].reverse().slice(0, limit);
  if (!models.length) return empty('학습된 모델 없음');
  const mode = m => m.from_experiment || m.training_mode === 'finetune_recent' ? '<span class="chip">fine-tune</span>' : '<span class="chip">scratch</span>';
  const stage = m => m.version === active ? '<span class="stage-dot green">Production</span>' : !m.gate?.passed ? '<span class="stage-dot red">Rejected</span>'
    : deployed.has(m.version) ? '<span class="stage-dot">Archived</span>' : '<span class="stage-dot amber">Staging</span>';
  return table(['버전', '등록 시각', '모드', '오차 (WAPE)', '스테이지', ''], models.map(m => [
    `<b>${escape(m.version)}</b>`, escape(new Date(m.at).toLocaleString('ko-KR', {month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit'})),
    `${mode(m)}${m.from_experiment ? ` <small class="muted">${escape(KIND_LABEL[m.experiment_kind] || m.experiment_kind)}</small>` : ''}`,
    `${pct(m.gate?.value)} <small class="muted">${m.training_mode === 'finetune_recent' || m.from_experiment ? '최근 13주' : '검증'}</small>`, stage(m),
    m.version !== active && m.gate?.passed ? `<button data-select="${escape(m.version)}" class="secondary">운영 선택</button>` : '']));
}

function dashboard(d) {
  const active = activeModel(d), m = d.metrics, last = d.predictions.at(-1), rows = d.dataset.rows.slice(-104), unit = rows[0].unit;
  const prices = rows.map(r => r.midpoint), forecast = last && last.version === d.models.active ? last.prediction : null;
  const priceChart = chart([
    {name:`주간 버터 가격 (${unit})`, values:[...prices, NaN]},
    {name:'4주 뒤 예측', values:[...prices.map((_, i) => i === prices.length-1 ? prices.at(-1) : NaN), forecast ?? NaN], color:'var(--amber)', dash:true, dots:true},
  ], {labels:[...rows.map(r => r.date), forecast ? day(last.target_date) : '']});
  const right = `${panel('운영 모델', active ? `<div class="model-identity"><div class="model-icon">◆</div><div><strong>${escape(active.version)}</strong>${badge('production','blue')} ${d.health.model_loaded ? badge('로딩됨','green') : badge(`${d.health.loading_mode} · 미로딩`,'amber')}</div></div>${definition('게이트', active.gate?.passed ? badge('통과 · 자동 배포', 'green') : badge('미달', 'red'))}${definition('학습 방식', active.from_experiment ? `실험 모델 · 파인튜닝 ← 실험 내 ${escape(active.warm_start_from)}` : trainedFrom(active.warm_start_from))}${definition('학습 데이터 끝', escape(day(active.validation_end)))}` : empty('운영 모델 없음', '모델 탭에서 학습하면 게이트 통과 시 자동 배포됩니다.'))}${panel('최근 경보', alertList(d.alerts))}`;
  const kr = forecast ? last.korea : null;
  return `<div class="kpis three">${
    kpi('4주 뒤 예측가', `<span class="${state.flash ? 'flash' : ''}">${price(forecast)}</span>`, unit, forecast ? `목표일 ${escape(day(last.target_date))}` : '예측 실행 전')}${
    kpi('한국 수입원가 추정', kr ? `<span class="${state.flash ? 'flash' : ''}">${num(kr.krw_per_kg, 0)}</span>` : '—', '원/kg', kr ? `최근 실측 ${escape(kr.korea_last_month)} ${num(kr.korea_last_krw_per_kg, 0)}원 · EU 변화의 ${num(kr.passthrough_beta*100, 0)}% 반영 · 약 ${kr.lag_months}개월 뒤` : 'EU 가격(EUR/100kg) 데이터일 때만')}${
    kpi('현재가', price(prices.at(-1)), unit, `${escape(rows.at(-1).date)} 관측`)}${
    ''}</div>
    ${service(d)}
    ${panel('AIOps 파이프라인', pipeline(d), (() => { const j = (d.jobs?.jobs || []).find(x => x.ended_ts || x.started_ts); return j ? badge(`마지막 작업 ${ago(j.ended_ts || j.started_ts)} · ${JOB[j.status]?.[0] || j.status}`) : ''; })())}
    ${panel('재학습 이력', `<p class="note">MLflow Model Registry(ButterCast_Predictor)에 등록된 버전을 그대로 보여줍니다. 실험에서 만든 모델도 여기 등록됩니다.</p>${history(d)}`, `<a href="#model">전체 보기</a>`)}
    <div class="grid-main"><div>${panel('가격과 예측', `${forecast ? `<div class="forecast-line ${state.flash ? 'flash' : ''}">${escape(day(last.target_date))} 예측 금액 : <b>${price(forecast)}</b> ${escape(unit)}${kr ? ` <span class="muted">→ 한국 수입원가 약 ${num(kr.krw_per_kg, 0)}원/kg (원/유로 ${num(kr.eurkrw, 0)}, ${escape(kr.fx_date)})</span>` : ''}</div>` : ''}${priceChart}`, `<button data-action="predict" ${active ? '' : 'disabled'}>예측 실행</button>`)}</div><div>${right}</div></div>`;
}

function model(d) {
  const models = [...d.models.models].reverse(), latest = models[0], active = d.models.active;
  const deployed = new Set(d.models.deployments.map(x => x.version));
  // Promotion is automatic on gate pass; the only manual action left is rolling back to a previously served model.
  // The newest gate-passed model is promoted automatically; any other gate-passed one can be chosen here.
  const candidates = models.length ? table(['모델', '출처', '학습 방식', ...GATE_HEADERS, '게이트', ''], models.map(m => [
    `${escape(m.version)} ${m.version === active ? badge('운영','blue') : ''}`,
    m.from_experiment ? badge(`실험 · ${KIND_LABEL[m.experiment_kind] || m.experiment_kind}`, 'amber') : '직접 학습',
    m.from_experiment ? `파인튜닝 ← 실험 내 ${escape(m.warm_start_from)}` : trainedFrom(m.warm_start_from),  // base lives in the experiment, not here
    ...gateCells(m.gate),
    m.gate ? badge(m.gate.passed ? '통과 · 자동 배포' : '탈락', m.gate.passed ? 'green' : 'red') : badge('구버전'),
    m.version !== active && m.gate?.passed ? `<button data-select="${escape(m.version)}" class="secondary">${deployed.has(m.version) ? '다시 운영' : '운영 선택'}</button>` : ''])) : empty('학습된 모델 없음');
  const preds = d.predictions.length ? table(['발행', '목표일', '모델', '예측가'], d.predictions.slice(-8).reverse().map(p => [escape(day(p.issued_at)), escape(p.target_date), escape(p.version), `<b>${price(p.prediction)}</b>`])) : empty('예측 기록 없음');
  return `${panel('모델 (LSTM)', `<p class="note">게이트: 검증 WAPE ≤ 6% · RMSE/평균가 ≤ 8% · 방향 정확도 ≥ 60%, 그리고 같은 검증 구간에서 운영 모델보다 나쁘지 않아야 자동 배포. 교체 직후 실제 서빙 경로로 한 번 더 확인하고, 이상하면 이전 모델로 자동 롤백. 드리프트 실험에서 파인튜닝·승격된 모델도 여기로 들어오고(실험 표시), 게이트를 통과한 모델은 언제든 운영으로 고를 수 있습니다.</p>${candidates}`, `<button data-action="train" ${state.job ? 'disabled' : ''}>학습 실행</button>`)}
    ${panel('예측 기록', preds, `<button data-action="predict" ${active ? '' : 'disabled'}>예측 실행</button>`)}`;
}

// Initial model + every retrain. Retraining warm-starts from whatever was serving, so the base is the last promoted one.
function experimentModels(r) {
  // Every fine-tune is registered into the main store in order, as r.adopted_models.
  let base = r.initial_model, adopted = [...(r.adopted_models || [])];
  const rows = [[`${escape(r.initial_model)}`, escape(r.initial_model), '실험 시작 시 운영 모델', '—', '정상', ...gateCells(r.initial_gate), badge('시작점', 'blue')]];
  r.retraining.forEach(t => {
    const main = adopted.shift();  // every retrain is registered, passed or not
    rows.push([escape(t.version), main ? `<b>${escape(main)}</b>` : '—', trainedFrom(base), escape(t.date), escape(PHASE[t.phase] || t.phase), ...gateCells(t.gate), badge(t.promoted ? '승격' : '게이트 탈락 · 등록만', t.promoted ? 'green' : 'red')]);
    if (t.promoted) base = t.version;
  });
  return table(['실험 내 모델', '모델 탭 이름', '학습 방식', '감지일', '구간', ...GATE_HEADERS, '결과'], rows);
}
const PHASE = {normal:'정상', changed:'주입', recovery:'복귀'};

const RULE = {retrain: 8};

function driftResult(r) {
  if (!r) return empty('완료된 실험 없음', '상황을 고르고 실행하세요. 학습·파인튜닝이 실제로 돌아 10~20초 걸립니다.');
  const steps = r.steps, retrainIdx = r.retraining.map(t => steps.findIndex(s => s.date === t.date)).filter(i => i >= 0);
  const b = r.business, cost = k => num(b[k].total_cost, 0);
  const [label, color] = LEVEL[r.observed_level], matched = r.observed_level === r.expected_level;
  const summary = `<div class="kpis four">${
    kpi('최고 경보 단계', `<span class="${matched ? '' : 'bad'}">${escape(label)}</span>`, '', `기대 ${escape(LEVEL[r.expected_level][0])} · ${matched ? '일치' : '불일치'}`)}${
    kpi('감지 지연', r.detection_delay_observations ?? '—', '주', r.kind === 'none' ? '주입 없음' : '주입 시작 → 첫 경보')}${
    kpi('파인튜닝', r.retraining.length, '회', `승격 ${r.retraining.filter(t => t.promoted).length}회`)}${
    kpi('운영 WAPE', `${num(r.scores.fixed.wape.value, 2)} → ${num(r.scores.adaptive.wape.value, 2)}`, '%', '고정 모델 → 재학습 운영')}${
    ''}</div>`;
  const weeks = r.level_weeks, levelTable = table(['구간', '0단계 (일반)', '1단계 (감시)', '2단계 (파인튜닝)'], [['정상 13주', 'normal'], ['주입 26주', 'changed'], ['복귀', 'recovery']].map(([name, k]) => [name, ...weeks[k].map(n => `${n}주`)]));
  return `${summary}
    ${panel(`가상 미래 가격 (${escape(r.steps[0].date)}~) · ${escape(KIND_LABEL[r.kind] || r.kind)}`, chart([
      {name:'입력 가격 (주입 포함)', values:steps.map(s => s.price)},
      {name:'원본 가격', values:steps.map(s => s.original_price), color:'var(--muted)', dash:true, width:1.6},
      {name:'재학습 운영 예측', values:steps.map(s => s.adaptive), color:'var(--amber)', width:1.8}], {labels:steps.map(s => s.date), marks:retrainIdx, markName:'파인튜닝'}))}
    <div class="grid-two">
      ${panel('주별 판정', `${chart([{name:'경보 단계 (0 일반 · 1 감시 · 2 파인튜닝)', values:steps.map(s => s.level), color:`var(--${color})`, dots:true, width:1.4}], {height:150, labels:steps.map(s => s.date), marks:retrainIdx, markName:'파인튜닝'})}${levelTable}`)}
      ${panel('판정 기준', `${definition('1단계 (감시)', '입력 분포 경보(2회 연속) 또는 13건 WAPE &gt; 경계 2회 연속')}${definition('2단계 (파인튜닝)', `13건 WAPE &gt; 경계가 ${RULE.retrain}회 연속`)}${definition('파인튜닝 후', '게이트 3개 통과 시 자동 승격')}<p class="note">일회성 충격은 몇 주 뒤 경계 안으로 돌아와 감시에서 멈추고, 체제 변화는 오차가 계속 남아 파인튜닝까지 갑니다.</p>`)}
    </div>
    ${panel('실험 모델 (경보 → 파인튜닝 → 게이트)', experimentModels(r))}
    <div class="grid-two">
      ${panel('운영 WAPE와 경계', chart([
        {name:'최근 13건 WAPE', values:steps.map(s => s.wape?.value ?? NaN)},
        {name:'경고 경계 (검증 95% 분위, 모델마다 다름)', values:steps.map(s => s.wape?.threshold ?? NaN), color:'var(--amber)', dash:true}], {height:190, labels:steps.map(s => s.date), marks:retrainIdx, markName:'파인튜닝'}))}
      ${panel('운영 점검', `${definition('주입 전 오경보', `${r.normal_alarm_observations} / ${r.normal_observations}주`)}${definition('같은 트리거 중복 학습', r.duplicate_training_verified ? '차단 확인' : '해당 없음')}${definition('롤백', r.rolled_back_model === r.initial_model ? `${escape(r.initial_model)} 복귀` : '실패')}${definition('손상 모델 배포', r.corruption_blocked ? '차단 확인' : '확인 안 됨')}`)}
    </div>
    ${panel('구매비 비교 (합성 업무 규칙)', table(['정책', '총 구매비', '선구매', '현물 대비'], [['현물 구매', cost('spot'), '—', '—'], ['고정 모델', cost('fixed_model'), b.fixed_model.prebuys, pct(b.fixed_model.saving_vs_spot_pct)], ['재학습 운영', cost('adaptive_model'), b.adaptive_model.prebuys, pct(b.adaptive_model.saving_vs_spot_pct)]]))}
    ${panel('실험 실행 로그', logView(r.aiops_log))}`;
}

// Direct predict (course Day1 simulation): raw JSON to /api/predict, shows the real status code — 200 or 422.
function samplePrices(d, noise = false) {
  const last = d.dataset.rows.slice(-6).map(r => r.midpoint);
  return JSON.stringify({prices: last.map(p => Math.round((noise ? p*(1+(Math.random()-.5)*.06) : p)*100)/100)}, null, 2);
}
function direct(d) {
  const text = state.predictJson ?? samplePrices(d), r = state.predictResult;
  const result = r ? `<div class="toolbar">${badge(`HTTP ${r.status}`, r.status === 200 ? 'green' : 'red')}${r.status === 200 && r.body.korea ? badge(`한국 수입원가 약 ${num(r.body.korea.krw_per_kg, 0)}원/kg`, 'blue') : ''}</div><pre>${escape(JSON.stringify(r.body, null, 2))}</pre>` : '';
  return panel('직접 예측 · POST /api/predict', `<p>최근 6주 가격(EUR/100kg)을 JSON으로 보냅니다. 값을 0이나 음수로 바꾸거나 개수를 줄이면 422 검증 오류가 납니다. 이렇게 보낸 예측은 운영 기록에 남지 않습니다.</p>
    <textarea id="predict-json" rows="8" spellcheck="false">${escape(text)}</textarea>
    <div class="toolbar"><button class="secondary" data-action="sample">최근 실제 6주</button><button class="secondary" data-action="sample-noise">예시 데이터 다시 생성</button><button class="secondary" data-action="sample-bad">422 예시</button><button data-action="predict-json">예측 실행</button></div>${result}`);
}

function drift(d) {
  RULE.retrain = d.system?.rules?.monitoring?.retrain_consecutive ?? RULE.retrain;
  const results = (d.experiments || []).filter(r => r.level_weeks), current = results.find(r => r.id === state.experiment) || results.at(-1);
  const running = state.job || (d.jobs?.jobs || []).some(j => j.kind === 'experiment' && ['queued', 'running'].includes(j.status));
  const history = results.length > 1 ? `<label class="inline">지난 결과 보기<select id="experiment-pick">${[...results].reverse().map(r => `<option value="${escape(r.id)}" ${current?.id === r.id ? 'selected' : ''}>${escape(day(r.at))} · ${escape(KIND_LABEL[r.kind] || r.kind)} · ${escape(r.id)}</option>`).join('')}</select></label>` : '';
  const buttons = Object.entries(KIND_LABEL).map(([kind, label]) => `<button data-action="experiment" data-kind="${kind}" class="${kind === 'none' ? 'secondary' : kind === 'variance' ? 'danger' : ''}" ${running || !d.models?.active ? 'disabled' : ''} title="${escape(KIND_NOTE[kind])}">${escape(label)}</button>`).join('');
  return `${direct(d)}${panel('드리프트 실험', `<p><b>지금 운영 중인 모델</b>에 실제 마지막 주 다음의 가상 미래 65주(정상 13 → 상황 26 → 복귀 26)를 한 주씩 흘려보냅니다. 운영과 같은 규칙으로 감시하고, 필요하면 실제로 파인튜닝해 게이트를 통과한 모델을 운영으로 올립니다. 실험에서 학습한 모델은 끝나면 모두 재학습 이력에 등록됩니다. 가상 가격은 실제 데이터와 섞이지 않게 따로 보관합니다.</p>
    <div class="toolbar">${buttons}${running ? badge('실험 실행 중… 10~20초', 'amber') : ''}${history}</div>
    <p class="note">${Object.entries(KIND_LABEL).map(([k, v]) => `<b>${escape(v)}</b>: ${escape(KIND_NOTE[k])}`).join(' · ')}</p>`)}
    ${driftResult(current)}`;
}

function logView(lines) {
  if (!lines?.length) return empty('로그 없음');
  const level = line => (line.match(/\[(WARN|ERROR|FAIL|ROLLBACK|OK|GATE PASSED|GATE FAILED|INFO)\]/) || [])[1] || 'INFO';
  const color = {WARN:'amber', ERROR:'red', FAIL:'red', ROLLBACK:'red', 'GATE FAILED':'red', OK:'green', 'GATE PASSED':'green'};
  return `<div class="log">${lines.slice(-200).reverse().map(line => `<div class="log-line ${color[level(line)] || ''}"><time>${escape(line.slice(11, 19))}</time>${escape(line.slice(33))}</div>`).join('')}</div>`;
}

function ops(d) {
  const jobs = d.jobs.jobs;
  return `<div class="grid-main"><div>${panel('aiops.log', logView(d.logs), badge('WARN → INFO → GATE → OK', 'blue'))}</div><div>
    ${panel('데이터 업로드', `<form id="import-form"><label>주간 가격 CSV 업로드 (week_ending, butter_price_usd_lb)<input type="file" name="file" accept=".csv" required></label><button type="submit">검증 후 적재</button></form>`)}
    ${panel('작업', jobs.length ? table(['작업', '종류', '상태', '소요'], jobs.slice(0, 10).map(j => [escape(j.id), escape(j.kind), badge(...(JOB[j.status] || [j.status])), j.ended_ts && j.started_ts ? `${num(j.ended_ts-j.started_ts, 1)}초` : '—'])) : empty('작업 없음'))}
  </div></div>`;
}

// Every number here is read from the running server (/api/system → rules.json), not typed into the page.
function system(d) {
  const s = d.system, r = s.rules, g = r.gate, f = g.finetune, m = r.monitoring, sv = r.service, pd = r.post_deploy, k = r.korea;
  const card = (title, rows, reason) => panel(title, `${rows.map(([n, v]) => definition(n, v)).join('')}${reason ? `<p class="note">${escape(reason)}</p>` : ''}`);
  return `<div class="grid-two">
    ${card('처음 학습 게이트', [['검증 WAPE', `≤ ${g.max_wape_percent}%`], ['RMSE ÷ 검증 평균가', `≤ ${g.max_rmse_percent}%`], ['4주 방향 정확도', `≥ ${g.min_direction_percent}%`], ['운영 모델 대비 WAPE', `≤ ${g.max_wape_vs_champion}배`]], g.reason)}
    ${card('파인튜닝 게이트 (경보로 시작한 재학습)', [['학습 창', `최근 ${f.window_weeks}주`], ['판정 구간', `학습에 안 쓴 최근 ${f.holdout_weeks}주`], ['학습', `${f.epochs} epoch · lr ${f.learning_rate}`], ['운영 모델 대비', 'WAPE가 더 낮아야 (같으면 탈락)'], ['상한선', `WAPE ≤ ${f.max_wape_percent}%`]], f.reason)}
    ${card('감시 단계', [['1단계 (감시)', `입력 분포 경보 또는 성능 조건 ${m.warn_consecutive}회 연속`], ['2단계 (파인튜닝)', `성능 조건 ${m.retrain_consecutive}회 연속`], ['재학습 간격', '91일 쿨다운 · 같은 트리거 중복 차단']], m.reason)}
    ${card('서비스 지표 경보', [['집계 창', `${sv.window_seconds/60}분`], ['평균 지연시간', `> ${sv.max_mean_latency_ms}ms`], ['에러율 (5xx)', `> ${sv.max_error_rate_pct}%`], ['판정 최소 요청', `${sv.min_requests}건`]], sv.reason)}
    ${card('교체 직후 확인 → 자동 롤백', [['추론 지연', `≤ ${pd.max_latency_ms}ms`], ['직전 모델 대비 예측 변화', `≤ ${pd.max_change_pct}%`]], pd.reason)}
    ${card('한국 수입원가 환산', [['EU 변화 반영 비율 β', k.passthrough_beta], ['반영 시차', `약 ${k.lag_months}개월`]], k.reason)}
    ${card('실행 환경', [['모델', `LSTM · 입력 ${s.sequence}주 → ${s.horizon_days}일 뒤`], ['로딩', s.loading_mode], ['작업 실행', s.job_runner || '서버 내 작업 스레드'], ['저장소', s.storage], ['규칙 버전', r.version]])}
  </div>`;
}

const PAGES = {
  dashboard: ['대시보드', 'OVERVIEW', '4주 뒤 버터 가격 예측과 운영 상태', dashboard, ['metrics', 'models', 'alerts', 'health', 'dataset', 'predictions', 'jobs', 'system', 'series']],
  model: ['모델', 'MLOPS', `학습 → 게이트(검증 WAPE) 통과 시 자동 배포 → MLflow @production`, model, ['models', 'predictions', 'health']],
  drift: ['시뮬레이션', 'SIMULATION', '직접 예측(200·422) · 정상 입력 / 이상 입력(파인튜닝 X) / 이상 입력(파인튜닝)', drift, ['experiments', 'jobs', 'system', 'dataset', 'models']],
  system: ['시스템', 'SYSTEM', '서버가 실제로 쓰는 규칙과 설정 (/api/system · rules.json)', system, ['system']],
  ops: ['로그·데이터', 'OPERATIONS', '운영 로그와 데이터 적재', ops, ['logs', 'jobs']],
};
const SOURCES = {metrics:() => `/metrics?window=${state.window}`, models:'/models', alerts:'/alerts', health:'/health', dataset:'/datasets', predictions:'/predictions', jobs:'/jobs', experiments:'/experiments', logs:'/logs', system:'/system', series:() => `/metrics/series?minutes=${Math.min(state.window/60, 1440)}`};

function render() {
  const [title, eyebrow, description, view] = PAGES[state.tab];
  $('title').textContent = title; $('eyebrow').textContent = eyebrow; $('description').textContent = description;
  document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('active', b.dataset.tab === state.tab); b.setAttribute('aria-current', b.dataset.tab === state.tab ? 'page' : 'false'); });
  $('content').innerHTML = view(state.data);
}

async function refresh() {
  try {
    const names = [...new Set(['health', ...PAGES[state.tab][4]])];
    const results = await Promise.all(names.map(n => api(typeof SOURCES[n] === 'function' ? SOURCES[n]() : SOURCES[n])));
    names.forEach((n, i) => state.data[n] = results[i]);
    const h = state.data.health;
    $('connection').hidden = true;
    $('status-text').textContent = h.active_model ? `운영 ${h.active_model}` : '운영 모델 없음';
    $('status-dot').className = `dot ${h.status === 'running' ? '' : 'off'}`;
    $('updated').textContent = `갱신 ${new Date().toLocaleTimeString('ko-KR', {hour12:false})}`;
    if (!document.querySelector('#content select:focus, #content input:focus, #content textarea:focus')) render();
  } catch (error) { $('connection').textContent = `연결 실패 · ${error.message}`; $('connection').hidden = false; }
}

async function waitJob(id, done) {
  state.job = id; render();
  while (true) {
    await new Promise(r => setTimeout(r, 1000));
    const job = await api(`/jobs/${id}`);
    if (['succeeded', 'failed', 'cancelled'].includes(job.status)) {
      state.job = null;
      toast(job.status === 'succeeded' ? done(job) : `실패: ${job.error}`);
      await refresh();
      return;
    }
  }
}

document.addEventListener('click', async event => {
  const b = event.target.closest('button'); if (!b) return;
  if (b.dataset.tab) { location.hash = b.dataset.tab; return; }
  if (b.id === 'refresh') { await refresh(); return; }
  if (b.dataset.window) { state.window = Number(b.dataset.window); await refresh(); return; }
  if (['sample', 'sample-noise', 'sample-bad'].includes(b.dataset.action)) {
    state.predictJson = b.dataset.action === 'sample-bad' ? JSON.stringify({prices: [430.5, 0, 431.2, -5, 428.9]}, null, 2) : samplePrices(state.data, b.dataset.action === 'sample-noise');
    state.predictResult = null; render(); return;
  }
  if (!b.dataset.action && !b.dataset.select) return;
  b.disabled = true;
  try {
    if (b.dataset.action === 'train') { const job = await post('/train', {epochs:40}); await waitJob(job.id, j => `${j.result.version} ${j.result.promoted ? '게이트 통과 · 자동 배포됨' : '게이트 탈락 · 기존 모델 유지'} (${gateText(j.result.gate)})`); }
    if (b.dataset.action === 'predict-json') {
      // Raw fetch: the point is to show the real status code, 422 included.
      const text = $('predict-json').value; state.predictJson = text;
      let body; try { body = JSON.parse(text); } catch (error) { state.predictResult = {status: 'JSON 오류', body: {detail: error.message}}; render(); return; }
      const response = await fetch('/api/predict', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      state.predictResult = {status: response.status, body: await response.json()}; render();
    }
    if (b.dataset.action === 'experiment') { state.kind = b.dataset.kind; const job = await post('/experiments', {kind:state.kind}); state.experiment = job.id; await waitJob(job.id, j => j.result.adopted_models.length ? `실험 완료 · ${j.result.adopted_models.join(', ')} 재학습 이력에 등록` : '실험 완료 · 파인튜닝 없음 (운영 모델 그대로)'); }
    // Blink the forecast (0.5 s) so a re-run with the same value still visibly refreshes.
    if (b.dataset.action === 'predict') { await post('/predict'); state.flash = true; await refresh(); state.flash = false; }
    if (b.dataset.select) { const r = await post(`/models/${b.dataset.select}/select`); toast(`${r.active} 운영 반영 · MLflow v${r.registry.version} @production · 대시보드에서 예측 실행`); await refresh(); }
  } catch (error) { toast(error.message); } finally { b.disabled = false; }
});
document.addEventListener('change', event => {
  if (event.target.id === 'experiment-pick') { state.experiment = event.target.value; render(); }
});
document.addEventListener('input', event => { if (event.target.id === 'predict-json') state.predictJson = event.target.value; });
document.addEventListener('submit', async event => {
  if (event.target.id !== 'import-form') return;
  event.preventDefault();
  const b = event.target.querySelector('button'); b.disabled = true;
  try { const r = await api('/datasets/import', {method:'POST', body:new FormData(event.target)}); toast(`${r.rows}주 적재 완료`); await refresh(); }
  catch (error) { toast(`적재 실패: ${error.message}`); } finally { b.disabled = false; }
});
window.addEventListener('hashchange', () => { state.tab = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard'; refresh(); });
state.tab = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : 'dashboard';
const tick = () => { $('clock').textContent = new Date().toLocaleTimeString('ko-KR', {hour12:false}); };
tick(); refresh();
setInterval(() => { tick(); if (!document.hidden && !state.job) refresh(); }, 5000);
