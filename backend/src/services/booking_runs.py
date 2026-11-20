from sqlalchemy import select
from redis import Redis
from fastapi import HTTPException
from src.models.entities import Node,BookingRun
from src.config.settings import REDIS_URL
from src.constants.enums import RUN_STATES
from src.utils.serialization import record
from .common import save
from .bookings import simulate

queue = Redis.from_url(REDIS_URL, decode_responses=True)


def listing(db):
    return [record(x) for x in db.scalars(select(BookingRun).order_by(BookingRun.id.desc()))]


def detail(db, ident):
    obj = db.get(BookingRun, ident)
    if not obj:
        raise HTTPException(404, '预约演练不存在')
    return record(obj)


def create(db, data, user):
    nodes = [record(x) for x in db.scalars(select(Node).order_by(Node.id))]
    payload = data.model_dump()
    try:
        simulate(payload['rooms'], payload['events'], payload['strategy'], nodes,
                 payload['hold_ttl_ms'], payload['wait_max_ms'], payload['max_delay_ms'], payload['sticky_ms'])
    except ValueError as e:
        raise HTTPException(422, str(e))
    snapshot = {'rooms': payload['rooms'], 'events': payload['events'], 'nodes': nodes}
    obj = BookingRun(**payload, input_snapshot=snapshot, status=RUN_STATES[0])
    result = save(db, obj, user, '预约演练入队 ' + data.name)
    try:
        queue.rpush('booking:queue', str(obj.id))
    except Exception:
        obj.status = 'failed'
        obj.error = '队列暂时不可用，请重新创建'
        db.commit()
        raise HTTPException(503, obj.error)
    return result
