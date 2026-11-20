"""API 层：权限、入队、worker 处理与旧试验回归。"""
from src.worker import handle
from tests.conftest import auth, fake


def queue_payload(name='booking-api', strategy='recheck'):
    return {
        'name': name, 'strategy': strategy, 'rooms': ['east-hall', 'west-hall'],
        'events': [
            {'at_ms': 0, 'kind': 'inspect', 'session': 'A', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000},
            {'at_ms': 1000, 'kind': 'register', 'session': 'A', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000, 'attempt': 1},
            {'at_ms': 2000, 'kind': 'register', 'session': 'B', 'room': 'east-hall', 'start_ms': 0, 'end_ms': 3600000, 'attempt': 1},
            {'at_ms': 3000, 'kind': 'register', 'session': 'C', 'room': 'west-hall', 'start_ms': 0, 'end_ms': 3600000, 'attempt': 1},
            {'at_ms': 4000, 'kind': 'replica_read', 'session': 'C', 'room': 'west-hall'},
        ],
        'hold_ttl_ms': 600000, 'wait_max_ms': 300000, 'max_delay_ms': 100, 'sticky_ms': 500,
    }


def drain():
    while True:
        item = fake.blpop(['replicaroute:queue', 'booking:queue'], timeout=1)
        if not item:
            return
        handle(item[0], int(item[1]))


def test_viewer_cannot_create_booking(client, headers):
    viewer = auth(client, 'viewer')
    res = client.post('/booking_runs', json=queue_payload(), headers=viewer)
    assert res.status_code == 403
    assert client.get('/booking_runs', headers=headers).status_code == 200


def test_booking_end_to_end(client, headers):
    res = client.post('/booking_runs', json=queue_payload(), headers=headers)
    assert res.status_code == 201, res.text
    ident = res.json()['id']
    assert fake.lrange('booking:queue', 0, -1)[-1] == str(ident)
    drain()
    run = client.get(f'/booking_runs/{ident}', headers=headers).json()
    assert run['status'] == 'completed'
    summary = run['result']['summary']
    assert summary['confirmed'] == 2 and summary['rejected'] == 1
    assert run['result']['projection']['excluded_ids'] == [2]
    exported = client.get(f'/booking_runs/{ident}/export', headers=headers)
    assert exported.status_code == 200 and 'attachment' in exported.headers['content-disposition']


def test_invalid_payload_rejected(client, headers):
    bad = queue_payload()
    bad['events'][1]['room'] = 'attic'
    assert client.post('/booking_runs', json=bad, headers=headers).status_code == 422
    bad = queue_payload()
    bad['rooms'] = ['east-hall', 'east-hall']
    assert client.post('/booking_runs', json=bad, headers=headers).status_code == 422


def test_legacy_trial_still_works(client, headers):
    created = client.post('/request_sets', json={
        'code': 'QY-REGRESSION-1', 'title': '旧试验回归',
        'events': [{'at_ms': 0, 'kind': 'write', 'session': 'lab-A'},
                   {'at_ms': 100, 'kind': 'read', 'session': 'lab-A'}],
    }, headers=headers)
    assert created.status_code == 201
    res = client.post('/trials', json={'name': 'legacy-regression', 'request_set_id': created.json()['id'],
                                       'max_delay_ms': 100, 'sticky_ms': 500}, headers=headers)
    assert res.status_code == 201, res.text
    drain()
    trial = client.get(f"/trials/{res.json()['id']}", headers=headers).json()
    assert trial['status'] == 'completed'
    assert trial['result']['trace'][1]['node'] == 'harbor-primary'
    assert trial['result']['summary']['rejected'] == 0
