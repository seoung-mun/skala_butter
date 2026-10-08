/* All values come from the local API; nothing on this page is a placeholder number. */
const state = {simMode: 'once', stream: {pattern: 'variance', strength: 3, weeks: 39, text: ''}, tab: 'service', data: {}, job: null, kind: 'variance', window: 300, predictJson: null, predictResult: null};
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
const EXPERIMENT_LABEL = {...KIND_LABEL, custom:'직접 입력 가격'};
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
function chart(series, {height=220, labels=[], marks=[], markName='재학습', bands=[], bandName='이상 입력 구간'}={}) {
  const all = series.flatMap(s => s.values).filter(Number.isFinite);
  if (!all.length) return empty('표시할 값이 없습니다');
  const width = 760, left = 54, right = 14, top = 12, bottom = 28, n = Math.max(...series.map(s => s.values.length));
  const min = Math.min(...all), max = Math.max(...all), span = max-min || Math.abs(max)*.05 || 1;
  const low = min >= 0 ? Math.max(0, min-span*.1) : min-span*.1, high = max+span*.1;
  const x = i => left+i*(width-left-right)/Math.max(n-1, 1), y = v => top+(high-v)/(high-low)*(height-top-bottom);
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${escape(series.map(s=>s.name).join(', '))}">`;
  for (let i = 0; i < 4; i++) { const v = low+(high-low)*i/3; svg += `<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" stroke="var(--grid)"/><text x="${left-8}" y="${y(v)+4}" text-anchor="end">${num(v, span < 10 ? 2 : 0)}</text>`; }
  bands.forEach(([from, to]) => { svg += `<rect x="${x(from)}" y="${top}" width="${Math.max(2, x(to)-x(from))}" height="${height-top-bottom}" fill="#c0504d" opacity=".09"/>`; });
  // Event marks (fine-tune moments) are solid purple verticals so they never read as a threshold line.
  marks.forEach(i => { svg += `<line x1="${x(i)}" y1="${top}" x2="${x(i)}" y2="${height-bottom}" stroke="#7b5ea7" stroke-width="2" opacity=".8"/>`; });
  series.forEach(s => {
    const points = s.values.map((v, i) => Number.isFinite(v) ? `${x(i)},${y(v)}` : null).filter(Boolean);
    if (s.line !== false) svg += `<polyline points="${points.join(' ')}" fill="none" stroke="${s.color||'var(--blue)'}" stroke-width="${s.width||2.4}" ${s.dash?'stroke-dasharray="5 5"':''} stroke-linejoin="round"/>`;
    s.values.forEach((v, i) => { if (s.dots && Number.isFinite(v)) svg += `<circle cx="${x(i)}" cy="${y(v)}" r="${s.r||4}" fill="${s.hollow ? 'var(--paper)' : s.color||'var(--blue)'}" stroke="${s.color||'var(--blue)'}" stroke-width="${s.hollow ? 2 : 0}"/>`; });
  });
  if (labels.length) [0, Math.floor((labels.length-1)/2), labels.length-1].forEach((index, i) => { svg += `<text x="${x(index)}" y="${height-6}" text-anchor="${['start','middle','end'][i]}">${escape(labels[index])}</text>`; });
  return svg+'</svg>'+`<div class="legend">${series.map(s=>`<span><i style="background:${s.hollow ? 'var(--paper)' : s.color||'var(--blue)'};border:2px solid ${s.color||'var(--blue)'}"></i>${escape(s.name)}</span>`).join('')}${marks.length?`<span><i style="background:#7b5ea7"></i>${escape(markName)} (세로선)</span>`:''}${bands.length?`<span><i style="background:#c0504d;opacity:.25;border-radius:2px"></i>${escape(bandName)} (음영)</span>`:''}</div>`;
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
  // Fine-tuning happens either in operation (retraining job) or inside an experiment; show whichever came last.
  const finished = kind => jobs.find(j => j.kind === kind && j.ended_ts);
  const retrain = finished('retraining'), experiment = jobs.find(j => j.kind === 'experiment' && j.status === 'succeeded' && j.result?.adopted_models?.length);
  const lastTune = [retrain, experiment].filter(Boolean).sort((a, b) => b.ended_ts-a.ended_ts)[0];
  let tune = ['', '대기'];
  if (running('retraining')) tune = ['active', '파인튜닝 중'];
  else if (running('experiment')) tune = ['active', '실험 실행 중'];
  else if (lastTune && lastTune === retrain) tune = [retrain.status === 'succeeded' ? 'done' : 'error', JOB[retrain.status][0]];
  else if (lastTune) {
    const version = lastTune.result.adopted_models.at(-1), passed = d.models.models.find(m => m.version === version)?.gate?.passed;
    tune = passed ? ['done', `실험 파인튜닝 ${version} 승격`] : ['error', '실험 파인튜닝 탈락 · 기존 유지'];
  }
  // A new training run resets every later stage to "not reached" until it finishes.
  const training = running('training'), wait = ['', '대기'];
  const stages = [
    ['데이터', '⛁', 'done', `${d.dataset.quality.rows}주 · ${d.dataset.rows[0].unit}`],
    ['학습', '◎', training ? 'active' : latest ? 'done' : '', training ? '학습 중' : latest ? `${latest.epochs_run} epoch` : '미실행'],
    ['게이트', '✓', ...(training ? wait : [!latest ? '' : latest.gate?.passed ? 'done' : 'error', latest?.gate ? (latest.gate.checks ? `조건 ${latest.gate.checks.filter(c => c.passed).length}/${latest.gate.checks.length} 통과` : gateText(latest.gate)) : '—'])],
    ['배포', '⇪', ...(training ? wait : [active ? 'done' : '', active ? `자동 · MLflow @production` : '미배포'])],
    ['감시', '◉', ...(training ? wait : [!active ? '' : openPerf ? 'error' : 'done', openPerf ? '성능 경보' : active ? 'WAPE·분포 감시' : '—'])],
    ['재학습', '↻', ...(training ? wait : tune)],
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
    `${mode(m)}${m.from_experiment ? ` <small class="muted">${escape(EXPERIMENT_LABEL[m.experiment_kind] || m.experiment_kind)}</small>` : ''}`,
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
    m.from_experiment ? badge(`실험 · ${EXPERIMENT_LABEL[m.experiment_kind] || m.experiment_kind}`, 'amber') : '직접 학습',
    m.from_experiment ? `파인튜닝 ← 실험 내 ${escape(m.warm_start_from)}` : trainedFrom(m.warm_start_from),  // base lives in the experiment, not here
    ...gateCells(m.gate),
    m.gate ? badge(m.gate.passed ? '통과 · 자동 배포' : '탈락', m.gate.passed ? 'green' : 'red') : badge('구버전'),
    m.version !== active && m.gate?.passed ? `<button data-select="${escape(m.version)}" class="secondary">${deployed.has(m.version) ? '다시 운영' : '운영 선택'}</button>` : ''])) : empty('학습된 모델 없음');
  const preds = d.predictions.length ? table(['발행', '목표일', '모델', '예측가'], d.predictions.slice(-8).reverse().map(p => [escape(day(p.issued_at)), escape(p.target_date), escape(p.version), `<b>${price(p.prediction)}</b>`])) : empty('예측 기록 없음');
  return `${panel('모델 (LSTM)', `<p class="note">게이트: 검증 WAPE ≤ 6% · RMSE/평균가 ≤ 8% · 방향 정확도 ≥ 60%, 그리고 같은 검증 구간에서 운영 모델보다 나쁘지 않아야 자동 배포. 교체 직후 실제 서빙 경로로 한 번 더 확인하고, 이상하면 이전 모델로 자동 롤백. 드리프트 실험에서 파인튜닝·승격된 모델도 여기로 들어오고(실험 표시), 게이트를 통과한 모델은 언제든 운영으로 고를 수 있습니다.</p>${candidates}`, `<button data-action="train" ${state.job ? 'disabled' : ''}>학습 실행</button>`)}
    ${panel('예측 기록', preds, `<button data-action="predict" ${active ? '' : 'disabled'}>예측 실행</button>`)}`;
}

// Purchase advice (서비스 탭): the serving model's forecast turned into buy / as usual / hold, with its track record.
const SIGNAL = {buy:['지금 구매', 'buy', '다음 4주 사용분을 지금 가격으로 미리 확보하세요.'],
  normal:['평소대로', 'normal', '필요한 만큼 평소처럼 구매하세요. 큰 움직임은 예상되지 않습니다.'],
  hold:['구매 보류', 'hold', '필요한 최소량만 사고 4주 뒤 다시 확인하세요. 가격 하락이 예상됩니다.']};
const signed = (v, d=1) => v === null || v === undefined ? '—' : `${v > 0 ? '+' : ''}${num(v, d)}`;
const weekBefore = iso => { const t = new Date(`${iso.slice(0, 10)}T00:00:00Z`); t.setUTCDate(t.getUTCDate()-7); return t.toISOString().slice(0, 10); };

function advisor(d) {
  const a = d.decision;
  if (!a || a.error) return empty('구매 판단을 아직 낼 수 없습니다', a?.error || '모델 탭에서 학습하면 게이트를 통과한 모델이 운영에 올라가고, 여기서 바로 판단이 나옵니다.');
  const [label, cls, action] = SIGNAL[a.signal], t = a.track_record, rule = a.rules;
  const changed = state.decisionVersion && state.decisionVersion !== a.version;
  if (changed) { state.decisionFlash = `${state.decisionVersion} → ${a.version}`; setTimeout(() => { state.decisionFlash = null; }, 4000); }
  state.decisionVersion = a.version;
  const krw = v => a.korea ? num(v/100*a.korea.eurkrw, 0) : '—';
  const same = t?.summary[a.signal];
  const evidence = !t ? '' : a.signal === 'normal'
    ? `과거 '평소대로' ${same.count}주 중 실제로 4주에 +${rule.missed_jump_pct}% 넘게 오른 주는 ${t.missed_jumps}주였습니다.`
    : `과거 같은 신호 <b>${same.count}번 중 ${same.hits}번</b> 맞았고, 실제로는 평균 <b>${signed(same.mean_actual_change_pct)}%</b> 움직였습니다.`;
  const banners = [
    a.from_experiment ? `<div class="banner warn">지금 운영 모델 ${escape(a.version)}은 시뮬레이션에서 가상 가격으로 학습한 모델입니다. 실제 구매 판단에는 정식 학습 모델을 쓰세요. ${t ? `<button class="secondary" data-select="${escape(t.model)}">${escape(t.model)}(으)로 되돌리기</button>` : ''}</div>` : '',
    a.caution.length ? `<div class="banner danger">최근 시장 움직임이 평소와 다릅니다 (${a.caution.map(c => escape(c.message)).join(' · ')}). 이 판단은 평소보다 덜 믿을 만합니다.</div>` : ''].join('');
  const hero = `<section class="decision ${cls} ${state.decisionFlash ? 'flash' : ''}">
    <div class="decision-main"><div class="decision-label">${label}</div><p>${action}</p></div>
    <div class="decision-facts">
      <div><span>4주 뒤 EU 버터</span><b>${signed(a.change_pct)}%</b><small>${price(a.current)} → ${price(a.forecast)} ${escape(a.unit)} · ${escape(a.target_date)}</small></div>
      ${a.korea ? `<div><span>한국 수입원가 추정</span><b>${num(a.korea_now.krw_per_kg, 0)} → ${num(a.korea.krw_per_kg, 0)}원/kg</b></div>` : ''}
      <div><span>판단 근거</span><b class="evidence">${evidence || '성적 계산용 모델 없음'}</b><small>기준: 예측 변화 ≥ +${rule.buy_change_pct}% 구매 · ≤ ${rule.hold_change_pct}% 보류</small></div>
    </div>
    <div class="decision-meta">운영 모델 <b>${escape(a.version)}</b>${state.decisionFlash ? ` · <b>${escape(state.decisionFlash)} 모델 교체로 판단 갱신</b>` : ''} · 최신 가격 ${escape(a.as_of)} · ${new Date().toLocaleTimeString('ko-KR', {hour12:false})} 갱신</div>
  </section>`;
  if (!t) return `${banners}${hero}`;
  // Chart: actual weekly price vs the forecast made 4 weeks earlier for that week, running on into the weeks whose
  // answer is not known yet. Dots mark past buy/hold signals at the week they were issued (filled = right).
  const hist = a.history, future = [], lastTarget = a.upcoming.at(-1).target_date;
  for (let d0 = new Date(`${hist.at(-1).date}T00:00:00Z`); ; ) { d0.setUTCDate(d0.getUTCDate()+7); const iso = d0.toISOString().slice(0, 10); if (iso > lastTarget) break; future.push(iso); }
  const dates = [...hist.map(h => h.date), ...future], index = new Map(dates.map((x, i) => [x, i])), blank = () => dates.map(() => NaN);
  const predicted = blank(); [...t.judged, ...a.upcoming].forEach(r => { const i = index.get(r.target_date); if (i !== undefined) predicted[i] = r.forecast; });
  const marks = (kind, hit) => { const v = blank(); t.judged.filter(r => r.signal === kind && r.hit === hit).forEach(r => { const i = index.get(weekBefore(r.issued_at)); if (i !== undefined) v[i] = hist[i].price; }); return v; };
  const priceChart = chart([
    {name:`실제값 (${a.unit})`, values:[...hist.map(h => h.price), ...future.map(() => NaN)], width:2},
    {name:'예측값 (4주 전에 낸 예측)', values:predicted, color:'var(--amber)', width:1.6},
    {name:'구매 신호 · 맞음', values:marks('buy', true), color:'#3f8a5f', dots:true, line:false, r:3.5},
    {name:'구매 신호 · 틀림', values:marks('buy', false), color:'#3f8a5f', dots:true, line:false, hollow:true, r:3.5},
    {name:'보류 신호 · 맞음', values:marks('hold', true), color:'#c0702b', dots:true, line:false, r:3.5},
    {name:'보류 신호 · 틀림', values:marks('hold', false), color:'#c0702b', dots:true, line:false, hollow:true, r:3.5}],
    {height:280, labels:dates, marks:[hist.length-1], markName:'오늘 (최신 가격)'});
  const s = t.summary, gainKrw = t.mean_gain === null ? '—' : krw(t.mean_gain);
  const kpis = `<div class="kpis five">${
    kpi('신호 적중률', num(t.hit_rate, 1), '%', `구매·보류 신호 ${t.signals}번 중 ${s.buy.hits+s.hold.hits}번 적중`)}${
    kpi('구매 신호', `${s.buy.hits}/${s.buy.count}`, '적중', `적중률 ${pct(s.buy.hit_rate, 0)} · 실제 평균 ${signed(s.buy.mean_actual_change_pct)}%`)}${
    kpi('보류 신호', `${s.hold.hits}/${s.hold.count}`, '적중', `적중률 ${pct(s.hold.hit_rate, 0)} · 실제 평균 ${signed(s.hold.mean_actual_change_pct)}%`)}${
    kpi('신호대로 했을 때', signed(t.mean_gain, 1), `EUR/100kg`, `신호 1번당 평균 절감 · 약 ${gainKrw}원/kg`, t.mean_gain > 0 ? 'good' : 'bad')}${
    kpi('놓친 급등', t.missed_jumps, '주', `'평소대로'였는데 4주에 +${rule.missed_jump_pct}% 넘게 오른 주`)}</div>`;
  const outcome = r => r.hit === null ? '<span class="muted">—</span>' : r.hit ? badge('맞음', 'green') : badge('틀림', 'red');
  const chip = r => `<span class="signal-chip ${SIGNAL[r.signal][1]}">${SIGNAL[r.signal][0]}</span>`;
  const pending = [...a.upcoming].reverse().map((r, i) => [escape(day(r.issued_at)), chip(r), `<b>${escape(r.target_date)}</b>`, `${signed(r.change_pct)}%`, '—',
    i === 0 ? badge('지금 판단', 'blue') : badge('판정 대기', 'amber'), '—']);
  const done = t.judged.slice(-8).reverse().map(r => [escape(day(r.issued_at)), chip(r), escape(r.target_date),
    `${signed(r.change_pct)}%`, `${signed(r.actual_change_pct)}%`, outcome(r), r.gain === null ? '—' : signed(r.gain, 1)]);
  const recent = table(['발행일', '판단', '목표일', '예측 변화', '실제 변화', '결과', '절감 (EUR/100kg)'], [...pending, ...done]);
  return `${banners}${hero}
    ${panel('판단 성적표 · 실측과 비교', kpis)}
    ${panel('실제값과 예측값', priceChart)}${panel('판단 기록 · 앞으로 4주와 지난 결과', recent)}`;
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
const PHASE = {normal:'정상', changed:'주입', recovery:'복귀', custom:'직접 입력'};

const RULE = {retrain: 8};

/* Overlaid histograms of weekly price change (%): the reference year vs the latest 13 weeks. */
function histogram(reference, current) {
  const width = 760, height = 200, left = 40, right = 12, top = 10, bottom = 28, edges = [];
  const span = Math.min(30, Math.max(6, Math.ceil(Math.max(...[...reference, ...current].map(Math.abs)))));
  const step = span/10; for (let v = -span; v < span; v += step) edges.push(v);
  const share = values => edges.map(e => values.filter(v => (v >= e || e === edges[0]) && (v < e+step || e === edges.at(-1))).length/values.length*100);
  const a = share(reference), b = share(current), peak = Math.max(...a, ...b, 1);
  const bw = (width-left-right)/edges.length, y = v => top+(1-v/peak)*(height-top-bottom);
  let svg = `<svg class="chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="주간 가격 변화 분포 비교">`;
  [0, peak/2, peak].forEach(v => { svg += `<line x1="${left}" y1="${y(v)}" x2="${width-right}" y2="${y(v)}" stroke="var(--grid)"/><text x="${left-6}" y="${y(v)+4}" text-anchor="end">${num(v, 0)}%</text>`; });
  edges.forEach((e, i) => {
    svg += `<rect x="${left+i*bw+1}" y="${y(a[i])}" width="${bw-2}" height="${y(0)-y(a[i])}" fill="#8b9b8a" opacity=".55"/>`;
    svg += `<rect x="${left+i*bw+bw*.25}" y="${y(b[i])}" width="${bw*.5}" height="${y(0)-y(b[i])}" fill="#c0504d" opacity=".85"/>`;
  });
  [-span, 0, span].forEach((v, i) => { svg += `<text x="${left+(v+span)/(2*span)*(width-left-right)}" y="${height-8}" text-anchor="${['start', 'middle', 'end'][i]}">${v > 0 ? '+' : ''}${num(v, 0)}%</text>`; });
  return `${svg}</svg><div class="legend"><span><i style="background:#8b9b8a"></i>직전 52주 (기준)</span><span><i style="background:#c0504d"></i>최근 13주</span><span class="muted">가로: 한 주 가격 변화 · 세로: 그 변화가 나온 주의 비율</span></div>`;
}
const spread = v => Math.sqrt(v.reduce((t, x) => t+x*x, 0)/v.length-(v.reduce((t, x) => t+x, 0)/v.length)**2);

const DRIFT_NAME = {ks:'KS (분포 모양)', wasserstein:'Wasserstein (변화 크기)'};
function driftView(r, d) {
  const steps = r.steps, ratio = key => steps.map(s => { const m = s.drift?.[key]; return m?.value != null && m?.threshold ? m.value/m.threshold : NaN; });
  const ks = ratio('ks'), ws = ratio('wasserstein');
  if (![...ks, ...ws].some(Number.isFinite)) return '';
  const alarm = steps.findIndex(s => s.alarm), fired = alarm >= 0 ? Object.entries(steps[alarm].checks).filter(([, c]) => c.alert).map(([k]) => DRIFT_NAME[k] || k) : [];
  const worst = steps.map((_, i) => Math.max(ks[i] || 0, ws[i] || 0)), peak = worst.indexOf(Math.max(...worst));
  // Rebuild the two windows compared at the peak week: real history up to the run, then the run's prices.
  const prices = [...d.dataset.rows.filter(row => row.date < steps[0].date).map(row => row.midpoint), ...steps.map(s => s.price)];
  const returns = prices.slice(1).map((p, i) => Math.log(p/prices[i])*100), last = returns.length-steps.length+peak;
  const current = returns.slice(last-12, last+1), reference = returns.slice(last-64, last-12);
  const chartScore = chart([
    {name:DRIFT_NAME.wasserstein, values:ws, color:'#c0504d', dots:true, width:1.8},
    {name:DRIFT_NAME.ks, values:ks, color:'#d08a2e', dots:true, width:1.4},
    {name:'경보 경계 = 1', values:steps.map(() => 1), color:'var(--muted)', dash:true, width:1.2}],
    {height:200, labels:steps.map(s => s.date), marks:alarm >= 0 ? [alarm] : [], markName:'드리프트 경보 (2주 연속 초과)'});
  const lead = alarm >= 0 ? `<b>${escape(steps[alarm].date)}</b> (${alarm+1}주차)에 <b>${fired.join(', ')}</b> 점수가 경계를 2주 연속 넘어 드리프트 경보가 울렸습니다.` : '이번 실행에서는 드리프트 경보가 울리지 않았습니다.';
  return panel('데이터 드리프트', `<p class="drift-lead">${lead} 가장 크게 벌어진 ${escape(steps[peak].date)}에는 직전 52주가 한 주 평균 <b>±${num(spread(reference), 1)}%</b> 움직인 데 비해 최근 13주는 <b>±${num(spread(current), 1)}%</b> 움직였습니다.</p>
    <div class="grid-two"><div><p class="note">점수 ÷ 경계값. 1을 넘으면 그 주는 경계 초과입니다.</p>${chartScore}</div><div><p class="note">${escape(steps[peak].date)} 기준 두 구간의 주간 가격 변화 분포</p>${histogram(reference, current)}</div></div>`);
}

function driftResult(r, d) {
  if (!r) return empty('완료된 실험 없음', '상황을 고르고 실행하세요. 학습·파인튜닝이 실제로 돌아 10~20초 걸립니다.');
  const steps = r.steps, retrainIdx = r.retraining.map(t => steps.findIndex(s => s.date === t.date)).filter(i => i >= 0);
  const b = r.business, cost = k => num(b[k].total_cost, 0);
  const [label, color] = LEVEL[r.observed_level], custom = r.kind === 'custom', matched = custom || r.observed_level === r.expected_level;
  const summary = `<div class="kpis four">${
    kpi('최고 경보 단계', `<span class="${matched ? '' : 'bad'}">${escape(label)}</span>`, '', custom ? '직접 넣은 가격 · 기대 단계 없음' : `기대 ${escape(LEVEL[r.expected_level][0])} · ${matched ? '일치' : '불일치'}`)}${
    kpi('감지 지연', r.detection_delay_observations ?? '—', '주', r.kind === 'none' ? '주입 없음' : custom ? '입력 시작 → 첫 경보' : '주입 시작 → 첫 경보')}${
    kpi('파인튜닝', r.retraining.length, '회', `승격 ${r.retraining.filter(t => t.promoted).length}회`)}${
    kpi('운영 WAPE', `${num(r.scores.fixed.wape.value, 2)} → ${num(r.scores.adaptive.wape.value, 2)}`, '%', '고정 모델 → 재학습 운영')}${
    ''}</div>`;
  const weeks = r.level_weeks, levelTable = table(['구간', '0단계 (일반)', '1단계 (감시)', '2단계 (파인튜닝)'], Object.entries(weeks).map(([k, counts]) => [k === 'custom' ? `직접 입력 ${steps.length}주` : {normal:'정상 13주', changed:'주입 26주', recovery:'복귀'}[k] || k, ...counts.map(n => `${n}주`)]));
  // Advice the fixed (never retrained) and the operating model would give on the last week, and how each scored.
  const adviceCard = (title, a) => a ? `<div class="advice-card"><span class="muted">${escape(title)} · ${escape(a.model)}</span><span class="signal-chip ${a.signal}">${SIGNAL[a.signal][0]}</span><b>${signed(a.change_pct)}%</b><small>4주 뒤 예측 ${price(a.forecast)}</small></div>` : '';
  const scoreRow = (name, t) => [escape(name), `${t.signals}번`, pct(t.hit_rate, 0), t.mean_gain === null ? '—' : signed(t.mean_gain, 1), `${t.missed_jumps}주`];
  const advice = r.advice ? panel('구매 판단 · 파인튜닝 전/후', `<div class="advice-pair">${adviceCard('재학습 없이 둔 모델', r.final_advice.fixed)}<span class="arrow">→</span>${adviceCard('재학습을 거친 운영 모델', r.final_advice.adaptive)}</div>
    <p class="note">마지막 주에 각 모델이 낸 판단입니다. 아래는 실행 중 매주 낸 판단을 4주 뒤 정답(넣은 가격)과 비교한 성적입니다 (정답이 나온 ${r.evaluated_predictions}주).</p>
    ${table(['모델', '구매·보류 신호', '적중률', '신호당 절감 (EUR/100kg)', '놓친 급등'], [scoreRow('재학습 없이 둔 모델', r.advice.fixed), scoreRow('재학습 운영', r.advice.adaptive)])}`) : '';
  // Real history before the run (grey), then the made-up future the run streamed; every future price is virtual.
  const past = d.dataset.rows.filter(row => row.date < steps[0].date).slice(-26), off = past.length;
  const dates = [...past.map(row => row.date), ...steps.map(s => s.date)], gap = () => past.map(() => NaN);
  const position = new Map(dates.map((x, i) => [x, i])), atTarget = dates.map(() => NaN);
  // Each forecast is drawn at the week it was for (its target), so it lines up with the price it tried to hit.
  steps.forEach(s => { const i = position.get(s.target_date); if (i !== undefined) atTarget[i] = s.adaptive; });
  const joined = values => [...past.map((_, i) => i === off-1 ? past[i].midpoint : NaN), ...values];
  const changedIdx = steps.map((s, i) => s.phase === 'changed' ? i+off : -1).filter(i => i >= 0);
  const bands = changedIdx.length ? [[changedIdx[0], changedIdx.at(-1)]] : [];
  return `${summary}${driftView(r, d)}${advice}
    ${panel(`실제 가격과 가상 미래 가격 (${escape(r.steps[0].date)}부터 가상) · ${escape(EXPERIMENT_LABEL[r.kind] || r.kind)}`, chart([
      {name:'실제 가격', values:[...past.map(row => row.midpoint), ...steps.map(() => NaN)], color:'#8b9b8a', width:2},
      {name:custom ? '가상 미래 가격 (넣은 값)' : '가상 미래 가격 (주입 후)', values:joined(steps.map(s => s.price))},
      // The built-in situations inject into a generated future; typed prices have no "before" to compare with.
      ...(custom ? [] : [{name:'가상 미래 가격 (주입 전)', values:joined(steps.map(s => s.original_price)), color:'var(--muted)', dash:true, width:1.4}]),
      {name:'예측값 (4주 전에 낸 예측)', values:atTarget, color:'var(--amber)', width:1.8}], {labels:dates, marks:retrainIdx.map(i => i+off), markName:'파인튜닝', bands}))}
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
// Weekly log-return band of the real history (0.5%–99.5%): a typed week outside it is flagged as unusual.
function returnBand(rows) {
  const r = rows.slice(1).map((x, i) => Math.log(x.midpoint/rows[i].midpoint)*100).sort((a, b) => a-b);
  const q = f => r[Math.min(r.length-1, Math.floor(f*(r.length-1)))];
  return {low: q(.005), high: q(.995)};
}
function adviceFor(d, current, forecast) {
  const rule = d.system?.rules?.decision; if (!rule) return '';
  const change = (forecast/current-1)*100, kind = change >= rule.buy_change_pct ? 'buy' : change <= rule.hold_change_pct ? 'hold' : 'normal';
  return `<span class="signal-chip ${kind}">${SIGNAL[kind][0]}</span> <span class="muted">예측 4주 변화 ${signed(change)}% (기준 ±${rule.buy_change_pct}%)</span>`;
}
function inputCheck(d, prices) {
  const band = returnBand(d.dataset.rows);
  const items = prices.slice(1).map((p, i) => { const r = Math.log(p/prices[i])*100, odd = r < band.low || r > band.high; return badge(`${i+2}주차 ${signed(r)}%${odd ? (r > 0 ? ' · 이례적 급등' : ' · 이례적 급락') : ''}`, odd ? 'red' : 'green'); });
  const odd = items.filter(x => x.includes('red')).length;
  return `<p class="note">입력 점검: 과거 20년 주간 변화의 99%는 ${signed(band.low)}% ~ ${signed(band.high)}% 안에 있습니다. ${odd ? `<b>${odd}주가 이 범위를 벗어났습니다.</b>` : '모두 평소 범위입니다.'} 6주만으로는 드리프트(최근 13주 vs 직전 52주)를 판정할 수 없어서, 드리프트·파인튜닝까지 보려면 "이어서 넣기"를 쓰세요.</p><div class="check-list">${items.join('')}</div>`;
}
function direct(d) {
  const text = state.predictJson ?? samplePrices(d), r = state.predictResult;
  let sent = null; try { sent = JSON.parse(text).prices; } catch { sent = null; }
  const ok = r?.status === 200 && Array.isArray(sent) && sent.length === 6;
  const result = r ? `<div class="toolbar">${badge(`HTTP ${r.status}`, r.status === 200 ? 'green' : 'red')}${r.status === 200 && r.body.korea ? badge(`한국 수입원가 약 ${num(r.body.korea.krw_per_kg, 0)}원/kg`, 'blue') : ''}${ok ? adviceFor(d, sent.at(-1), r.body.prediction) : ''}</div>${ok ? inputCheck(d, sent) : ''}<pre>${escape(JSON.stringify(r.body, null, 2))}</pre>` : '';
  return `<p>최근 6주 가격(EUR/100kg)을 JSON으로 보냅니다. 값을 0이나 음수로 바꾸거나 개수를 줄이면 422 검증 오류가 납니다. 이렇게 보낸 예측은 운영 기록에 남지 않습니다.</p>
    <textarea id="predict-json" rows="8" spellcheck="false">${escape(text)}</textarea>
    <div class="toolbar"><button class="secondary" data-action="sample">최근 실제 6주</button><button class="secondary" data-action="sample-noise">예시 데이터 다시 생성</button><button class="secondary" data-action="sample-bad">422 예시</button><button data-action="predict-json">예측 실행</button></div>${result}`;
}

// "이어서 넣기": future weekly prices typed (or generated, then edited) by the user, streamed through the same
// monitoring → fine-tune → gate → promote path as the drift experiment, starting from the serving model.
const STREAM_PATTERN = {none:['정상', '변동 그대로', null], ramp:['급등', '13주 뒤부터 4주에 걸쳐 오른 뒤 유지', 15], variance:['변동폭 확대', '13주 뒤부터 주간 변동 × 배수', 3]};
function seeded(seed) { return () => { seed |= 0; seed = seed+0x6D2B79F5|0; let t = Math.imul(seed^seed>>>15, 1|seed); t = t+Math.imul(t^t>>>7, 61|t)^t; return ((t^t>>>14)>>>0)/4294967296; }; }
function streamPrices(d, pattern, strength, weeks) {
  // Recent year's weekly moves with their trend removed (mean 0): "정상" wobbles around today's price instead of
  // carrying last year's slide, so the changed weeks stand out against it.
  const rows = d.dataset.rows.slice(-53), raw = rows.slice(1).map((x, i) => Math.log(x.midpoint/rows[i].midpoint));
  const mean = raw.reduce((t, v) => t+v, 0)/raw.length, returns = raw.map(v => v-mean), rand = seeded(42);
  let base = rows.at(-1).midpoint, out = [];
  for (let k = 0; k < weeks; k++) {
    let r = returns[Math.floor(rand()*returns.length)];
    if (pattern === 'variance' && k >= 13) r *= strength;
    base *= Math.exp(r);
    out.push(pattern === 'ramp' && k >= 13 ? base*(1+strength/100*Math.min(1, (k-12)/4)) : base);
  }
  return out.map(v => v.toFixed(2));
}
function streamPanel(d) {
  if (!state.stream.text) state.stream.text = streamPrices(d, state.stream.pattern, Number(state.stream.strength) || 0, state.stream.weeks).join(', ');
  const st = state.stream, running = state.job || (d.jobs?.jobs || []).some(j => j.kind === 'experiment' && ['queued', 'running'].includes(j.status));
  const count = (st.text.match(/[^\s,]+/g) || []).length;
  return `<p>마지막 실제 주(${escape(d.dataset.rows.at(-1).date)}) 다음부터 넣을 주간 가격입니다. 패턴으로 채운 뒤 숫자를 직접 고쳐도 됩니다. 지금 운영 모델에서 출발해 한 주씩 흘려보내며 매주 예측·구매 판단·드리프트 점수·경보 단계를 기록하고, 성능 경보가 ${RULE.retrain}회 이어지면 실제로 파인튜닝해 게이트를 통과한 모델을 운영으로 올립니다. 넣은 가격이 그대로 각 예측의 정답이 됩니다.</p>
    <div class="form-grid"><label>패턴<select id="stream-pattern">${Object.entries(STREAM_PATTERN).map(([k, [name]]) => `<option value="${k}" ${st.pattern === k ? 'selected' : ''}>${name}</option>`).join('')}</select></label>
      <label>세기 ${st.pattern === 'ramp' ? '(상승 %)' : st.pattern === 'variance' ? '(배수)' : '(정상은 없음)'}<input id="stream-strength" type="number" step="0.5" min="0" value="${escape(st.strength)}" ${st.pattern === 'none' ? 'disabled' : ''}></label>
      <label>주 수 (13~156)<input id="stream-weeks" type="number" min="13" max="156" value="${escape(st.weeks)}"></label></div>
    <p class="note">${escape(STREAM_PATTERN[st.pattern][1])}. 앞 13주는 최근 1년 변동을 그대로 씁니다. 감지만 보려면 13주 이상, 파인튜닝까지 보려면 26주 이상 넣으세요.</p>
    <textarea id="stream-prices" spellcheck="false" placeholder="예: 452.1, 455.3, 449.8, …">${escape(st.text)}</textarea>
    <div class="toolbar"><button class="secondary" data-action="stream-fill">패턴으로 채우기</button><button data-action="stream-run" ${running || !d.models?.active || count < 13 ? 'disabled' : ''}>이어서 넣기 실행</button>${badge(`${count}주 입력`, count >= 26 ? 'green' : count >= 13 ? 'amber' : 'red')}${running ? badge('실행 중… 10~30초', 'amber') : ''}</div>`;
}

function drift(d) {
  RULE.retrain = d.system?.rules?.monitoring?.retrain_consecutive ?? RULE.retrain;
  const results = (d.experiments || []).filter(r => r.level_weeks), current = results.find(r => r.id === state.experiment) || results.at(-1);
  const running = state.job || (d.jobs?.jobs || []).some(j => j.kind === 'experiment' && ['queued', 'running'].includes(j.status));
  const history = results.length > 1 ? `<label class="inline">지난 결과 보기<select id="experiment-pick">${[...results].reverse().map(r => `<option value="${escape(r.id)}" ${current?.id === r.id ? 'selected' : ''}>${escape(day(r.at))} · ${escape(EXPERIMENT_LABEL[r.kind] || r.kind)} · ${escape(r.id)}</option>`).join('')}</select></label>` : '';
  const buttons = Object.entries(KIND_LABEL).map(([kind, label]) => `<button data-action="experiment" data-kind="${kind}" class="${kind === 'none' ? 'secondary' : kind === 'variance' ? 'danger' : ''}" ${running || !d.models?.active ? 'disabled' : ''} title="${escape(KIND_NOTE[kind])}">${escape(label)}</button>`).join('');
  const mode = `<div class="segmented mode-tabs">${[['once', '한 번 예측'], ['stream', '이어서 넣기']].map(([k, label]) => `<button data-mode="${k}" class="${state.simMode === k ? 'selected' : ''}">${label}</button>`).join('')}</div>`;
  const directPanel = panel(state.simMode === 'stream' ? '직접 예측 · 이어서 넣기 (POST /api/experiments · prices)' : '직접 예측 · POST /api/predict', state.simMode === 'stream' ? streamPanel(d) : direct(d), mode);
  return `${directPanel}${panel('드리프트 실험', `<p><b>지금 운영 중인 모델</b>에 실제 마지막 주 다음의 가상 미래 65주(정상 13 → 상황 26 → 복귀 26)를 한 주씩 흘려보냅니다. 운영과 같은 규칙으로 감시하고, 필요하면 실제로 파인튜닝해 게이트를 통과한 모델을 운영으로 올립니다. 실험에서 학습한 모델은 끝나면 모두 재학습 이력에 등록됩니다. 가상 가격은 실제 데이터와 섞이지 않게 따로 보관합니다.</p>
    <div class="toolbar">${buttons}${running ? badge('실험 실행 중… 10~20초', 'amber') : ''}${history}</div>
    <p class="note">${Object.entries(KIND_LABEL).map(([k, v]) => `<b>${escape(v)}</b>: ${escape(KIND_NOTE[k])}`).join(' · ')}</p>`)}
    ${driftResult(current, d)}`;
}

// Severity drives the colour: failures/rollbacks red, warnings amber, passes/promotions green, the rest neutral.
const SEVERITY = {ERROR:'error', FAIL:'error', ROLLBACK:'error', 'GATE FAILED':'error', WARN:'warn', OK:'ok', 'GATE PASSED':'ok', INFO:'info'};
const SEVERITY_LABEL = [['error', '오류·롤백'], ['warn', '경고'], ['ok', '통과·승격'], ['info', '정보']];
function logView(lines) {
  if (!lines?.length) return empty('로그 없음');
  const rows = lines.slice(-200).reverse().map(line => {
    const [, tag = 'INFO', message = line.slice(33)] = line.slice(33).match(/^\[([A-Z ]+)\]\s*(.*)$/) || [];
    return {time: line.slice(11, 19), tag, message, sev: SEVERITY[tag] || 'info'};
  });
  const count = sev => rows.filter(r => r.sev === sev).length;
  const legend = `<div class="log-legend">${SEVERITY_LABEL.map(([sev, label]) => `<span class="log-tag sev-${sev}">● ${label} ${count(sev)}</span>`).join('')}</div>`;
  return `<div class="log">${legend}${rows.map(r => `<div class="log-line sev-${r.sev}"><time>${escape(r.time)}</time><span class="log-tag sev-${r.sev}">${escape(r.tag)}</span><span class="log-msg">${escape(r.message)}</span></div>`).join('')}</div>`;
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
    ${r.decision ? card('구매 판단 (서비스 탭)', [['지금 구매', `예측 4주 변화 ≥ +${r.decision.buy_change_pct}%`], ['구매 보류', `예측 4주 변화 ≤ ${r.decision.hold_change_pct}%`], ['평소대로', '그 사이'], ['놓친 급등', `평소대로인데 실제 +${r.decision.missed_jump_pct}% 초과`]], r.decision.reason) : ''}
    ${card('한국 수입원가 환산', [['EU 변화 반영 비율 β', k.passthrough_beta], ['반영 시차', `약 ${k.lag_months}개월`]], k.reason)}
    ${card('실행 환경', [['모델', `LSTM · 입력 ${s.sequence}주 → ${s.horizon_days}일 뒤`], ['로딩', s.loading_mode], ['작업 실행', s.job_runner || '서버 내 작업 스레드'], ['저장소', s.storage], ['규칙 버전', r.version]])}
  </div>`;
}

const PAGES = {
  service: ['서비스', 'PURCHASE ADVICE', '4주 뒤 가격 예측으로 본 지금의 구매 판단과 그 판단의 과거 성적', advisor, ['decision', 'models']],
  dashboard: ['대시보드', 'OVERVIEW', '4주 뒤 버터 가격 예측과 운영 상태', dashboard, ['metrics', 'models', 'alerts', 'health', 'dataset', 'predictions', 'jobs', 'system', 'series']],
  model: ['모델', 'MLOPS', `학습 → 게이트(검증 WAPE) 통과 시 자동 배포 → MLflow @production`, model, ['models', 'predictions', 'health']],
  drift: ['시뮬레이션', 'SIMULATION', '직접 예측(200·422) · 정상 입력 / 이상 입력(파인튜닝 X) / 이상 입력(파인튜닝)', drift, ['experiments', 'jobs', 'system', 'dataset', 'models']],
  system: ['시스템', 'SYSTEM', '서버가 실제로 쓰는 규칙과 설정 (/api/system · rules.json)', system, ['system']],
  ops: ['로그·데이터', 'OPERATIONS', '운영 로그와 데이터 적재', ops, ['logs', 'jobs']],
};
const SOURCES = {metrics:() => `/metrics?window=${state.window}`, models:'/models', alerts:'/alerts', health:'/health', dataset:'/datasets', predictions:'/predictions', jobs:'/jobs', experiments:'/experiments', decision:'/decision', logs:'/logs', system:'/system', series:() => `/metrics/series?minutes=${Math.min(state.window/60, 1440)}`};

function render() {
  const [title, eyebrow, description, view] = PAGES[state.tab];
  $('title').textContent = title; $('eyebrow').textContent = eyebrow; $('description').textContent = description;
  document.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('active', b.dataset.tab === state.tab); b.setAttribute('aria-current', b.dataset.tab === state.tab ? 'page' : 'false'); });
  $('content').innerHTML = view(state.data);
}

async function refresh() {
  try {
    const names = [...new Set(['health', ...PAGES[state.tab][4]])];
    // No serving model yet is a normal state for the advice (409), shown as such instead of a connection failure.
    const results = await Promise.all(names.map(n => { const call = api(typeof SOURCES[n] === 'function' ? SOURCES[n]() : SOURCES[n]); return n === 'decision' ? call.catch(error => ({error: error.message})) : call; }));
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
  if (b.dataset.mode) { state.simMode = b.dataset.mode; render(); return; }
  if (b.dataset.action === 'stream-fill') {
    const st = state.stream, weeks = Math.min(156, Math.max(13, Math.round(Number(st.weeks) || 39)));
    st.weeks = weeks; st.text = streamPrices(state.data, st.pattern, Number(st.strength) || 0, weeks).join(', '); render(); return;
  }
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
    if (b.dataset.action === 'stream-run') {
      const prices = (state.stream.text.match(/[^\s,]+/g) || []).map(Number);
      const bad = prices.findIndex(v => !Number.isFinite(v) || v <= 0);
      if (bad >= 0) { toast(`${bad+1}번째 값이 양수 숫자가 아닙니다`); return; }
      const job = await post('/experiments', {prices}); state.experiment = job.id;
      await waitJob(job.id, j => j.result.adopted_models.length ? `이어서 넣기 완료 · ${j.result.adopted_models.join(', ')} 재학습 이력에 등록` : '이어서 넣기 완료 · 파인튜닝 없음 (운영 모델 그대로)');
    }
    if (b.dataset.action === 'experiment') { state.kind = b.dataset.kind; const job = await post('/experiments', {kind:state.kind}); state.experiment = job.id; await waitJob(job.id, j => j.result.adopted_models.length ? `실험 완료 · ${j.result.adopted_models.join(', ')} 재학습 이력에 등록` : '실험 완료 · 파인튜닝 없음 (운영 모델 그대로)'); }
    // Blink the forecast (0.5 s) so a re-run with the same value still visibly refreshes.
    if (b.dataset.action === 'predict') { await post('/predict'); state.flash = true; await refresh(); state.flash = false; }
    if (b.dataset.select) { const r = await post(`/models/${b.dataset.select}/select`); toast(`${r.active} 운영 반영 · MLflow v${r.registry.version} @production · 대시보드에서 예측 실행`); await refresh(); }
  } catch (error) { toast(error.message); } finally { b.disabled = false; }
});
document.addEventListener('change', event => {
  if (event.target.id === 'experiment-pick') { state.experiment = event.target.value; render(); }
  if (event.target.id === 'stream-pattern') { const st = state.stream; st.pattern = event.target.value; st.strength = STREAM_PATTERN[st.pattern][2] ?? 0; render(); }
});
document.addEventListener('input', event => {
  if (event.target.id === 'predict-json') state.predictJson = event.target.value;
  if (event.target.id === 'stream-prices') state.stream.text = event.target.value;
  if (event.target.id === 'stream-strength') state.stream.strength = event.target.value;
  if (event.target.id === 'stream-weeks') state.stream.weeks = event.target.value;
});
document.addEventListener('submit', async event => {
  if (event.target.id !== 'import-form') return;
  event.preventDefault();
  const b = event.target.querySelector('button'); b.disabled = true;
  try { const r = await api('/datasets/import', {method:'POST', body:new FormData(event.target)}); toast(`${r.rows}주 적재 완료`); await refresh(); }
  catch (error) { toast(`적재 실패: ${error.message}`); } finally { b.disabled = false; }
});
window.addEventListener('hashchange', () => { state.tab = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : 'service'; refresh(); });
state.tab = PAGES[location.hash.slice(1)] ? location.hash.slice(1) : 'service';
const tick = () => { $('clock').textContent = new Date().toLocaleTimeString('ko-KR', {hour12:false}); };
tick(); refresh();
setInterval(() => { tick(); if (!document.hidden && !state.job) refresh(); }, 5000);
