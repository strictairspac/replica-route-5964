import {table} from '../components/common/table.js';
import {badge} from '../components/common/badge.js';
import {esc,date} from '../utils/html.js';
import {session} from '../hooks/session.js';
import {BOOKING_STRATEGIES} from '../constants/bookings.js';

export const bookingSample=[
  {at_ms:0,kind:'inspect',session:'shift-A',room:'east-hall',start_ms:0,end_ms:3600000},
  {at_ms:1000,kind:'inspect',session:'shift-B',room:'east-hall',start_ms:1800000,end_ms:5400000},
  {at_ms:3000,kind:'register',session:'shift-B',room:'east-hall',start_ms:1800000,end_ms:5400000,attempt:1},
  {at_ms:4000,kind:'register',session:'shift-A',room:'east-hall',start_ms:0,end_ms:3600000,attempt:1},
  {at_ms:6000,kind:'retry',session:'shift-A',room:'east-hall',start_ms:0,end_ms:3600000,attempt:1},
  {at_ms:7000,kind:'register',session:'shift-C',room:'west-hall',start_ms:0,end_ms:3600000,attempt:1},
  {at_ms:8000,kind:'register',session:'shift-A',room:'east-hall',start_ms:5400000,end_ms:7200000,attempt:2},
  {at_ms:12000,kind:'replicate',node:'south-replica',position:107,delay_ms:20},
  {at_ms:13000,kind:'replica_read',session:'shift-A',room:'east-hall'},
  {at_ms:14000,kind:'replica_read',session:'shift-C',room:'west-hall'}
];

export function bookingsPage(items){
  return `<div class="split"><section><div class="section-top"><h2>预约演练记录</h2><span>TWO-STEP / IDEMPOTENT / REPLICA</span></div>
  ${table(['演练 / 时间','策略','状态','证据'],items.map(r=>[`<strong>${esc(r.name)}</strong><small>${date(r.created_at)}</small>`,esc(BOOKING_STRATEGIES[r.strategy]||r.strategy),badge(r.status),`<a href="#booking_evidence/${r.id}">查看 →</a>`]))}
  <div class="protocol"><h3>两步预约的边界</h3><ol><li>查空档只读取可用性；是否占位由策略决定，登记才是成交动作</li><li>同一机房重叠时段最多成交一单，不同机房互不阻塞</li><li>同一会话同一次登记（同attempt）重试沿用原记录，不会重复占用</li><li>副本只复制已成交预约，已驳回的登记永远不进副本</li><li>成交后查副本，没追上就按读路由原规则回退或拒绝</li></ol></div>
  </section>
  <section class="form-panel"><span class="eyebrow">BOOKING REHEARSAL</span><h2>排一次并发预约</h2>
  <div class="sample-row"><button type="button" class="small" data-sample="interleave">载入交错两单样本</button><span>同一机房重叠时段，两单交错</span></div>
  <form id="booking-form">
  <label>演练名称<input name="name" value="shift-booking-20261117" required></label>
  <label>预约策略<select name="strategy"><option value="recheck">提交时再复检（输家重做）</option><option value="hold">查到空档先占位（后来者等待）</option></select></label>
  <label>机房（逗号分隔）<input name="rooms" value="east-hall, west-hall, north-lab" required></label>
  <label>时序事件JSON<textarea name="events" rows="14" required>${esc(JSON.stringify(bookingSample,null,2))}</textarea></label>
  <div class="fields"><label>占位有效期ms<input name="hold_ttl_ms" type="number" min="0" value="600000" required></label><label>等待上限ms<input name="wait_max_ms" type="number" min="0" value="300000" required></label></div>
  <div class="fields"><label>延迟门槛ms<input name="max_delay_ms" type="number" min="0" value="100" required></label><label>粘滞时间ms<input name="sticky_ms" type="number" min="0" value="500" required></label></div>
  <button class="primary" ${session.user?.role==='viewer'?'disabled':''}>提交预约编排 ↗</button></form>
  <p class="help">查空档(inspect)与登记(register)分开两步；retry 沿用同一 attempt 即视为同一次登记。副本查询复用读路由的位点、延迟与粘滞规则。</p>
  </section></div>`;
}
