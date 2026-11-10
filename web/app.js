'use strict';

const paths = {
  grid: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  history: '<path d="M3 11a9 9 0 1 1 3 7"/><path d="M3 4v7h7M12 7v5l3 2"/>',
  server: '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/><path d="M7 6.5h.01M7 17.5h.01M16 6.5h2M16 17.5h2"/>',
  lock: '<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3M12 14v3"/>',
  refresh: '<path d="M20 7v5h-5M4 17v-5h5"/><path d="M6.1 6.1a8 8 0 0 1 13 2.9M4.9 15a8 8 0 0 0 13 2.9"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  radar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="m12 12 6-6"/><circle cx="12" cy="12" r="1"/>',
  workflow: '<rect x="3" y="8" width="6" height="8" rx="1.5"/><rect x="15" y="2" width="6" height="6" rx="1.5"/><rect x="15" y="16" width="6" height="6" rx="1.5"/><path d="M9 12h3V5h3M12 12v7h3"/>',
  report: '<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v7c0 4 16 4 16 0V5M4 12v7c0 4 16 4 16 0v-7"/>',
  wallet: '<path d="M20 8V5a2 2 0 0 0-2-2H6a3 3 0 0 0-3 3v12a2 2 0 0 0 2 2h15V8H6a2 2 0 0 1-3-2"/><path d="M20 12h-5v4h5M16.5 14h.01"/>',
  arrow: '<path d="m9 5 7 7-7 7"/>', back: '<path d="m15 5-7 7 7 7"/>',
  check: '<path d="m5 12 4 4L19 6"/>', close: '<path d="m6 6 12 12M6 18 18 6"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7h.01"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M7 3v4M17 3v4M8 14h2M14 14h2M8 17h2"/>',
  pulse: '<path d="M2 12h5l3-8 4 16 3-8h5"/>',
  alert: '<path d="m10.3 3.8-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.7-3.2l-8-14a2 2 0 0 0-3.4 0ZM12 9v4M12 17h.01"/>'
};
const icon = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] ?? paths.clock}</svg>`;
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
const labels = { ready: '等待调度', running: '运行中', success: '成功', partial: '部分完成', failed: '失败', skipped: '已跳过', interrupted: '已中断', unknown: '待确认', attention: '需关注', overdue: '记录逾期', disabled: '未加载', stopped: '未运行' };
const badge = (status) => `<span class="badge ${esc(status)}">${esc(labels[status] ?? status)}</span>`;
const number = (value) => value === null || value === undefined ? '—' : new Intl.NumberFormat('en-US').format(value);
const tokens = (value) => value === null || value === undefined ? '未知' : value >= 1000000 ? `${(value / 1000000).toFixed(2).replace(/\.?0+$/, '')}M` : value >= 1000 ? `${(value / 1000).toFixed(2).replace(/\.?0+$/, '')}K` : number(value);
const bytes = (value) => value === null || value === undefined ? '—' : value >= 1073741824 ? `${(value / 1073741824).toFixed(2)} GB` : value >= 1048576 ? `${(value / 1048576).toFixed(1)} MB` : `${number(value)} B`;
const dateFormat = (value, options) => value === null || value === undefined ? '—' : new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false, ...options }).format(new Date(value * 1000));
const dateTime = (value) => dateFormat(value, { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const fullTime = (value) => dateFormat(value, { year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
const shortTime = (value) => dateFormat(value, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
const duration = (value) => value === null || value === undefined ? '未知' : value < 1 ? '< 1 秒' : value < 60 ? `${value.toFixed(1)} 秒` : value < 3600 ? `${Math.floor(value / 60)} 分 ${Math.floor(value % 60)} 秒` : `${Math.floor(value / 3600)} 小时 ${Math.floor(value % 3600 / 60)} 分`;
const sourceLabel = (run) => run.kind === 'history' ? '历史凭据' : '实时采集';
const taskName = (id) => state.data?.tasks.find((task) => task.id === id)?.name ?? id;
const taskHref = (id) => `#task/${encodeURIComponent(id)}`;
const state = { data: null, route: '', taskSearch: '', filters: { task_id: '', status: 'non_skipped', kind: '', days: '7', search: '' }, offset: 0, history: null, historyTicket: 0, logs: new Map(), shownLogs: new Set(), loading: false, dialogRun: null };
const main = document.querySelector('#main');
const dialog = document.querySelector('#run-dialog');
const calendarState = { start: MiniCalendar.weekStart(MiniCalendar.dateKey(Date.now()/1000)), selected: MiniCalendar.dateKey(Date.now()/1000), mode: 'planned', task: '', data: null, ticket: 0, followDay: true };
let searchTimer;

/** Preserve scroll while polling; stale responses must never overwrite another week's view. */
function calendarPage() {
  return heading('任务日历', '看清一周安排，追踪每一次实际执行。') + '<div id="calendar-content">'+MiniCalendar.view(calendarState.data, state.data.tasks, calendarState, { esc, icon, badge, taskHref, duration, labels })+'</div>';
}
function updateCalendar(followDay = false) {
  const target=document.querySelector('#calendar-content');
  if (!target) return;
  const old=document.querySelector('#calendar-scroll'), x=old?.scrollLeft??0, y=old?.scrollTop??0;
  target.innerHTML=MiniCalendar.view(calendarState.data,state.data.tasks,calendarState,{esc,icon,badge,taskHref,duration,labels});
  const next=document.querySelector('#calendar-scroll');
  if(next){next.scrollLeft=x;next.scrollTop=y;
    if(followDay&&next.scrollWidth>next.clientWidth){const day=target.querySelector('.cal-day.selected');if(day)next.scrollLeft=Math.max(0,day.offsetLeft+day.offsetWidth/2-next.clientWidth/2);}
  }
}
async function loadCalendar() {
  const ticket=++calendarState.ticket;
  try {
    const data=await api('/api/calendar?start='+encodeURIComponent(calendarState.start));
    if(ticket!==calendarState.ticket) return;
    calendarState.data=data; updateCalendar(calendarState.followDay); calendarState.followDay=false;
  } catch(error) {if(ticket===calendarState.ticket) toast('日历读取失败：'+error.message);}
}

/** 所有数据显示都转义后插入；接口不提供执行、暂停或修改任务的动作。 */
async function api(path) {
  const response = await fetch(path, { cache: 'no-store', signal: AbortSignal.timeout(12000) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error ?? `请求失败 (${response.status})`);
  return data;
}

function toast(message) {
  const node = document.querySelector('#toast');
  node.textContent = message;
  node.hidden = false;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => { node.hidden = true; }, 3500);
}

function empty(text) {
  return `<div class="empty">${icon('history')}${esc(text)}</div>`;
}

function heading(title, subtitle, eyebrow = 'MAC MINI / OPERATIONS') {
  return `<div class="page-heading"><div><div class="eyebrow">${esc(eyebrow)}</div><h1>${esc(title)}</h1><p class="subtitle">${esc(subtitle)}</p></div><div class="date-label">${icon('calendar')}${dateFormat(Date.now() / 1000, { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' })}</div></div>`;
}

function taskRows(tasks) {
  return tasks.map((task) => {
    const last = task.last_run;
    return `<tr class="clickable" data-task="${esc(task.id)}"><td><div class="task-name"><span class="task-icon">${icon(task.icon)}</span><a class="task-link" href="${taskHref(task.id)}"><strong>${esc(task.name)}</strong><small>${task.source === 'codex-script' ? 'Codex 触发 · 脚本执行' : 'launchd · ' + (task.instrumented ? '已接入完整记录' : '现有状态')}</small></a></div></td><td>${badge(task.state)}</td><td><span class="schedule-text">${esc(task.schedule.text)}</span><span class="secondary">北京时间${task.run_at_load ? ' · 登录时也执行' : ''}</span></td><td class="time">${last ? dateTime(last.created_at) : '暂无记录'}<span class="secondary">${last ? (last.status === 'running' ? '执行中' : labels[last.status]) + ' · ' + sourceLabel(last) : '等待首次采集'}</span></td><td class="time">${task.next_at ? dateTime(task.next_at) : task.state === 'running' ? '当前正在执行' : '由调度器决定'}<span class="secondary">${task.next_at ? task.next_estimated ? '间隔估计' : '计划时间' : ''}</span></td><td class="arrow">${icon('arrow')}</td></tr>`;
  }).join('');
}

function taskTable(tasks) {
  return `<div class="table-wrap"><table class="task-table"><thead><tr><th>任务名称</th><th>当前状态</th><th>执行计划</th><th>最近记录</th><th>下次执行</th><th></th></tr></thead><tbody id="task-rows">${taskRows(tasks)}</tbody></table></div>${tasks.length ? '' : empty('没有匹配的任务')}`;
}

function activityRows(records) {
  return records.slice(0, 5).map((run) => `<div class="activity-row clickable" data-run="${esc(run.id)}" tabindex="0"><span class="activity-status ${esc(run.status)}">${icon(run.status === 'failed' || run.status === 'interrupted' ? 'close' : run.status === 'running' ? 'pulse' : run.status === 'skipped' ? 'clock' : 'check')}</span><div><div class="activity-title">${esc(taskName(run.task_id))} · ${esc(run.summary)}</div><div class="activity-sub">${esc(sourceLabel(run))} · ${run.duration === null ? '耗时未知' : duration(run.duration)}</div></div><span class="activity-time">${shortTime(run.created_at)}</span></div>`).join('') || empty('尚无执行记录');
}

function chart() {
  const days = state.data.daily;
  const totals = days.map((day) => Object.values(day.counts).reduce((sum, count) => sum + count, 0));
  const maximum = Math.max(1, ...totals);
  return `<div class="chart">${days.map((day, index) => {
    const total = totals[index];
    const success = day.counts.success ?? 0;
    const failed = (day.counts.failed ?? 0) + (day.counts.interrupted ?? 0);
    const skipped = total - success - failed;
    return `<div class="chart-col" title="${esc(day.date)}：${total} 条记录，${failed} 条失败或中断"><div class="bar-label">${total || '—'}</div><div class="bar-stack" style="height:${Math.max(2, total / maximum * 115)}px"><div class="bar-success" style="height:${total ? success / total * 100 : 0}%"></div><div class="bar-failed" style="height:${total ? failed / total * 100 : 0}%"></div><div class="bar-skipped" style="height:${total ? skipped / total * 100 : 0}%"></div></div></div>`;
  }).join('')}</div><div class="chart-dates">${days.map((day, index) => `<span>${index === days.length - 1 ? '今天' : esc(day.date.slice(5).replace('-', '/'))}</span>`).join('')}</div><div class="legend"><span><i></i>成功</span><span><i class="fail"></i>失败 / 中断</span><span><i class="skip"></i>跳过 / 其他</span></div><div class="chart-note">历史凭据与接入后的执行记录 · 无记录不代表未执行</div>`;
}

function overview() {
  const data = state.data;
  const stats = data.stats;
  const tasks = data.tasks.filter((task) => task.category === 'work');
  const filtered = tasks.filter((task) => (task.name + task.description).toLowerCase().includes(state.taskSearch.toLowerCase()));
  return `${heading('任务总览', '掌握每一次运行，让定时任务有迹可循。')}
    ${data.collector.errors?.length ? `<div class="notice">部分数据源暂时无法读取：${esc(data.collector.errors.join('；'))}。页面保留已采集记录。</div>` : ''}
    <section class="stats" aria-label="任务概况"><div class="stat"><div class="stat-label">监控任务${icon('grid')}</div><div class="stat-value">${stats.tasks}<small>个任务</small></div><div class="stat-note">${tasks.filter((task) => task.instrumented).length} 个完整记录 · ${tasks.filter((task) => task.source === 'codex-script').length} 个外部触发</div></div><div class="stat"><div class="stat-label">今日记录${icon('history')}</div><div class="stat-value">${number(stats.today)}<small>条</small></div><div class="stat-note">${stats.counts.success ?? 0} 次成功 · ${stats.counts.skipped ?? 0} 次跳过</div></div><div class="stat"><div class="stat-label">正在运行${icon('pulse')}</div><div class="stat-value healthy">${stats.running}<small>个任务</small></div><div class="stat-note">${stats.running ? '任务正在执行中' : '当前等待下一次调度'}</div></div><div class="stat"><div class="stat-label">需要关注${icon('alert')}</div><div class="stat-value ${stats.attention ? 'problem' : 'healthy'}">${stats.attention}<small>个任务</small></div><div class="stat-note">${stats.attention ? '查看异常、逾期或缺失记录' : '任务状态正常'}</div></div></section>
    <section class="panel"><div class="panel-heading"><div><h2>定时任务<span class="count-pill">${tasks.length}</span></h2><p class="panel-description">执行计划、运行状态与最近结果</p></div><label class="search-box">${icon('search')}<input id="task-search" type="search" aria-label="搜索任务" placeholder="搜索任务…" value="${esc(state.taskSearch)}" /></label></div>${taskTable(filtered)}<div class="panel-foot">${icon('info')}点击任务查看详情。间隔任务的下次时间为估计；历史记录保留原有精度。</div></section>
    <div class="lower-grid"><section class="panel"><div class="panel-heading"><div><h2>最近活动</h2><p class="panel-description">最新的执行与结果记录</p></div><a href="#history" class="text-link">全部记录${icon('arrow')}</a></div><div class="activity">${activityRows(data.recent)}</div></section><section class="panel"><div class="panel-heading"><div><h2>运行记录趋势</h2><p class="panel-description">最近 7 天</p></div><span class="count-pill">7 DAYS</span></div>${chart()}</section></div>`;
}

function options(items, selected) {
  return items.map(([value, text]) => `<option value="${esc(value)}"${String(value) === String(selected) ? ' selected' : ''}>${esc(text)}</option>`).join('');
}

function historyFilters(taskId) {
  return `<div class="filters">${taskId ? '' : `<select data-filter="task_id" aria-label="按任务筛选">${options([['', '全部任务'], ...state.data.tasks.map((task) => [task.id, task.name])], state.filters.task_id)}</select>`}<select data-filter="status" aria-label="按执行结果筛选">${options([['non_skipped', '隐藏跳过'], ['', '全部结果'], ...['success', 'partial', 'failed', 'running', 'skipped', 'interrupted', 'unknown'].map((key) => [key, labels[key]])], state.filters.status)}</select><select data-filter="kind" aria-label="按记录来源筛选">${options([['', '全部来源'], ['execution', '实时采集'], ['history', '历史凭据']], state.filters.kind)}</select><select data-filter="days" aria-label="按时间筛选">${options([['1', '今天'], ['7', '最近 7 天'], ['30', '最近 30 天'], ['0', '全部时间']], state.filters.days)}</select></div>`;
}

function historyPanel(taskId = '') {
  return `<section class="panel"><div class="panel-heading"><div><h2>执行记录</h2><p class="panel-description">仅保留最近 7 天；默认隐藏跳过记录，可通过结果筛选查看</p></div>${historyFilters(taskId)}</div><div id="history-content" aria-live="polite">${empty('正在读取执行记录…')}</div><div class="panel-foot">${icon('info')}历史凭据不是完整执行历史。没有可靠时间的记录显示“未知”，不会补成零耗时。</div></section>`;
}

function runTable(result, taskId) {
  if (!result.items.length) return empty('当前筛选下没有执行记录');
  return `<div class="table-wrap"><table class="task-table"><thead><tr><th>${taskId ? '执行摘要' : '任务 / 执行摘要'}</th><th>结果</th><th>开始 / 记录时间</th><th>耗时</th><th>来源</th><th></th></tr></thead><tbody>${result.items.map((run) => `<tr class="clickable" data-run="${esc(run.id)}" tabindex="0"><td class="summary-cell"><strong style="font-weight:500">${esc(taskId ? run.summary : taskName(run.task_id))}</strong>${taskId ? '' : `<span class="secondary" title="${esc(run.summary)}">${esc(run.summary)}</span>`}</td><td>${badge(run.status)}</td><td class="time">${dateTime(run.created_at)}<span class="secondary">${run.started_at === null ? '原有记录时间' : '开始时间'}</span></td><td class="time">${run.status === 'running' ? '执行中' : duration(run.duration)}</td><td><span class="secondary" style="margin-top:0">${sourceLabel(run)}</span></td><td class="arrow">${icon('arrow')}</td></tr>`).join('')}</tbody></table></div><div class="pagination"><span>共 ${number(result.total)} 条 · 当前 ${result.offset + 1}–${result.offset + result.items.length}</span><div><button class="button" data-page="prev"${result.offset === 0 ? ' disabled' : ''}>上一页</button><button class="button" data-page="next"${result.offset + result.items.length >= result.total ? ' disabled' : ''}>下一页</button></div></div>`;
}

async function loadHistory(taskId = '') {
  const ticket = ++state.historyTicket;
  const filters = { ...state.filters, task_id: taskId || state.filters.task_id, limit: '30', offset: String(state.offset) };
  const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value !== ''));
  try {
    const result = await api('/api/runs?' + query);
    if (ticket !== state.historyTicket) return;
    state.history = result;
    const target = document.querySelector('#history-content');
    if (target) target.innerHTML = runTable(result, taskId);
  } catch (error) {
    if (ticket !== state.historyTicket) return;
    const target = document.querySelector('#history-content');
    if (target) target.innerHTML = empty('记录读取失败：' + error.message);
  }
}

function infoRow(label, content, mono = false) {
  return `<div class="info-row"><dt>${esc(label)}</dt><dd${mono ? ' class="mono"' : ''}>${content}</dd></div>`;
}

function business(task) {
  const data = task.data;
  if (task.id === 'metaso-balance') {
    const history = data.balance_history ?? [];
    const values = history.filter((item) => item.balance !== null).map((item) => item.balance);
    const min = Math.min(...values), max = Math.max(...values);
    const points = values.map((value, index) => `${index / Math.max(1, values.length - 1) * 400},${43 - (value - min) / Math.max(1, max - min) * 38}`).join(' ');
    return `<div class="panel"><div class="panel-heading"><h2>账户积分</h2>${data.balance !== null && data.balance < data.threshold ? badge('attention') : badge('success')}</div><div class="business-card"><div class="business-label">最近留存的剩余积分</div><div class="business-value">${number(data.balance)}<small>点</small></div><div class="secondary">${history.length ? fullTime(history.at(-1).observed_at) : '暂无记录'} · 非实时余额查询</div>${values.length > 1 ? `<svg class="sparkline" viewBox="0 0 400 48" preserveAspectRatio="none" role="img" aria-label="最近 ${values.length} 次余额记录趋势"><polyline points="${points}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>` : ''}<div class="mini-stats"><div class="mini-stat">告警阈值<strong>${number(data.threshold)}</strong></div><div class="mini-stat">调度方式<strong style="font-size:14px">Codex 定时触发</strong></div></div></div></div>`;
  }
  if (task.id === 'fastgpt-backup') {
    const backup = data.latest_backup;
    return `<div class="panel"><div class="panel-heading"><h2>备份产物</h2>${backup ? badge('success') : badge('unknown')}</div><div class="business-card"><div class="business-label">最近一次备份大小</div><div class="business-value">${backup ? bytes(backup.bytes) : '—'}</div><div class="secondary">${backup ? `${fullTime(backup.completed_at)} · ${number(backup.file_count)} 个文件` : '尚无备份清单'}</div><div class="mini-stats"><div class="mini-stat">保留周期<strong>7 天</strong></div><div class="mini-stat">存储组件<strong>3 个</strong></div></div>${(data.backups ?? []).slice(-4).reverse().map((item) => `<div class="backup-item"><span>${esc(item.date)}</span><span>${bytes(item.bytes)} · ${item.file_count} 文件</span></div>`).join('')}</div></div>`;
  }
  if (task.id === 'pr-report') {
    const report = data.latest_report;
    return `<div class="panel"><div class="panel-heading"><h2>最近日报</h2>${report?.status === 'sent' ? badge('success') : badge('unknown')}</div><div class="business-card"><div class="business-label">${report ? esc(report.date) + ' 审查活动' : '暂无日报'}</div><div class="business-value">${number(report?.pr_count)}<small>个 PR</small></div><div class="secondary">发送时间 ${fullTime(report?.sent_at)}</div><div class="mini-stats"><div class="mini-stat">表内累计 Token<strong>${tokens(report?.total_tokens)}</strong></div><div class="mini-stat">用量覆盖<strong style="font-size:14px">${report?.partial_usage ? '部分缺失' : report ? '已有记录' : '未知'}</strong></div></div></div>${report ? `<div class="report-list"><table><tbody>${report.rows.map((row) => `<tr><td><a href="https://github.com/labring/FastGPT/pull/${encodeURIComponent(row.pr)}" target="_blank" rel="noreferrer">#${esc(row.pr)}</a></td><td title="${esc(row.title)}">${esc(row.title?.slice(0, 48))}</td><td>${tokens(row.total_tokens)}</td></tr>`).join('')}</tbody></table></div>` : ''}</div>`;
  }
  if (task.id === 'pr-scan' || task.id === 'pr-worker') {
    const counts = data.queue_counts ?? {};
    return `<div class="panel"><div class="panel-heading"><h2>PR 队列快照</h2><span class="count-pill">#${data.scan_id ?? '—'}</span></div><div class="business-card"><div class="business-label">最新扫描 ${fullTime(data.last_scan_at)}</div><div class="mini-stats" style="border:0;margin-top:0;padding-top:5px"><div class="mini-stat">已处理<strong>${counts.processed ?? 0}</strong></div><div class="mini-stat">待执行 / 执行中<strong>${(counts.queued ?? 0) + (counts.running ?? 0)}</strong></div><div class="mini-stat">处理异常<strong>${counts.error ?? 0}</strong></div></div><div class="mini-stats"><div class="mini-stat">最大并发<strong>${data.scheduling?.max_concurrent ?? 2}</strong></div><div class="mini-stat">静置窗口<strong>10 分钟</strong></div><div class="mini-stat">检查新提交<strong>15 秒</strong></div></div>${data.queue_items?.length ? data.queue_items.map((item) => `<div class="backup-item"><span>PR #${esc(item.pr)}</span><span>${esc(({ queued: '队列等待', running: '执行中', error: '异常' })[item.status] ?? item.status)}</span></div>`).join('') : '<div class="secondary" style="margin-top:20px">当前没有待处理或执行中的 PR。</div>'}</div></div>`;
  }
  return `<div class="panel"><div class="panel-heading"><h2>记录范围</h2></div><div class="business-card notes">自动发现的系统定时任务。展示 launchd 当前状态，尚未接入逐次执行记录。</div></div>`;
}

function renderLogs(taskId) {
  const result = state.logs.get(taskId);
  if (!result) return empty('正在读取日志…');
  return result.items.map((item) => `<div class="log-panel"><div class="log-title">${esc(item.path)}${item.truncated ? ' · 仅显示末尾 48 KB' : ''}</div><pre>${esc(item.text || '日志为空')}</pre></div>`).join('');
}

async function loadLogs(taskId) {
  try {
    const result = await api('/api/tasks/' + encodeURIComponent(taskId) + '/logs');
    state.logs.set(taskId, result);
    const target = document.querySelector('#task-logs');
    if (target && currentTaskId() === taskId) target.innerHTML = renderLogs(taskId);
  } catch (error) {
    toast('日志读取失败：' + error.message);
  }
}

function detail(task) {
  return `<a href="#overview" class="back-link">${icon('back')}返回任务总览</a><div class="page-heading"><div><div class="detail-heading"><span class="task-icon">${icon(task.icon)}</span><div><div class="eyebrow">TASK DETAILS</div><h1>${esc(task.name)}</h1></div>${badge(task.state)}</div><p class="subtitle detail-description">${esc(task.description)}</p></div></div><div class="detail-grid"><section class="panel"><div class="panel-heading"><h2>任务信息</h2><span class="count-pill">只读</span></div><dl class="info-list">${infoRow('调度来源', task.source === 'codex-script' ? 'Codex 定时触发本地脚本' : 'macOS launchd')}${infoRow('执行计划', esc(task.schedule.text) + ' · 北京时间')}${infoRow('下次执行', task.next_at ? fullTime(task.next_at) + (task.next_estimated ? '（估计）' : '（计划）') : '由调度器决定')}${infoRow('记录方式', task.instrumented ? '已接入执行入口 · 开始 / 结束 / 退出码 / 日志' : task.source === 'codex-script' ? '持续导入已有执行记录' : '读取现有状态')}${infoRow('入口脚本', esc(task.script), true)}${infoRow('调度配置', esc(task.definition_path), true)}${infoRow('launchd 状态', task.source === 'codex-script' ? '外部调度' : (task.loaded ? '已加载' : '未加载') + (task.pid ? ` · PID ${task.pid}` : ' · 当前无运行进程'))}${infoRow('最近退出码', esc(task.last_exit_code ?? '暂无调度器凭据'))}</dl></section><div>${business(task)}</div></div>${historyPanel(task.id)}<section class="panel"><div class="panel-heading"><div><h2>现有日志</h2><p class="panel-description">原脚本累计日志，可能包含已恢复的历史错误；每次执行的独立日志在上方记录中查看。</p></div><button class="button" data-logs="${esc(task.id)}">${icon('history')}${state.shownLogs.has(task.id) ? '刷新日志' : '查看日志'}</button></div><div class="logs-area" id="task-logs">${state.shownLogs.has(task.id) ? renderLogs(task.id) : '<p class="notes">按需读取现有日志，敏感凭据会隐藏。</p>'}</div></section>`;
}

function services() {
  const items = state.data.collector.services ?? [];
  const tasks = state.data.tasks.filter((task) => task.category === 'system');
  return `${heading('后台服务', '观察常驻服务的运行情况，与业务定时任务分开展示。')}<div class="service-grid">${items.map((service) => `<section class="service-card"><div class="service-top"><h2>${esc(service.name)}</h2>${badge(service.state)}</div><p>${esc(service.description)}</p><div class="service-meta"><span>PID<strong>${service.pid ?? '—'}</strong></span><span>最近退出码<strong>${service.last_exit_code ?? '—'}</strong></span><span>加载<strong>${service.loaded ? '是' : '否'}</strong></span></div><div class="secondary mono" style="margin-top:14px">${esc(service.label)}</div></section>`).join('')}</div>${tasks.length ? `<h2 class="system-heading">其他系统定时任务</h2><section class="panel">${taskTable(tasks)}</section>` : ''}<p class="notes" style="margin-top:20px">常驻服务的最近退出码是历史退出结果，当前是否运行以进程状态为准。</p>`;
}

function currentTaskId() {
  return location.hash.startsWith('#task/') ? decodeURIComponent(location.hash.slice(6)) : '';
}

/** 自动刷新时恢复输入焦点和光标，避免搜索与筛选被刷新打断。 */
function render() {
  if (!state.data) return;
  const calendarScroll = document.querySelector('#calendar-scroll');
  const calendarPosition = calendarScroll ? [calendarScroll.scrollLeft,calendarScroll.scrollTop] : null;
  const focused = document.activeElement;
  const focusId = focused?.id;
  const selection = focused instanceof HTMLInputElement ? [focused.selectionStart, focused.selectionEnd] : null;
  const taskId = currentTaskId();
  const route = taskId ? 'task' : location.hash.slice(1) || 'overview';
  document.querySelectorAll('[data-nav]').forEach((node) => node.classList.toggle('active', node.dataset.nav === (route === 'task' ? 'overview' : route)));
  document.querySelector('#breadcrumb').textContent = route === 'task' ? taskName(taskId) : ({ overview: '任务总览', history: '执行记录', services: '后台服务', calendar: '任务日历' })[route] ?? '任务总览';
  if (route === 'history') main.innerHTML = heading('执行记录', '每一次运行的结果，都有据可查。', 'MAC MINI / RUN HISTORY') + historyPanel();
  else if (route === 'calendar') main.innerHTML = calendarPage();
  else if (route === 'services') main.innerHTML = services();
  else if (route === 'task') {
    const task = state.data.tasks.find((item) => item.id === taskId);
    main.innerHTML = task ? detail(task) : heading('任务不存在', '该任务可能已移除，返回总览查看当前任务。') + '<a class="button" href="#overview">返回总览</a>';
  } else main.innerHTML = overview();
  if (route === 'history' || route === 'task') {
    if (state.history && state.route === location.hash) document.querySelector('#history-content')?.replaceChildren();
    loadHistory(taskId);
    if (taskId && state.shownLogs.has(taskId)) loadLogs(taskId);
  }
  if (route === 'calendar') {
    if(calendarPosition){const viewport=document.querySelector('#calendar-scroll');viewport.scrollLeft=calendarPosition[0];viewport.scrollTop=calendarPosition[1];}
    loadCalendar();
  }
  state.route = location.hash;
  if (focusId) {
    const replacement = document.getElementById(focusId);
    replacement?.focus({ preventScroll: true });
    if (selection && replacement instanceof HTMLInputElement) replacement.setSelectionRange(...selection);
  }
}

async function refresh(manual = false) {
  if (state.loading) return;
  state.loading = true;
  const button = document.querySelector('#refresh');
  button.disabled = true;
  try {
    state.data = await api('/api/overview');
    const checked = state.data.collector.checked_at;
    const stale = Date.now() / 1000 - (checked ?? 0) > 60;
    const status = document.querySelector('#sync-status');
    status.classList.toggle('error', stale);
    status.innerHTML = `<i class="live-dot"></i>${stale ? '采集心跳已延迟' : '已同步 ' + shortTime(checked)}`;
    document.querySelector('#host-name').textContent = state.data.collector.hostname ?? 'Mac mini';
    document.querySelector('#history-count').textContent = number(state.data.stats.total_records);
    document.querySelector('#footer-info').textContent = `${state.data.stats.execution_records} 条实时采集 · 每 15 秒同步`;
    render();
    if (manual) toast('数据已同步');
  } catch (error) {
    const status = document.querySelector('#sync-status');
    status.className = 'error';
    status.innerHTML = '<i class="live-dot"></i>连接暂时中断';
    if (!state.data) main.innerHTML = `<div class="error-page"><h1>暂时无法读取监控数据</h1><p>${esc(error.message)}</p><button class="button" id="retry">重新连接</button></div>`;
    if (manual) toast('同步失败：' + error.message);
  } finally {
    state.loading = false;
    button.disabled = false;
  }
}

async function showRun(id, reopen = true) {
  try {
    const run = await api('/api/runs/' + encodeURIComponent(id));
    state.dialogRun = id;
    document.querySelector('#dialog-content').innerHTML = `<div class="dialog-heading"><div><div class="eyebrow">RUN DETAILS</div><h2 id="dialog-title">${esc(taskName(run.task_id))}</h2></div><button class="icon-button" id="close-dialog" aria-label="关闭详情">${icon('close')}</button></div><div class="dialog-body">${badge(run.status)}<p class="dialog-summary">${esc(run.summary)}</p><dl class="info-list">${infoRow('记录来源', sourceLabel(run))}${infoRow(run.started_at === null ? '记录时间' : '开始时间', fullTime(run.started_at ?? run.created_at))}${infoRow('结束时间', fullTime(run.finished_at))}${infoRow('执行耗时', run.status === 'running' ? '执行中' : duration(run.duration))}${infoRow('退出码', esc(run.exit_code ?? '无原始凭据'))}${Object.entries(run.details).filter(([, value]) => value !== null && value !== undefined).map(([key, value]) => infoRow(key, esc(typeof value === 'boolean' ? value ? '是' : '否' : typeof value === 'object' ? JSON.stringify(value) : value))).join('')}</dl>${run.kind === 'history' ? '<div class="dialog-source">这是已有文件中的历史凭据。缺失的时间与退出码保持未知，不代表零耗时或完整执行历史。</div>' : `<div class="panel-heading" style="padding:18px 0 0"><h2>本次执行日志</h2><button class="button" id="refresh-dialog">${icon('refresh')}刷新记录</button></div><div class="log-panel"><pre>${esc(run.log_tail || (run.status === 'running' ? '任务已开始，等待输出…' : '本次执行没有控制台输出'))}</pre></div><p class="dialog-source">保存脱敏后的末尾 24 KB 日志；原始日志仍位于任务自己的日志文件中。</p>`}</div>`;
    if (reopen && !dialog.open) dialog.showModal();
  } catch (error) {
    toast('记录读取失败：' + error.message);
  }
}

document.querySelectorAll('[data-icon]').forEach((node) => { node.innerHTML = icon(node.dataset.icon); });
document.querySelector('#refresh').addEventListener('click', () => refresh(true));
window.addEventListener('hashchange', () => { state.offset = 0; state.history = null; render(); window.scrollTo(0, 0); });
document.addEventListener('click', (event) => {
  const target = event.target;
  if (!(target instanceof Element)) return;
  const calendarControl = target.closest('[data-cal-day], [data-cal-mode], [data-cal-shift], [data-cal-today], [data-cal-agenda]');
  if (calendarControl) {
    if(calendarControl.hasAttribute('data-cal-agenda')) {document.querySelector('.cal-agenda')?.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth'});return;}
    if (calendarControl.hasAttribute('data-cal-today')) {calendarState.selected=MiniCalendar.dateKey(state.data.now);calendarState.start=MiniCalendar.weekStart(calendarState.selected);calendarState.data=null;calendarState.followDay=true;updateCalendar();loadCalendar();}
    else if (calendarControl.dataset.calShift) {calendarState.start=MiniCalendar.addDays(calendarState.start,Number(calendarControl.dataset.calShift));calendarState.selected=MiniCalendar.addDays(calendarState.selected,Number(calendarControl.dataset.calShift));calendarState.data=null;calendarState.followDay=true;updateCalendar();loadCalendar();}
    else {if(calendarControl.dataset.calDay)calendarState.selected=calendarControl.dataset.calDay;if(calendarControl.dataset.calMode)calendarState.mode=calendarControl.dataset.calMode;updateCalendar();}
    return;
  }
  if (target.closest('#retry')) { refresh(true); return; }
  if (target.closest('#close-dialog')) { dialog.close(); return; }
  if (target.closest('#refresh-dialog')) { showRun(state.dialogRun, false); return; }
  const run = target.closest('[data-run]');
  if (run) { showRun(run.dataset.run); return; }
  const task = target.closest('[data-task]');
  if (task && !target.closest('a')) { location.hash = taskHref(task.dataset.task).slice(1); return; }
  const logs = target.closest('[data-logs]');
  if (logs) { state.shownLogs.add(logs.dataset.logs); logs.innerHTML = icon('refresh') + '刷新日志'; loadLogs(logs.dataset.logs); return; }
  const page = target.closest('[data-page]');
  if (page && !page.disabled) { state.offset = Math.max(0, state.offset + (page.dataset.page === 'next' ? 30 : -30)); loadHistory(currentTaskId()); }
});
document.addEventListener('keydown', (event) => {
  if ((event.key === 'Enter' || event.key === ' ') && event.target instanceof Element && event.target.matches('[data-run]')) {
    event.preventDefault(); showRun(event.target.dataset.run);
  }
});
document.addEventListener('change', (event) => {
  const target = event.target;
  if (target.id === 'calendar-task') {calendarState.task=target.value;updateCalendar();return;}
  if (target instanceof HTMLSelectElement && target.dataset.filter) { state.filters[target.dataset.filter] = target.value; state.offset = 0; loadHistory(currentTaskId()); }
});
document.addEventListener('input', (event) => {
  if (event.target.id === 'task-search') {
    state.taskSearch = event.target.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      const table = document.querySelector('#task-rows');
      if (table) table.innerHTML = taskRows(state.data.tasks.filter((task) => task.category === 'work' && (task.name + task.description).toLowerCase().includes(state.taskSearch.toLowerCase())));
    }, 120);
  }
});
dialog.addEventListener('click', (event) => { if (event.target === dialog) { const box = dialog.getBoundingClientRect(); if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.close(); } });
refresh();
setInterval(() => { if (!document.hidden) refresh(); }, 15000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
