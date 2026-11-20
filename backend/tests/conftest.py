import os
import sys
import tempfile
from pathlib import Path

import fakeredis
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB_FILE = Path(tempfile.mkdtemp()) / 'test.db'
os.environ['DATABASE_URL'] = 'sqlite:///' + str(DB_FILE)
os.environ['SEED_DEMO'] = 'false'
os.environ['JWT_SECRET'] = 'test-secret'
os.environ['ARTIFACT_DIR'] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

from src.config.database import Base, Session, engine  # noqa: E402
from src.main import app  # noqa: E402
from src.models.entities import Node, User  # noqa: E402
from src.services import booking_runs, trials  # noqa: E402
from src.utils.security import password_hash  # noqa: E402

fake = fakeredis.FakeRedis(decode_responses=True)
booking_runs.queue = fake
trials.queue = fake


@pytest.fixture(scope='session', autouse=True)
def prepare():
    Base.metadata.create_all(engine)
    with Session() as db:
        for role in ('admin', 'operator', 'viewer'):
            db.add(User(email=role + '@qiyuan.test', password=password_hash('Demo2026!'), role=role))
        for name, role, pos, delay in [('harbor-primary', 'primary', 105, 0),
                                       ('north-replica', 'replica', 104, 150),
                                       ('south-replica', 'replica', 105, 20)]:
            db.add(Node(name=name, role=role, status='healthy', lineage='qiyuan-log-01', position=pos, delay_ms=delay, note=''))
        db.commit()
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def auth(client, role='operator'):
    res = client.post('/auth/login', json={'email': role + '@qiyuan.test', 'password': 'Demo2026!'})
    assert res.status_code == 200, res.text
    return {'Authorization': 'Bearer ' + res.json()['token']}


@pytest.fixture()
def headers(client):
    return auth(client)
