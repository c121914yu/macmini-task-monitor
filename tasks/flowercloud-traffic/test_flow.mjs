import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { parseUsage, shanghaiDate, shouldSkip, buildCard, redact } from './flow.mjs';
import { execute } from './run.mjs';

test('真实页面格式、流量为零、逗号及超额均正确解析', () => {
  const sample = parseUsage('流量使用\n\n15.83GB / 150GB');
  assert.equal(sample.used, 15.83);
  assert.equal(sample.percent, 10.55);
  assert.equal(sample.remaining_display, '134.17 GB');
  assert.equal(parseUsage('0GB / 150GB').percent, 0);
  assert.equal(parseUsage('1,024.5GB / 2,000GB').used, 1024.5);
  assert.equal(parseUsage('160GB / 150GB').remaining_display, '0 GB');
});
test('缺失、零总量、多个指标拒绝；不同单位保留原值而不猜测换算', () => {
  for (const s of ['', '10GB / 0GB', '-1GB / 150GB', '1GB / 2GB 3GB / 4GB']) assert.throws(() => parseUsage(s));
  assert.equal(parseUsage('500MB / 150GB').percent, null);
});
test('北京时间边界和发送凭据状态', () => {
  assert.equal(shanghaiDate(new Date('2026-11-04T15:59:59Z')), '2026-11-04');
  assert.equal(shanghaiDate(new Date('2026-11-04T16:00:00Z')), '2026-11-05');
  for (const status of ['sent', 'sending', 'unknown']) assert.equal(shouldSkip({ status }), true);
  for (const status of ['failed', 'reading', 'checked']) assert.equal(shouldSkip({ status }), false);
});
test('卡片只带流量，凭据与 URL 认证参数脱敏', () => {
  const card = buildCard(parseUsage('15.83GB / 150GB'), '2026-11-04');
  assert.equal(card.msg_type, 'interactive');
  assert.match(JSON.stringify(card), /15.83 GB/);
  assert.match(JSON.stringify(card), /150 GB/);
  assert.doesNotMatch(JSON.stringify(card), /username|password|cookie/i);
  assert.equal(redact('abc pwd hook ?token=tok', { username: 'abc', password: 'pwd', webhook_url: 'hook' }),
    '[REDACTED] [REDACTED] [REDACTED] ?token=[REDACTED]');
});

async function fixture(fn) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'flowercloud-test-'));
  const config = { username: 'test', password: 'private', webhook_url: 'unused', product_url: 'unused' };
  await fs.writeFile(path.join(root, 'config.json'), JSON.stringify(config), { mode: 0o600 });
  try { await fn(root); } finally { await fs.rm(root, { recursive: true, force: true }); }
}
test('真实目录凭据去重，第二次不打开浏览器也不重复推送', async () => {
  await fixture(async root => {
    let reads = 0, sends = 0;
    const options = { root, now: new Date('2026-11-04T01:00:00Z'),
      read: async () => { reads++; return parseUsage('15.83GB / 150GB'); },
      send: async () => { sends++; } };
    assert.equal((await execute(options)).notified, true);
    assert.equal((await execute(options)).event, 'flowercloud_skipped');
    assert.equal(reads, 1); assert.equal(sends, 1);
    const ledger = JSON.parse(await fs.readFile(path.join(root, 'checks/2026-11-04.json')));
    assert.equal(ledger.status, 'sent');
    assert.doesNotMatch(JSON.stringify(ledger), /private/);
  });
});
test('推送响应丢失保留 unknown，不能下一次盲目重发', async () => {
  await fixture(async root => {
    let sends = 0;
    const options = { root, now: new Date('2026-11-04T01:00:00Z'),
      read: async () => parseUsage('15GB / 150GB'),
      send: async () => { sends++; throw new Error('transport interrupted'); } };
    await assert.rejects(execute(options), /transport/);
    assert.equal((await execute(options)).status, 'unknown');
    assert.equal(sends, 1);
  });
});
test('只读检查不发通知、不写正式账本；查询失败不伪造零流量', async () => {
  await fixture(async root => {
    const result = await execute({ root, checkOnly: true,
      read: async () => parseUsage('15GB / 150GB'), send: async () => assert.fail('unexpected webhook') });
    assert.equal(result.notified, false);
    await assert.rejects(fs.stat(path.join(root, 'checks')));
    await assert.rejects(execute({ root, read: async () => { throw new Error('login failed'); },
      send: async () => assert.fail('unexpected webhook') }), /login failed/);
  });
});

test('明确拒绝保留 failed，后续执行可以重新发送', async () => {
  await fixture(async root => {
    let sends = 0;
    const options = { root, now: new Date('2026-11-04T01:00:00Z'),
      read: async () => parseUsage('15GB / 150GB'),
      send: async () => { sends++; if (sends === 1) {
        const error = new Error('rejected'); error.definiteRejection = true; throw error;
      } } };
    await assert.rejects(execute(options), /rejected/);
    assert.equal((await execute(options)).notified, true);
    assert.equal(sends, 2);
  });
});
test('推送后持久化失败仍保留发送意图，避免下一轮重复通知', async () => {
  await fixture(async root => {
    const checks = path.join(root, 'checks');
    const options = { root, now: new Date('2026-11-04T01:00:00Z'),
      read: async () => parseUsage('15GB / 150GB'),
      send: async () => { await fs.chmod(checks, 0o500); } };
    try { await assert.rejects(execute(options)); }
    finally { await fs.chmod(checks, 0o700); }
    assert.equal((await execute({ ...options, send: async () => assert.fail('duplicate') })).event,
      'flowercloud_skipped');
  });
});
