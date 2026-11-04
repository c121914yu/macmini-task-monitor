import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readUsage } from './browser.mjs';
import { buildCard, shanghaiDate, shouldSkip, redact } from './flow.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));

/** 同目录原子写入，认证和执行凭据不向其他用户开放。 */
async function save(file, data) {
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temp = file + '.' + process.pid + '.tmp';
  await fs.writeFile(temp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
  await fs.rename(temp, file);
}

/** 一次请求发送指定 webhook；响应未知时保留 unknown，禁止盲目重复通知。 */
export async function sendCard(config, card) {
  const target = new URL(config.webhook_url);
  if (target.origin !== 'https://open.feishu.cn' ||
      !target.pathname.startsWith('/open-apis/bot/v2/hook/')) throw new Error('未授权的通知地址');
  const response = await fetch(target, { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(card),
    signal: AbortSignal.timeout(15000), redirect: 'error' });
  const body = await response.json();
  if (!response.ok || body.code !== 0) {
    const error = new Error('飞书明确拒绝通知，HTTP ' + response.status + '，code ' + body.code);
    error.definiteRejection = body.code !== undefined;
    throw error;
  }
  return { code: body.code };
}

/** 锁由 run_check.py 管理；先查发送凭据，再采集真实流量，发送前写意图避免中断后重复。 */
export async function execute({ root = ROOT, checkOnly = false, read = readUsage, send = sendCard,
                                now = new Date() } = {}) {
  process.umask(0o077);
  const configPath = path.join(root, 'config.json');
  const mode = (await fs.stat(configPath)).mode & 0o077;
  if (mode) throw new Error('config.json 权限必须为 600');
  const config = JSON.parse(await fs.readFile(configPath, 'utf8'));
  const day = shanghaiDate(now);
  const ledgerPath = path.join(root, 'checks', day + '.json');
  let previous;
  try { previous = JSON.parse(await fs.readFile(ledgerPath, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT') throw error; }
  if (!checkOnly && shouldSkip(previous)) {
    return { event: 'flowercloud_skipped', date: day, status: previous.status,
      summary: '当天已有发送凭据，本次未重复发送' };
  }
  let record = { date: day, started_at: new Date().toISOString(), status: 'reading' };
  try {
    const usage = await read(config, root);
    record = { ...record, usage, status: 'checked' };
    const card = buildCard(usage, day);
    if (checkOnly) {
      await save(path.join(root, 'verification', 'check-' + Date.now() + '.json'), { ...record, card });
      return { event: 'flowercloud_finished', mode: 'check_only', notified: false, ...usage,
        summary: '已用 ' + usage.used_display + ' / ' + usage.total_display + '，只读检查' };
    }
    record.status = 'sending';
    await save(ledgerPath, record);
    try { await send(config, card); }
    catch (error) {
      record.status = error.definiteRejection ? 'failed' : 'unknown';
      throw error;
    }
    record.status = 'sent';
    record.sent_at = new Date().toISOString();
    record.finished_at = record.sent_at;
    await save(ledgerPath, record);
    return { event: 'flowercloud_finished', mode: 'scheduled', date: day, notified: true,
      ...usage, summary: '已用 ' + usage.used_display + ' / ' + usage.total_display + '，飞书已发送' };
  } catch (error) {
    if (['sent', 'sending'].includes(record.status)) record.status = 'unknown';
    else if (!['unknown', 'failed'].includes(record.status)) record.status = 'failed';
    record.error = redact(error.message || error, config).slice(0, 1200);
    record.finished_at = new Date().toISOString();
    const file = checkOnly ? path.join(root, 'verification', 'failed-' + Date.now() + '.json') : ledgerPath;
    await save(file, record);
    throw new Error(record.error);
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  try { console.log(JSON.stringify(await execute({ checkOnly: process.argv.includes('--check-only') }))); }
  catch (error) {
    console.error(JSON.stringify({ event: 'flowercloud_failed', error: String(error.message).slice(0, 1200) }));
    process.exitCode = 1;
  }
}
