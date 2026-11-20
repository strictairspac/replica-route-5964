import {table} from '../components/common/table.js';
import {badge} from '../components/common/badge.js';
import {esc,date} from '../utils/html.js';
import {BOOKING_STATES,BOOKING_STRATEGIES,BOOKING_DECISIONS} from '../constants/bookings.js';

function bookingBadge(state){
  const label=BOOKING_STATES[state]||state||'—';
  return `<span class="badge booking-${esc(state||'none')}">${esc(label)}</span>`;
}

function decision(value){
  const label=BOOKING_DECISIONS[value]||value;
  const cls=['rejected','unavailable','expired','ignored'].includes(value)?'rejected':value==='pending'?'waited':'';
  return `<span class="${cls}">${esc(label)}</span>`;
}

export function bookingEvidencePage(run){
  if(!run)return '<div class="empty">还没有预约演练。先到「并发预约」页提交一次查空档与登记编排。</div>';
  const r=run.result,s=r?.summary;
  const confirmed=s?r.bookings.filter(b=>b.status==='confirmed'):[];
  const rejected=s?r.bookings.filter(b=>b.status==='rejected'):[];
  const projection=r?r.projection.confirmed:[];
  return `<div class="evidence-top"><div><span class="eyebrow">BOOKING EVIDENCE / #${run.id}</span><h2>${esc(run.name)} ${badge(run.status)}</h2>
  <p>${date(run.created_at)} · 策略：${esc(BOOKING_STRATEGIES[run.strategy])} · 输入已冻结</p></div>
  <button id="booking-export" data-id="${run.id}">导出 JSON ↓</button></div>
  ${run.error?`<div class="error">${esc(run.error)}。请调整编排后重新提交。</div>`:''}
  ${!r?'<div class="notice">后台重放中，每1.5秒刷新。可保留此页，或稍后从记录返回。</div>':
  `<div class="verdict"><div class="verdict-icon">${s.confirmed>0?'✓':'!'}</div>
  <div><span class="eyebrow">CONFLICT OUTCOME</span><h2>重叠时段成交 ${s.confirmed} 单 · 驳回 ${s.rejected} 单</h2>
  <p>${esc(BOOKING_STRATEGIES[r.strategy])} · 等待 ${s.waited_bookings} 单（共 ${s.total_waited_ms}ms） · 重做 ${s.redo_attempts} 次 · 幂等复用 ${s.duplicate_retries_blocked} 次</p></div>
  <div class="equation">${s.confirmed}<span>成交</span> / ${s.rejected}<span>驳回</span></div></div>
  <div class="policy-strip">占位有效期 ${r.policy.hold_ttl_ms}ms <span>／</span> 等待上限 ${r.policy.wait_max_ms}ms <span>／</span> 延迟门槛 ${r.policy.max_delay_ms}ms <span>／</span> 粘滞 ${r.policy.sticky_ms}ms</div>
  <div class="section-top"><h2>查空档 / 登记 / 重试 / 副本查询</h2><span>两步分开 · 幂等登记 · 只复制已成交</span></div>
  ${table(['时间 / 动作','会话','机房 / 时段','判定','依据'],r.trace.map(t=>[
    `${t.clock}<small>${esc(t.kind)}${t.synthetic?' · 自动':''}</small>`,
    esc(t.session||'—'),
    t.room?`${esc(t.room)}${t.start_ms!=null?`<small>${t.start_ms}–${t.end_ms}ms${t.attempt?' · attempt '+t.attempt:''}</small>`:''}`:'—',
    `${decision(t.decision)}${t.booking_id?`<small>预约 #${t.booking_id} ${bookingBadge(t.booking_status)}</small>`:''}${t.kind==='replica_read'?`<small>${t.required_position!=null?`需 ${t.required_position} / 实际 ${t.observed_position??'—'}`:'无可核对位点'}</small>`:''}`,
    esc(t.reason)]))}
  <div class="section-top"><h2>预约台账</h2><span>待确认 / 成交 / 驳回 / 释放</span></div>
  ${table(['预约','机房 / 时段','状态','位点 / 等待'],r.bookings.map(b=>[
    `#${b.id}<small>${esc(b.session)}</small>`, `${esc(b.room)}<small>${b.start_ms}–${b.end_ms}ms · attempt ${b.attempt}</small>`,
    bookingBadge(b.status), `${b.position??'—'} / ${b.waited_ms}ms`]))}
  <div class="evidence-notes"><section><h3>副本投影</h3><p>副本只复制已成交预约；已驳回与待确认不会出现。被排除：#${r.projection.excluded_ids.join(', #')||'无'}。</p>
  ${table(['#','机房','位点'],projection.map(p=>[p.booking_id,esc(p.room),p.position]))}</section>
  <section><h3>成交后的副本读取</h3><p>同一会话查副本，若副本位点没追上成交位点，按原读路由规则回退主库或拒绝；粘滞期内保持主库。</p>
  <p>已路由 ${s.replica_reads_routed} / ${s.replica_reads} · 拒绝 ${s.replica_reads_rejected} · 回退 ${s.replica_fallbacks}</p>
  <code>${esc(r.checksum)}</code></section></div>
  ${table(['机房','成交单'],r.rooms.map(room=>[esc(room),confirmed.filter(b=>b.room===room).length?confirmed.filter(b=>b.room===room).map(b=>'#'+b.id).join('、'):'<span class="rejected">无成交（不影响其他机房）</span>']))}`}`;
}
