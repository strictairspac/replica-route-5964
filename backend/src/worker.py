import json
import os
import traceback
from pathlib import Path

from src.config.database import Session
from src.models.entities import BookingRun, Trial
from src.services.booking_runs import queue
from src.services.bookings import simulate as simulate_booking
from src.services.routing import simulate as simulate_route

QUEUES = ('replicaroute:queue', 'booking:queue')
ARTIFACTS = Path(os.getenv('ARTIFACT_DIR', '/artifacts'))


def run_booking(ident):
    with Session() as db:
        obj = db.get(BookingRun, ident)
        obj.status = 'running'
        db.commit()
        snapshot = obj.input_snapshot
        strategy = obj.strategy
        hold_ttl_ms, wait_max_ms = obj.hold_ttl_ms, obj.wait_max_ms
        max_delay_ms, sticky_ms = obj.max_delay_ms, obj.sticky_ms
    result = simulate_booking(snapshot['rooms'], snapshot['events'], strategy, snapshot['nodes'],
                              hold_ttl_ms, wait_max_ms, max_delay_ms, sticky_ms)
    with Session() as db:
        obj = db.get(BookingRun, ident)
        obj.result = result
        obj.status = 'completed'
        db.commit()
    (ARTIFACTS / f'booking-{ident}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))


def run_trial(ident):
    with Session() as db:
        obj = db.get(Trial, ident)
        obj.status = 'running'
        db.commit()
        snapshot = obj.input_snapshot
        delay, sticky = obj.max_delay_ms, obj.sticky_ms
    result = simulate_route(snapshot['nodes'], snapshot['request_set']['events'], delay, sticky)
    with Session() as db:
        obj = db.get(Trial, ident)
        obj.result = result
        obj.status = 'completed'
        db.commit()
    (ARTIFACTS / f'route-{ident}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2))


def handle(queue_name, ident):
    try:
        (run_booking if queue_name == 'booking:queue' else run_trial)(ident)
    except Exception as e:
        traceback.print_exc()
        model = BookingRun if queue_name == 'booking:queue' else Trial
        with Session() as db:
            obj = db.get(model, ident)
            if obj:
                obj.status = 'failed'
                obj.error = str(e)
                db.commit()


def main():
    while True:
        Path('/tmp/worker-heartbeat').touch()
        item = queue.blpop(list(QUEUES), timeout=2)
        if not item:
            continue
        handle(item[0], int(item[1]))


if __name__ == '__main__':
    main()
