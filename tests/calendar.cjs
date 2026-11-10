/** Date boundaries and safe initial rendering, without browser or network dependencies. */
const assert=require('node:assert/strict');
require('../web/calendar.js');
const c=globalThis.MiniCalendar;
assert.equal(c.weekStart('2027-01-01'),'2026-12-28');
assert.equal(c.addDays('2028-02-28',1),'2028-02-29');
assert.equal(c.addDays('2028-02-29',1),'2028-03-01');
assert.equal(c.dateKey(Date.parse('2026-11-10T16:00:00Z')/1000),'2026-11-11');
assert.equal(c.minute(Date.parse('2026-11-10T23:30:00Z')/1000),450);
const esc=s=>String(s??'').replace(/[&<>"']/g,v=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[v]));
const h={esc,icon:()=>'',badge:esc,taskHref:id=>'#task/'+encodeURIComponent(id),duration:()=>'',labels:{success:'成功'}};
const ui={start:'2026-11-09',selected:'2026-11-11',mode:'planned',task:''};
assert.match(c.view(null,[],ui,h),/正在读取日历/);
const tasks=[{id:'x',name:'<img src=x onerror=alert(1)>',category:'work',schedule:{text:'每天07:00'}}];
const data={now:Date.parse('2026-11-11T00:00:00Z')/1000,days:{'2026-11-11':{planned:[{task_id:'x',at:Date.parse('2026-11-10T23:00:00Z')/1000,loaded:true}],actual:[],recurring:{}}},recurring_ids:[]};
const html=c.view(data,tasks,ui,h);
assert.ok(!html.includes('<img'));
assert.ok(html.includes('&lt;img'));
console.log('PASS 北京时间、跨年周、闰日、加载状态与任务名称转义');
