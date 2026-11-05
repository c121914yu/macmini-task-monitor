import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { readUsage } from './browser.mjs';
import { randomUUID } from 'node:crypto';
import { postWebhook } from '/Users/yjl/.local/share/webhook-delivery/webhook_delivery.mjs';
import { buildCard, shanghaiDate, shouldSkip, redact } from './flow.mjs';

const ROOT = path.dirname(fileURLToPath(import.meta.url));

/** 同目录原子写入，认证和执行凭据不向其他用户开放。 */
async function save(file, data) {
  await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
  const temp = file + '.' + process.pid + '.tmp';
  await fs.writeFile(temp, JSON.stringify(data, null, 2) + '\n', { mode: 0o600 });
  await fs.rename(temp, file);
}

/** 所有 webhook 共用固定 60 秒、最多 3 次重试的传输策略。 */
export async function sendCard(config, card, options = {}) {
  return postWebhook(config.webhook_url, card, options);
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
  let record = { date: day, started_at: new Date().toISOString(), status: 'reading',
    delivery_attempts: previous?.delivery_attempts || [] };
  try {
    const captured = previous?.usage?.captured_at;
    const sameDay = captured && shanghaiDate(new Date(captured)) === day;
    const usage = !checkOnly && previous?.status === 'failed' && sameDay
      ? previous.usage : await read(config, root);
    record = { ...record, usage, status: 'checked' };
    const card = buildCard(usage, day);
    if (checkOnly) {
      await save(path.join(root, 'verification', 'check-' + Date.now() + '.json'), { ...record, card });
      return { event: 'flowercloud_finished', mode: 'check_only', notified: false, ...usage,
        summary: '已用 ' + usage.used_display + ' / ' + usage.total_display + '，只读检查' };
    }
    record.status = 'sending';
    await save(ledgerPath, record);
    let initialAttempts = Math.max(0, ...record.delivery_attempts.map(x => x.attempt || 0));
    const priorLimit = /code[ =]+(?:11232|11233|11247)/.test(previous?.error || '');
    if (priorLimit && initialAttempts === 0) initialAttempts = 1;
    const last = record.delivery_attempts.at(-1);
    const notBefore = last?.next_attempt_at ??
      (priorLimit && previous?.finished_at ? Date.parse(previous.finished_at) / 1000 + 60 : undefined);
    record.delivery_journal = path.join(root, 'webhook-attempts', day + '-' + randomUUID() + '.json');
    await save(ledgerPath, record);
    try {
      const result = await send(config, card, {
        journalPath: record.delivery_journal, initialAttempts, notBefore,
      });
      if (result?.attempts) record.delivery_attempts.push(...result.attempts);
    }
    catch (error) {
      if (error.attempts) record.delivery_attempts.push(...error.attempts);
      record.status = error.deliveryStatus === 'rate_limited' ? 'rate_limited' :
        error.definiteRejection ? 'failed' : 'unknown';
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
    else if (!['unknown', 'failed', 'rate_limited'].includes(record.status)) record.status = 'failed';
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
