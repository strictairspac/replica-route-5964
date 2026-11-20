"""Deterministic room-slot booking rehearsal.

查空档(inspect)与登记(register)分成两步；同一机房重叠时段最多成交一单，其他机房互不影响。
策略二选一：
  hold    — 查到空档先占位（默认占位有效期 hold_ttl_ms），后来者排队等待或超时驳回；
  recheck — 查到空档不预占，提交时再复检；输家立即驳回，需换 attempt 重做。
登记以(session, room, attempt)幂等：同一次登记重试沿用原记录，不会重复占用。
副本只复制“已成交”预约：待确认、已驳回、已释放都不进入副本投影。
成交后同一会话查副本，未追上时直接复用 routing.choose_read_target 的原规则选路。
"""
import copy
import hashlib
import json

from .routing import choose_read_target, validate_nodes

STRATEGIES = {'hold': '查到空档先占位', 'recheck': '提交时再复检'}
EVENT_KINDS = ('inspect', 'register', 'retry', 'release', 'replicate', 'node_down', 'node_up', 'replica_read')
ROOM_EVENTS = ('inspect', 'register', 'retry', 'release')
NODE_EVENTS = ('replicate', 'node_down', 'node_up', 'replica_read')
DAY_MS = 86400000


def overlaps(a_start, a_end, b_start, b_end):
    return a_start < b_end and b_start < a_end


def _slot(event):
    return int(event['start_ms']), int(event['end_ms'])


def _validate_inputs(rooms, events, strategy, hold_ttl_ms, wait_max_ms, max_delay_ms, sticky_ms):
    if not 1 <= len(rooms) <= 12:
        raise ValueError('需要1–12个机房')
    if len(set(rooms)) != len(rooms) or any(not str(r).strip() for r in rooms):
        raise ValueError('机房名必须唯一且非空')
    if not 1 <= len(events) <= 100:
        raise ValueError('需要1–100个时序事件')
    if strategy not in STRATEGIES:
        raise ValueError('未知预约策略')
    for value, label in ((hold_ttl_ms, '占位有效期'), (wait_max_ms, '等待上限'),
                         (max_delay_ms, '延迟门槛'), (sticky_ms, '粘滞时间')):
        if not isinstance(value, int) or value < 0:
            raise ValueError(label + '必须是非负整数')
    names = set(rooms)
    last = -1
    for event in events:
        at = event.get('at_ms')
        kind = event.get('kind')
        if not isinstance(at, int) or not 0 <= at <= DAY_MS:
            raise ValueError('事件时间必须在0–86400000ms')
        if at < last:
            raise ValueError('事件必须按at_ms非递减排列')
        last = at
        if kind not in EVENT_KINDS:
            raise ValueError('未知事件类型：' + str(kind))
        if kind not in ('replicate', 'node_down', 'node_up') and not event.get('session'):
            raise ValueError('机房与副本查询事件都需要session')
        if kind in ROOM_EVENTS:
            if event.get('room') not in names:
                raise ValueError('事件引用未知机房：' + str(event.get('room')))
            start, end = event.get('start_ms'), event.get('end_ms')
            if not isinstance(start, int) or not isinstance(end, int) or not 0 <= start < end <= DAY_MS:
                raise ValueError('时段必须满足0≤start_ms<end_ms≤86400000')
            if kind in ('register', 'retry'):
                attempt = event.get('attempt')
                if not isinstance(attempt, int) or attempt < 1:
                    raise ValueError('登记需要正整数attempt作为幂等号')
        elif kind == 'replicate':
            if not event.get('node'):
                raise ValueError('replicate需要node')
            position, delay = event.get('position'), event.get('delay_ms')
            if not isinstance(position, int) or not isinstance(delay, int) or position < 0 or delay < 0:
                raise ValueError('replicate需要非负position和delay_ms')
        elif kind in ('node_down', 'node_up'):
            if not event.get('node'):
                raise ValueError('节点事件需要node')


def _clock(ms):
    if not isinstance(ms, int) or ms <= 0:
        return '09:00:00'
    hour, rest = divmod(9 * 3600000 + ms, 3600000)
    minute, rest = divmod(rest, 60000)
    second = rest // 1000
    return '%02d:%02d:%02d' % (hour % 24, minute, second)


def simulate(rooms, events, strategy, node_inputs=None, hold_ttl_ms=600000, wait_max_ms=300000,
             max_delay_ms=100, sticky_ms=500):
    node_inputs = node_inputs or []
    _validate_inputs(rooms, events, strategy, hold_ttl_ms, wait_max_ms, max_delay_ms, sticky_ms)
    uses_nodes = any(e['kind'] in NODE_EVENTS for e in events)
    if uses_nodes and not node_inputs:
        raise ValueError('包含节点或副本查询事件时，需要可用的复制节点')

    nodes, primary_name, commit_position = {}, None, 0
    if uses_nodes:
        primary_input = validate_nodes(node_inputs)
        nodes = {n['name']: copy.deepcopy(n) for n in node_inputs}
        primary_name = primary_input['name']
        commit_position = primary_input['position']
    lineage = nodes[primary_name]['lineage'] if primary_name else 'booking-log'

    records, attempts, holds, waiters, trace = [], {}, [], [], []
    duplicate_count = 0

    def row(at, kind, event, decision, reason, record=None, synthetic=False, node=None):
        item = {'index': len(trace) + 1, 'synthetic': synthetic, 'at_ms': at, 'clock': _clock(at),
                'kind': kind, 'session': event.get('session'), 'room': event.get('room'),
                'start_ms': event.get('start_ms'), 'end_ms': event.get('end_ms'),
                'attempt': event.get('attempt'), 'node': node,
                'decision': decision, 'reason': reason,
                'booking_id': record['id'] if record else None,
                'booking_status': record['status'] if record else None}
        trace.append(item)
        return item

    def confirmed_overlap(room, start, end):
        return [r for r in records if r['status'] == 'confirmed' and r['room'] == room
                and overlaps(r['start_ms'], r['end_ms'], start, end)]

    def live_holds(room, start, end, session=None):
        return [h for h in holds if h['room'] == room and overlaps(h['start_ms'], h['end_ms'], start, end)
                and h['session'] != session]

    def own_hold(session, room, start, end):
        return next((h for h in holds if h['session'] == session and h['room'] == room
                     and overlaps(h['start_ms'], h['end_ms'], start, end)), None)

    def end_wait(record, at, reason, kind='wait_end'):
        record.update(status='rejected', reason=reason, resolved_at=at,
                      waited_ms=max(0, at - record['registered_at']))
        row(at, kind, {'session': record['session'], 'room': record['room'],
                       'start_ms': record['start_ms'], 'end_ms': record['end_ms'],
                       'attempt': record['attempt']}, 'rejected', reason, record, synthetic=True)

    def reject_now(record, at, reason):
        record.update(status='rejected', reason=reason, resolved_at=at, waited_ms=0)

    def confirm(record, at, reason):
        nonlocal commit_position
        commit_position += 1
        record.update(status='confirmed', position=commit_position, confirmed_at=at,
                      resolved_at=at, reason=reason, waited_ms=max(0, at - record['registered_at']))
        if primary_name:
            nodes[primary_name]['position'] = commit_position
        for hold in list(holds):
            if hold['session'] == record['session'] and hold['room'] == record['room'] and overlaps(
                    hold['start_ms'], hold['end_ms'], record['start_ms'], record['end_ms']):
                holds.remove(hold)
        for waiter in list(waiters):
            other = waiter['record']
            if other is record or other['status'] != 'pending':
                waiters.remove(waiter)
                continue
            if other['room'] == record['room'] and overlaps(
                    other['start_ms'], other['end_ms'], record['start_ms'], record['end_ms']):
                waiters.remove(waiter)
                end_wait(other, at, '空档已由 #%d 成交，等待结束，需换 attempt 重做' % record['id'])

    def grant_earliest(room, start, end, at, trigger):
        while True:
            candidates = [w for w in waiters
                          if w['record']['status'] == 'pending' and w['record']['room'] == room
                          and overlaps(w['record']['start_ms'], w['record']['end_ms'], start, end)
                          and w['deadline'] > at
                          and not confirmed_overlap(room, w['record']['start_ms'], w['record']['end_ms'])]
            if not candidates:
                return
            candidates.sort(key=lambda w: (w['record']['registered_at'], w['record']['id']))
            record = candidates[0]['record']
            confirm(record, at, trigger)
            row(at, 'grant', {'session': record['session'], 'room': record['room'],
                              'start_ms': record['start_ms'], 'end_ms': record['end_ms'],
                              'attempt': record['attempt']}, 'confirmed',
                '等待后成交，等待 %dms' % record['waited_ms'], record, synthetic=True)

    def settle(limit):
        while True:
            options = [(h['expiry_at'], 0, 'hold', h) for h in holds if h['expiry_at'] <= limit]
            options += [(w['deadline'], 1, 'timeout', w) for w in waiters
                        if w['record']['status'] == 'pending' and w['deadline'] <= limit]
            if not options:
                return
            options.sort(key=lambda item: (item[0], item[1]))
            at, _, kind, item = options[0]
            if kind == 'timeout':
                waiters.remove(item)
                record = item['record']
                if record['status'] == 'pending':
                    end_wait(record, at, '等待超时，未等到空档，需重新查空档', 'wait_timeout')
            else:
                holds.remove(item)
                row(at, 'hold_expired', {'session': item['session'], 'room': item['room'],
                                         'start_ms': item['start_ms'], 'end_ms': item['end_ms']},
                    'expired', '占位到期，空档回到可用池', None, synthetic=True)
                grant_earliest(item['room'], item['start_ms'], item['end_ms'], at, '前序占位到期，空档转给等待者')

    def find_confirmed(session, at, room=None):
        candidates = [r for r in records if r['session'] == session and r['status'] == 'confirmed'
                      and r['confirmed_at'] <= at and (room is None or r['room'] == room)]
        if not candidates:
            return None
        return max(candidates, key=lambda r: (r['confirmed_at'], r['id']))

    def register(event, at, kind):
        nonlocal duplicate_count
        session, room = event['session'], event['room']
        start, end = _slot(event)
        key = (session, room, event['attempt'])
        if key in attempts:
            duplicate_count += 1
            existing = attempts[key]
            row(at, kind, event, 'duplicate',
                '同一次登记重试：沿用记录 #%d（%s），未重复占用' % (existing['id'], existing['status']), existing)
            return
        record = {'id': len(records) + 1, 'session': session, 'room': room, 'start_ms': start,
                  'end_ms': end, 'attempt': event['attempt'], 'status': 'pending',
                  'registered_at': at, 'resolved_at': None, 'position': None,
                  'confirmed_at': None, 'waited_ms': 0, 'reason': ''}
        records.append(record)
        attempts[key] = record
        own_confirmed = [r for r in records if r is not record and r['session'] == session
                         and r['status'] == 'confirmed' and r['room'] == room
                         and overlaps(r['start_ms'], r['end_ms'], start, end)]
        if own_confirmed:
            reason = '本会话已成交重叠时段（#%d），无需重复占用' % own_confirmed[0]['id']
            reject_now(record, at, reason)
            row(at, kind, event, 'rejected', reason, record)
            return
        blocker = confirmed_overlap(room, start, end)
        if blocker:
            reason = ('提交时该时段已被 #%d 成交，需换 attempt 重做' % blocker[0]['id'] if strategy == 'recheck'
                      else '占位期间该时段已被 #%d 成交，需重新查空档' % blocker[0]['id'])
            reject_now(record, at, reason)
            row(at, kind, event, 'rejected', reason, record)
            return
        own = own_hold(session, room, start, end) if strategy == 'hold' else None
        if own:
            confirm(record, at, '占位有效，提交即成交')
            row(at, kind, event, 'confirmed', record['reason'], record)
            return
        blocking = live_holds(room, start, end, session) if strategy == 'hold' else []
        if blocking:
            blocker_hold = min(blocking, key=lambda h: (h['expiry_at'], h['session']))
            waiters.append({'record': record, 'deadline': at + wait_max_ms})
            record['reason'] = ('空档被 %s 的占位挡住，排队等待至 %s 或该占位释放'
                                % (blocker_hold['session'], _clock(blocker_hold['expiry_at'])))
            row(at, kind, event, 'pending', record['reason'], record)
            return
        confirm(record, at, '提交时空档仍在，直接成交')
        row(at, kind, event, 'confirmed', record['reason'], record)

    def release(event, at):
        session, room = event['session'], event['room']
        start, end = _slot(event)
        dropped = [h for h in holds if h['session'] == session and h['room'] == room
                   and overlaps(h['start_ms'], h['end_ms'], start, end)]
        for hold in dropped:
            holds.remove(hold)
        target = next((r for r in reversed(records) if r['session'] == session and r['room'] == room
                       and r['status'] == 'confirmed' and overlaps(r['start_ms'], r['end_ms'], start, end)), None)
        if target:
            target.update(status='released', resolved_at=at, reason='会话主动释放，空档回到可用池')
            row(at, 'release', event, 'applied', target['reason'], target)
        elif dropped:
            row(at, 'release', event, 'applied', '会话撤销占位，空档回到可用池')
        else:
            row(at, 'release', event, 'ignored', '该会话在此机房没有可释放的占位或已成交预约')
            return
        grant_earliest(room, start, end, at, '前序预约释放，空档转给等待者')

    def inspect(event, at):
        session, room = event['session'], event['room']
        start, end = _slot(event)
        blocked_confirmed = confirmed_overlap(room, start, end)
        blocked_holds = live_holds(room, start, end, session) if strategy == 'hold' else []
        if blocked_confirmed or blocked_holds:
            parts = []
            if blocked_confirmed:
                parts.append('#%d 已成交' % blocked_confirmed[0]['id'])
            if blocked_holds:
                parts.append('%s 已占位' % blocked_holds[0]['session'])
            row(at, 'inspect', event, 'unavailable', '空档不可用：' + '；'.join(parts))
            return
        if strategy == 'recheck':
            row(at, 'inspect', event, 'available', '查到空档（提交时再复检，不预占）')
            return
        hold = own_hold(session, room, start, end)
        if hold:
            hold['expiry_at'] = at + hold_ttl_ms
        else:
            holds.append({'session': session, 'room': room, 'start_ms': start, 'end_ms': end,
                          'created_at': at, 'expiry_at': at + hold_ttl_ms})
        row(at, 'inspect', event, 'held', '查到空档并占位，%s 前有效' % _clock(at + hold_ttl_ms))

    def apply_node_event(event, at, kind):
        target = nodes.get(event['node'])
        if not target:
            raise ValueError('事件引用未知节点：' + str(event['node']))
        if kind == 'replicate':
            if target['role'] != 'replica':
                raise ValueError('replicate仅允许副本节点')
            if target['status'] != 'healthy':
                raise ValueError('停机副本不能应用日志，请先node_up')
            if target['lineage'] != lineage:
                raise ValueError('日志谱系不匹配，不得复制当前主库日志')
            position, delay = event['position'], event['delay_ms']
            if not target['position'] <= position <= commit_position:
                raise ValueError('复制位点必须单调且不超过已成交位点')
            target['position'], target['delay_ms'] = position, delay
            row(at, kind, event, 'applied', '副本应用已成交预约至位点 %d' % position, node=target['name'])
        else:
            target['status'] = 'down' if kind == 'node_down' else 'healthy'
            row(at, kind, event, 'applied', '节点下线' if kind == 'node_down' else '节点恢复', node=target['name'])

    def replica_read(event, at):
        session = event['session']
        booking = find_confirmed(session, at, event.get('room'))
        if not booking:
            row(at, 'replica_read', event, 'rejected', '该会话此时没有已成交预约，副本查询没有可核对位点')
            return
        replicas = [n for n in nodes.values() if n['role'] == 'replica']
        preferred = nodes.get(event.get('node')) if event.get('node') else None
        if event.get('node') and (not preferred or preferred['role'] != 'replica'):
            raise ValueError('replica_read的node必须是已知副本')
        if not preferred:
            if not replicas:
                row(at, 'replica_read', event, 'rejected', '没有可查询的副本节点')
                return
            preferred = sorted(replicas, key=lambda n: (-n['position'], n['delay_ms'], n['name']))[0]
        required = booking['position']
        caught_up = (preferred['status'] == 'healthy' and preferred['lineage'] == lineage
                     and preferred['position'] >= required and preferred['delay_ms'] <= max_delay_ms)
        state = {'required': required, 'lineage': lineage, 'last_write_ms': booking['confirmed_at']}
        selected, reason = choose_read_target(nodes, primary_name, state, at, max_delay_ms, sticky_ms)
        if not caught_up:
            reason = '查询副本未追上成交位点（需 %d / 实际 %d）→ %s' % (required, preferred['position'], reason)
        elif selected is None:
            reason = '副本已追上，但主库亦不可用 → ' + reason
        elif selected['name'] != preferred['name']:
            reason = '副本已追上，按原规则改走 %s → %s' % (selected['name'], reason)
        else:
            reason = '副本已追上成交位点，直接读取'
        item = row(at, 'replica_read', event, 'routed' if selected else 'rejected', reason, booking)
        item.update(preferred_node=preferred['name'], node=selected['name'] if selected else None,
                    caught_up=caught_up, required_position=required,
                    observed_position=selected['position'] if selected else None,
                    safe=bool(selected and selected['position'] >= required and selected['lineage'] == lineage),
                    replica_checks=[{'name': n['name'], 'healthy': n['status'] == 'healthy',
                                     'same_lineage': n['lineage'] == lineage,
                                     'position_sufficient': n['position'] >= required,
                                     'delay_within_limit': n['delay_ms'] <= max_delay_ms}
                                    for n in replicas])

    for event in events:
        at = event['at_ms']
        kind = event['kind']
        settle(at)
        if kind == 'inspect':
            inspect(event, at)
        elif kind in ('register', 'retry'):
            register(event, at, kind)
        elif kind == 'release':
            release(event, at)
        elif kind == 'replica_read':
            replica_read(event, at)
        else:
            apply_node_event(event, at, kind)

    horizon = max([DAY_MS] + [w['deadline'] for w in waiters] + [h['expiry_at'] for h in holds])
    settle(horizon)

    confirmed = [r for r in records if r['status'] == 'confirmed']
    rejected = [r for r in records if r['status'] == 'rejected']
    released = [r for r in records if r['status'] == 'released']
    pending = [r for r in records if r['status'] == 'pending']
    projection = [{'booking_id': r['id'], 'room': r['room'], 'session': r['session'],
                   'start_ms': r['start_ms'], 'end_ms': r['end_ms'], 'position': r['position'],
                   'confirmed_at': r['confirmed_at']} for r in sorted(confirmed, key=lambda r: r['position'])]
    replica_reads = [t for t in trace if t['kind'] == 'replica_read']
    summary = {
        'bookings': len(records),
        'confirmed': len(confirmed),
        'rejected': len(rejected),
        'released': len(released),
        'pending': len(pending),
        'duplicate_retries_blocked': duplicate_count,
        'waited_bookings': sum(1 for r in records if r['waited_ms'] > 0),
        'total_waited_ms': sum(r['waited_ms'] for r in records),
        'redo_attempts': sum(1 for r in records if r['attempt'] > 1),
        'replica_reads': len(replica_reads),
        'replica_reads_routed': sum(1 for t in replica_reads if t['decision'] == 'routed'),
        'replica_reads_rejected': sum(1 for t in replica_reads if t['decision'] == 'rejected'),
        'replica_fallbacks': sum(1 for t in replica_reads
                                 if t['decision'] == 'routed' and t.get('node') != t.get('preferred_node')),
        'rooms_confirmed': sorted({r['room'] for r in confirmed}),
        'rooms_touched': sorted({r['room'] for r in records}),
    }
    result = {
        'mode': '受控机房时段预约模型 / 非真实库存系统',
        'strategy': strategy,
        'strategy_label': STRATEGIES[strategy],
        'rooms': list(rooms),
        'trace': trace,
        'bookings': records,
        'projection': {'confirmed': projection,
                       'excluded_ids': [r['id'] for r in records if r['status'] != 'confirmed'],
                       'note': '副本只复制已成交预约；待确认、已驳回、已释放均不进入副本'},
        'summary': summary,
        'policy': {'hold_ttl_ms': hold_ttl_ms, 'wait_max_ms': wait_max_ms,
                   'max_delay_ms': max_delay_ms, 'sticky_ms': sticky_ms,
                   'idempotency': '(session, room, attempt) 相同即视为同一次登记，重试沿用原记录',
                   'replica_read': '复用读路由原规则：粘滞主库 / 位点与延迟达标副本 / 回退主库 / 拒绝'},
    }
    result['checksum'] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return result
