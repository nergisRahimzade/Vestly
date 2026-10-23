import os

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["VESTLY_SKIP_WORLD"] = "1"
# The unit tests run on the cast world: 15 people, January to June 2026 (the app and the scorer default to the company world).
os.environ["VESTLY_WORLD"] = "cast"

import pytest
from fastapi.testclient import TestClient

from app.db import ENGINE, Base, init_db
from app.equity import models as equity_models  # noqa: F401
from app.ledger import models  # noqa: F401
from app.reconcile import models as reconcile_models  # noqa: F401
from app.record import models as record_models  # noqa: F401
from app.controls import models as controls_models  # noqa: F401
from app.cases import models as cases_models  # noqa: F401
from app.fx import models as fx_models  # noqa: F401
from app.memory import models as memory_models  # noqa: F401
from app.world import models as world_models  # noqa: F401
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(ENGINE)
    init_db()
    with TestClient(app) as test_client:
        yield test_client
