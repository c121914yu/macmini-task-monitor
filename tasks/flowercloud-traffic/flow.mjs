/** FlowerCloud 流量归一化与通知数据；纯函数不访问浏览器、网络或凭据。 */
export function parseUsage(text) {
  const number = '(?:\\d{1,3}(?:,\\d{3})+|\\d+)(?:\\.\\d+)?';
  const unit = '(?:[KMGT]i?B|B)';
  const pattern = new RegExp('^\\s*(' + number + ')\\s*(' + unit + ')\\s*/\\s*(' + number + ')\\s*(' + unit + ')\\s*$', 'gim');
  const matches = [...String(text).matchAll(pattern)];
  if (matches.length !== 1) throw new Error('流量区域缺少唯一的已用/总量数值');
  const [, usedText, usedUnit, totalText, totalUnit] = matches[0];
  const used = Number(usedText.replaceAll(',', ''));
  const total = Number(totalText.replaceAll(',', ''));
  if (!Number.isFinite(used) || !Number.isFinite(total) || used < 0 || total <= 0) {
    throw new Error('流量数值无效');
  }
  const sameUnit = usedUnit.toUpperCase() === totalUnit.toUpperCase();
  return {
    used, total, used_unit: usedUnit, total_unit: totalUnit,
    used_display: usedText + ' ' + usedUnit, total_display: totalText + ' ' + totalUnit,
    percent: sameUnit ? Number((used / total * 100).toFixed(2)) : null,
    remaining_display: sameUnit ? Number(Math.max(0, total - used).toFixed(2)) + ' ' + totalUnit : null,
  };
}

/** 日报按北京时间归属；服务器或进程时区变化不影响同日去重。 */
export function shanghaiDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const values = Object.fromEntries(parts.map(p => [p.type, p.value]));
  return values.year + '-' + values.month + '-' + values.day;
}

/** 同日发送成功或结果不确定时停止重复发送；明确失败可由人工或下轮任务重试。 */
export function shouldSkip(record) {
  return ['sent', 'sending', 'unknown'].includes(record?.status);
}

/** 卡片仅包含用户指定的流量数据，不携带账号、认证或订阅信息。 */
export function buildCard(usage, date) {
  const lines = [
    '**已用流量：** ' + usage.used_display,
    '**套餐总量：** ' + usage.total_display,
  ];
  if (usage.remaining_display !== null) lines.push('**剩余流量：** ' + usage.remaining_display);
  if (usage.percent !== null) lines.push('**使用比例：** ' + usage.percent + '%');
  return { msg_type: 'interactive', card: {
    config: { wide_screen_mode: true },
    header: { template: 'blue', title: { tag: 'plain_text', content: 'FlowerCloud 流量日报' } },
    elements: [
      { tag: 'div', text: { tag: 'lark_md', content: lines.join('\n') } },
      { tag: 'note', elements: [{ tag: 'plain_text', content: date + ' · Mac mini 自动读取' }] },
    ],
  } };
}

/** 错误日志去除本任务凭据及可能存在于 URL 的认证参数。 */
export function redact(message, config) {
  let value = String(message);
  for (const field of ['username', 'password', 'webhook_url']) {
    if (config[field]) value = value.split(config[field]).join('[REDACTED]');
  }
  return value.replace(/([?&](?:token|key|secret|password)=)[^&\s]+/gi, '$1[REDACTED]');
}
