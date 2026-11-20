"""机房时段预约演练的确定性关键规则。"""
import pytest

from src.services.bookings import DAY_MS, simulate

NODES = [
    {'name': 'harbor-primary', 'role': 'primary', 'status': 'healthy', 'lineage': 'qiyuan-log-01', 'position': 105, 'delay_ms': 0},
    {'name': 'north-replica', 'role': 'replica', 'status': 'healthy', 'lineage': 'qiyuan-log-01', 'position': 104, 'delay_ms': 150},
    {'name': 'south-replica', 'role': 'replica', 'status': 'healthy', 'lineage': 'qiyuan-log-01', 'position': 105, 'delay_ms': 20},
]
ROOMS = ['east-hall', 'west-hall']


def book(session, room, attempt, at, start=0, end=3600000, kind='register'):
    return {'at_ms': at, 'kind': kind, 'session': session, 'room': room,
            'start_ms': start, 'end_ms': end, 'attempt': attempt}


def test_recheck_loser_must_redo_with_new_attempt():
    events = [book('A', 'east-hall', 1, 1000), book('B', 'east-hall', 1, 2000),
              book('B', 'east-hall', 2, 2000)]
    result = simulate(ROOMS, events, 'recheck', NODES)
    first, second, third = result['bookings']
    assert first['status'] == 'confirmed'
    assert second['status'] == 'rejected'
    assert third['status'] == 'rejected' and third['attempt'] == 2
    assert result['summary']['confirmed'] == 1
    assert result['summary']['redo_attempts'] == 1


def test_same_session_retry_with_new_attempt_does_not_double_book():
    events = [book('B', 'east-hall', 1, 1000), book('B', 'east-hall', 2, 2000)]
    result = simulate(ROOMS, events, 'recheck', NODES)
    first, second = result['bookings']
    assert first['status'] == 'confirmed'
    assert second['status'] == 'rejected' and '本会话已成交' in second['reason']
    assert result['summary']['confirmed'] == 1


def test_hold_strategy_waits_then_grant_on_hold_expiry():
    events = [
        {'at_ms': 0, 'kind': 'inspect', 'session': 'A', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000},
        book('B', 'east-hall', 1, 1000),
    ]
    result = simulate(ROOMS, events, 'hold', NODES, hold_ttl_ms=5000, wait_max_ms=60000)
    b = result['bookings'][0]
    assert b['status'] == 'confirmed'
    assert b['waited_ms'] == 4000
    assert b['reason'] == '前序占位到期，空档转给等待者'
    decisions = [t['decision'] for t in result['trace']]
    kinds = [t['kind'] for t in result['trace']]
    assert 'pending' in decisions and 'grant' in kinds


def test_hold_confirm_rejects_still_waiting_session():
    events = [
        {'at_ms': 0, 'kind': 'inspect', 'session': 'A', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000},
        book('B', 'east-hall', 1, 1000),
        book('A', 'east-hall', 1, 2000),
        book('B', 'east-hall', 2, 2500),
        {'at_ms': 3000, 'kind': 'release', 'session': 'A', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000},
        book('B', 'east-hall', 3, 3500),
    ]
    result = simulate(ROOMS, events, 'hold', NODES)
    by_key = {(b['session'], b['attempt']): b for b in result['bookings']}
    assert by_key[('B', 1)]['status'] == 'rejected'
    assert '等待结束' in by_key[('B', 1)]['reason']
    assert by_key[('A', 1)]['status'] == 'released'
    assert by_key[('B', 2)]['status'] == 'rejected'
    assert by_key[('B', 3)]['status'] == 'confirmed'
    assert result['summary']['waited_bookings'] >= 1
    assert result['summary']['redo_attempts'] >= 2


def test_hold_ttl_expiry_releases_slot_and_rejects_waiter():
    events = [
        {'at_ms': 0, 'kind': 'inspect', 'session': 'A', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000},
        book('B', 'east-hall', 1, 1000),
    ]
    result = simulate(ROOMS, events, 'hold', NODES, hold_ttl_ms=5000, wait_max_ms=1000)
    b = result['bookings'][0]
    assert b['status'] == 'rejected'
    kinds = [t['kind'] for t in result['trace']]
    assert 'hold_expired' in kinds and 'wait_timeout' in kinds


def test_other_rooms_are_not_blocked():
    events = [book('A', 'east-hall', 1, 0), book('C', 'west-hall', 1, 0),
              book('D', 'east-hall', 1, 0)]
    result = simulate(ROOMS, events, 'recheck', NODES)
    status = {(b['session'], b['room']): b['status'] for b in result['bookings']}
    assert status[('A', 'east-hall')] == 'confirmed'
    assert status[('C', 'west-hall')] == 'confirmed'
    assert status[('D', 'east-hall')] == 'rejected'


def test_retry_same_attempt_is_idempotent():
    events = [book('A', 'east-hall', 7, 0), book('A', 'east-hall', 7, 500, kind='retry')]
    result = simulate(ROOMS, events, 'recheck', NODES)
    assert len(result['bookings']) == 1
    assert result['bookings'][0]['status'] == 'confirmed'
    assert result['trace'][1]['decision'] == 'duplicate'
    assert result['summary']['duplicate_retries_blocked'] == 1


def test_rejected_booking_never_enters_replica_projection():
    events = [book('A', 'east-hall', 1, 0), book('B', 'east-hall', 1, 1000),
              {'at_ms': 2000, 'kind': 'replicate', 'node': 'south-replica', 'position': 106, 'delay_ms': 20},
              {'at_ms': 3000, 'kind': 'replica_read', 'session': 'A', 'room': 'east-hall'}]
    result = simulate(ROOMS, events, 'recheck', NODES)
    rejected = [b['id'] for b in result['bookings'] if b['status'] == 'rejected']
    assert rejected and rejected[0] in result['projection']['excluded_ids']
    assert all(p['booking_id'] not in rejected for p in result['projection']['confirmed'])
    replica = [t for t in result['trace'] if t['kind'] == 'replica_read'][0]
    assert replica['decision'] == 'routed' and replica['node'] == 'south-replica'


def test_replica_read_falls_back_when_not_caught_up():
    events = [book('A', 'east-hall', 1, 0), book('C', 'west-hall', 1, 1000),
              {'at_ms': 2000, 'kind': 'replicate', 'node': 'south-replica', 'position': 106, 'delay_ms': 20},
              {'at_ms': 3000, 'kind': 'replica_read', 'session': 'C', 'room': 'west-hall'}]
    result = simulate(ROOMS, events, 'recheck', NODES)
    replica = [t for t in result['trace'] if t['kind'] == 'replica_read'][0]
    assert replica['caught_up'] is False
    assert replica['decision'] == 'routed' and replica['node'] == 'harbor-primary'
    assert result['summary']['replica_fallbacks'] == 1


def test_same_input_same_checksum():
    events = [book('A', 'east-hall', 1, 0), book('B', 'east-hall', 1, 1000)]
    assert simulate(ROOMS, events, 'hold', NODES)['checksum'] == simulate(ROOMS, events, 'hold', NODES)['checksum']


def test_overlap_boundaries_touching_slots_do_not_conflict():
    events = [book('A', 'east-hall', 1, 0, 0, 1000), book('B', 'east-hall', 1, 1000, 1000, 2000)]
    result = simulate(ROOMS, events, 'recheck', NODES)
    assert [b['status'] for b in result['bookings']] == ['confirmed', 'confirmed']


def test_validation_rejects_unknown_room_and_bad_time():
    with pytest.raises(ValueError):
        simulate(ROOMS, [book('A', 'attic', 1, 0)], 'recheck', NODES)
    with pytest.raises(ValueError):
        simulate(ROOMS, [book('A', 'east-hall', 1, DAY_MS + 1)], 'recheck', NODES)
    with pytest.raises(ValueError):
        simulate(ROOMS, [book('A', 'east-hall', 1, 1000, 5000, 1000)], 'recheck', NODES)
