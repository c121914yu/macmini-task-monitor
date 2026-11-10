/** 在 DOM 模拟器中检查真实接口驱动的页面导航、筛选与日志；不控制本机浏览器。 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const dependencyRoot = '/Volumes/code/dev/FastGPT/node_modules/.pnpm';
const installed = fs.existsSync(dependencyRoot) ? fs.readdirSync(dependencyRoot).find((entry) => entry.startsWith('jsdom@')) : null;
const { JSDOM, VirtualConsole } = require(process.env.JSDOM_PATH ?? (installed ? path.join(dependencyRoot, installed, 'node_modules/jsdom') : 'jsdom'));
const base = process.env.MONITOR_URL ?? 'http://127.0.0.1:8765';
const errors = [];
const virtualConsole = new VirtualConsole();
virtualConsole.on('jsdomError', (error) => errors.push(error.message));

async function until(callback, message) {
  const started = Date.now();
  while (!callback()) {
    if (Date.now() - started > 12000) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 40));
  }
}

(async () => {
  const source = await fetch(base + '/api/overview').then((response) => response.json());
  const expectedBalance = source.tasks.find((task) => task.id === 'metaso-balance').data.balance;
  const expectedBackupFiles = source.tasks.find((task) => task.id === 'fastgpt-backup').data.latest_backup.file_count;
  const dom = await JSDOM.fromURL(base, {
    runScripts: 'dangerously', resources: 'usable', pretendToBeVisual: true, virtualConsole,
    beforeParse(window) {
      window.fetch = (url, options) => fetch(new URL(url, window.location.href), options);
      window.AbortSignal = globalThis.AbortSignal;
      window.scrollTo = () => {};
      window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
      window.HTMLDialogElement.prototype.close = function () { this.open = false; };
    }
  });
  const w = dom.window;
  const document = w.document;
  const change = (select, value) => { select.value = value; select.dispatchEvent(new w.Event('change', { bubbles: true })); };
  try {
    const workTasks = source.tasks.filter((task) => task.category !== 'system');
    await until(() => document.querySelectorAll('#task-rows tr').length === workTasks.length, '总览业务任务数量与接口不一致');
    assert.equal(document.querySelector('h1').textContent, '任务总览');
    assert.equal(document.querySelectorAll('.stats .stat').length, 4);
    assert.ok(document.querySelector('#sync-status').textContent.includes('已同步'));
    assert.ok(source.recent.every((run) => run.status !== 'skipped'));

    const search = document.querySelector('#task-search');
    search.value = 'PR'; search.dispatchEvent(new w.Event('input', { bubbles: true }));
    const prTasks = workTasks.filter((task) => (task.name + ' ' + task.description).toLowerCase().includes('pr'));
    await until(() => document.querySelectorAll('#task-rows tr').length === prTasks.length, '任务搜索无效');
    console.log('PASS 总览、统计、任务搜索');

    w.location.hash = '#task/metaso-balance';
    await until(() => document.querySelector('h1')?.textContent === '秘塔搜索余额' && document.querySelectorAll('#history-content tbody tr').length > 0, '秘塔详情未加载');
    assert.ok(document.querySelector('.business-value').textContent.includes(new Intl.NumberFormat('en-US').format(expectedBalance)));
    document.querySelector('[data-logs]').click();
    await until(() => (document.querySelector('#task-logs pre')?.textContent.trim().length ?? 0) > 0, '原始日志未加载');
    assert.ok(!document.querySelector('#task-logs').textContent.includes('FEISHU_WEBHOOK_URL'));
    console.log('PASS 秘塔详情、余额、历史记录、按需读取日志');

    w.location.hash = '#history';
    await until(() => document.querySelector('h1')?.textContent === '执行记录' && document.querySelector('#history-content tbody'), '记录页面未加载');
    assert.equal(document.querySelector('[data-filter="status"]').value, 'non_skipped');
    assert.equal(document.querySelectorAll('#history-content .badge.skipped').length, 0);
    change(document.querySelector('[data-filter="status"]'), 'success');
    await until(() => document.querySelectorAll('#history-content tbody tr').length > 0 && [...document.querySelectorAll('#history-content tbody .badge')].every((node) => node.classList.contains('success')), '结果筛选无效');
    change(document.querySelector('[data-filter="status"]'), 'skipped');
    await until(() => document.querySelectorAll('#history-content tbody tr').length > 0 && [...document.querySelectorAll('#history-content tbody .badge')].every((node) => node.classList.contains('skipped')), '手动查看跳过记录无效');
    change(document.querySelector('[data-filter="kind"]'), 'execution');
    await until(() => document.querySelectorAll('#history-content tbody tr').length > 0 && [...document.querySelectorAll('#history-content tbody tr')].every((node) => node.textContent.includes('实时采集')), '来源筛选无效');
    document.querySelector('#history-content [data-run]').click();
    await until(() => document.querySelector('#run-dialog').open, '执行详情未打开');
    assert.ok(document.querySelector('#dialog-content').textContent.includes('本次执行日志'));
    assert.ok(document.querySelector('#dialog-content pre').textContent.length > 0);
    document.querySelector('#close-dialog').click();
    assert.equal(document.querySelector('#run-dialog').open, false);
    console.log('PASS 默认隐藏跳过、手动查看跳过记录、结果/来源筛选、详情弹层、逐次日志');

    change(document.querySelector('[data-filter="kind"]'), '');
    change(document.querySelector('[data-filter="status"]'), '');
    change(document.querySelector('[data-filter="days"]'), '0');
    await until(() => document.querySelector('#history-content tbody tr') && !document.querySelector('[data-page="next"]').disabled, '分页未显示');
    document.querySelector('[data-page="next"]').click();
    await until(() => document.querySelector('.pagination').textContent.includes('当前 31'), '下一页失效');
    console.log('PASS 历史分页');

    w.location.hash = '#task/fastgpt-backup';
    await until(() => document.querySelector('h1')?.textContent === 'FastGPT 数据备份', '备份详情未加载');
    assert.match(document.querySelector('.business-value').textContent, /(?:MB|GB)/);
    assert.ok(document.querySelector('.business-card').textContent.includes(String(expectedBackupFiles)));
    w.location.hash = '#task/pr-report';
    await until(() => document.querySelector('h1')?.textContent === 'PR 审查日报', '日报详情未加载');
    assert.ok(document.querySelector('.report-list tbody tr'));
    w.location.hash = '#calendar';
    await until(() => document.querySelectorAll('.cal-day').length === 7 && document.querySelector('.cal-event'), '周日历未加载');
    assert.equal(document.querySelectorAll('.cal-hour-row').length, 24);
    assert.equal([...document.querySelectorAll('.cal-day')].filter(node=>node.getAttribute('aria-pressed')==='true').length, 1);
    assert.ok(document.querySelector('.cal-recurring-grid').textContent.includes('PR 扫描'));
    assert.ok(!document.querySelector('.cal-grid').textContent.includes('PR 扫描'));
    const previousWeek = document.querySelector('.cal-date-controls h2').textContent;
    document.querySelector('[data-cal-shift="7"]').click();
    await until(() => document.querySelector('.cal-date-controls h2').textContent !== previousWeek && document.querySelector('.cal-event'), '周切换未加载');
    document.querySelector('[data-cal-today]').click();
    await until(() => document.querySelector('.cal-date-controls h2').textContent === previousWeek && document.querySelector('.cal-event'), '回到今天无效');
    change(document.querySelector('#calendar-task'), 'pr-report');
    assert.ok([...document.querySelectorAll('.cal-event')].every(node => node.textContent.includes('PR 审查日报')));
    document.querySelector('[data-cal-day]').click();
    assert.equal(document.querySelectorAll('.cal-day[aria-pressed="true"]').length, 1);
    change(document.querySelector('#calendar-task'), '');
    document.querySelector('[data-cal-mode="actual"]').click();
    assert.equal(document.querySelector('[data-cal-mode="actual"]').getAttribute('aria-pressed'), 'true');
    const calendarRun = document.querySelector('.cal-event[data-run]');
    if (calendarRun) {
      calendarRun.click();
      await until(() => document.querySelector('#run-dialog').open, '日历执行记录详情未打开');
      document.querySelector('#close-dialog').click();
    }
    console.log('PASS 周日历、持续调度折叠、日期切换、任务筛选、实际执行与详情');
    w.location.hash = '#services';
    await until(() => document.querySelectorAll('.service-card').length === source.collector.services.length, '服务状态未显示');
    assert.ok(document.querySelector('.system-heading').textContent.includes('系统定时任务'));
    assert.deepEqual(errors, []);
    console.log('PASS 备份产物、日报内容、常驻服务、系统定时任务；无前端运行错误');
  } finally {
    w.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
