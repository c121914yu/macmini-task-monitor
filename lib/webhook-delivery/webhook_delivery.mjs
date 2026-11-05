import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const script = path.join(path.dirname(fileURLToPath(import.meta.url)), 'webhook_delivery.py');

/** Node 任务复用唯一传输策略；敏感数据走 stdin，限流等待与尝试凭据由 Python helper 维护。 */
export function postWebhook(url, payload, { journalPath, initialAttempts = 0, notBefore } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn('/opt/homebrew/bin/python3', [script], { stdio: ['pipe', 'pipe', 'pipe'] });
    let output = '';
    child.stdout.on('data', data => { output = (output + data.toString('utf8')).slice(-32768); });
    // 不转发原始异常，避免第三方 URL 或请求数据意外进入任务日志。
    child.stderr.resume();
    child.on('error', () => reject(new Error('Webhook helper could not start')));
    child.on('close', () => {
      let result;
      try { result = JSON.parse(output.trim()); }
      catch { reject(new Error('Webhook helper result is unknown')); return; }
      if (result.status === 'sent') { resolve(result); return; }
      const error = new Error('Webhook delivery ' + result.status + ' (code ' + result.code + ')');
      error.deliveryStatus = result.status;
      error.code = result.code;
      error.attempts = result.attempts;
      error.definiteRejection = ['rejected', 'rate_limited'].includes(result.status);
      reject(error);
    });
    child.stdin.on('error', () => {});
    child.stdin.end(JSON.stringify({ url, payload, journal_path: journalPath, initial_attempts: initialAttempts, not_before: notBefore }));
  });
}
