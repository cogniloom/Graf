"""Administrative erasure never grants mutation authority to runtime callers."""

import uuid

import psycopg
import pytest
from test_app_lifecycle import app_database, manager, ready

from evidencekg.hybrid.postgres import _connect

__all__ = ["app_database", "manager"]


def test_runtime_cannot_authorize_or_execute_erasure(manager):
    ready(manager)
    with _connect(manager.dsn) as db:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute(
                "INSERT INTO docworm_erasure_authorizations(id,instruction_sha,actor) VALUES('fake','fake','fake')"
            )
        db.rollback()
        db.execute("SELECT set_config('graf.erasure_action','fake',false)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            db.execute("DELETE FROM hybrid_documents")


def test_admin_needs_live_attributed_authorization(manager, app_database):
    ready(manager)
    with _connect(app_database[1]) as db:
        with pytest.raises(psycopg.Error, match="Immutable"):
            db.execute("DELETE FROM hybrid_results WHERE false")
        db.rollback()
        key = uuid.uuid4().hex
        with db.transaction():
            db.execute(
                "INSERT INTO docworm_erasure_authorizations(id,instruction_sha,actor) VALUES(%s,%s,%s)",
                (key, "a" * 64, "synthetic-administrator"),
            )
            db.execute("SELECT set_config('graf.erasure_action',%s,true)", (key,))
            db.execute("DELETE FROM hybrid_results WHERE false")
            db.execute("UPDATE docworm_erasure_authorizations SET completed_at=now() WHERE id=%s", (key,))
        with pytest.raises(psycopg.Error, match="Immutable"):
            db.execute("DELETE FROM hybrid_results WHERE false")
