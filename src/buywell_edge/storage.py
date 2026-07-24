from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS connections (
  id TEXT PRIMARY KEY,
  extension_id TEXT NOT NULL,
  extension_version TEXT NOT NULL,
  package_digest TEXT NOT NULL,
  display_name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('module','adapter-driver')),
  enabled INTEGER NOT NULL DEFAULT 1,
  config_json TEXT NOT NULL DEFAULT '{}',
  secret_ref TEXT,
  health_state TEXT NOT NULL DEFAULT 'offline',
  health_message TEXT,
  session_expires_at TEXT,
  last_success_at TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS package_versions (
  extension_id TEXT NOT NULL,
  version TEXT NOT NULL,
  digest TEXT NOT NULL,
  directory TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  installed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(extension_id, version, digest)
);
CREATE TABLE IF NOT EXISTS instances (
  id TEXT PRIMARY KEY,
  connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  extension_version TEXT NOT NULL,
  package_digest TEXT NOT NULL,
  state TEXT NOT NULL,
  pid INTEGER,
  started_at TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS event_outbox (
  id TEXT PRIMARY KEY,
  connection_id TEXT NOT NULL REFERENCES connections(id) ON DELETE CASCADE,
  payload_json TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  available_at REAL NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  value_json TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS update_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  target TEXT NOT NULL,
  from_version TEXT,
  to_version TEXT NOT NULL,
  status TEXT NOT NULL,
  detail TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass(frozen=True)
class ConnectionRecord:
    id: str
    extension_id: str
    extension_version: str
    package_digest: str
    display_name: str
    kind: str
    enabled: bool
    config: dict[str, Any]
    secret_ref: str | None
    health_state: str
    health_message: str | None
    session_expires_at: str | None
    last_success_at: str | None


class EdgeStore:
    def __init__(self, filename: Path) -> None:
        filename.parent.mkdir(parents=True, exist_ok=True)
        self.filename = filename
        self._lock = threading.RLock()
        with self.connect() as database:
            database.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        database = sqlite3.connect(self.filename, timeout=10)
        database.row_factory = sqlite3.Row
        try:
            yield database
            database.commit()
        except Exception:
            database.rollback()
            raise
        finally:
            database.close()

    def metadata(self, key: str) -> str | None:
        with self._lock, self.connect() as database:
            row = database.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
            return str(row["value"]) if row else None

    def set_metadata(self, key: str, value: str) -> None:
        with self._lock, self.connect() as database:
            database.execute(
                "INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def delete_metadata(self, key: str) -> None:
        with self._lock, self.connect() as database:
            database.execute("DELETE FROM metadata WHERE key=?", (key,))

    def upsert_connection(self, record: ConnectionRecord) -> None:
        with self._lock, self.connect() as database:
            database.execute(
                """
                INSERT INTO connections(id,extension_id,extension_version,package_digest,display_name,kind,enabled,config_json,secret_ref,health_state,health_message,session_expires_at,last_success_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                  extension_id=excluded.extension_id,extension_version=excluded.extension_version,
                  package_digest=excluded.package_digest,display_name=excluded.display_name,kind=excluded.kind,
                  enabled=excluded.enabled,config_json=excluded.config_json,secret_ref=excluded.secret_ref,
                  health_state=excluded.health_state,health_message=excluded.health_message,
                  session_expires_at=excluded.session_expires_at,last_success_at=excluded.last_success_at,
                  updated_at=CURRENT_TIMESTAMP
                """,
                (
                    record.id, record.extension_id, record.extension_version, record.package_digest,
                    record.display_name, record.kind, int(record.enabled),
                    json.dumps(record.config, ensure_ascii=False), record.secret_ref,
                    record.health_state, record.health_message, record.session_expires_at, record.last_success_at,
                ),
            )

    def connections(self) -> list[ConnectionRecord]:
        with self._lock, self.connect() as database:
            rows = database.execute("SELECT * FROM connections ORDER BY display_name,id").fetchall()
        return [
            ConnectionRecord(
                id=row["id"], extension_id=row["extension_id"], extension_version=row["extension_version"],
                package_digest=row["package_digest"], display_name=row["display_name"], kind=row["kind"],
                enabled=bool(row["enabled"]), config=json.loads(row["config_json"]), secret_ref=row["secret_ref"],
                health_state=row["health_state"], health_message=row["health_message"],
                session_expires_at=row["session_expires_at"], last_success_at=row["last_success_at"],
            )
            for row in rows
        ]

    def set_enabled(self, connection_id: str, enabled: bool) -> bool:
        with self._lock, self.connect() as database:
            cursor = database.execute(
                "UPDATE connections SET enabled=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (int(enabled), connection_id),
            )
            return cursor.rowcount == 1

    def switch_connection(self, connection_id: str, version: str, digest: str) -> tuple[str, str]:
        with self._lock, self.connect() as database:
            connection = database.execute(
                "SELECT extension_id,extension_version,package_digest FROM connections WHERE id=?",
                (connection_id,),
            ).fetchone()
            if not connection:
                raise ValueError("Connection was not found")
            package = database.execute(
                "SELECT 1 FROM package_versions WHERE extension_id=? AND version=? AND digest=?",
                (connection["extension_id"], version, digest),
            ).fetchone()
            if not package:
                raise ValueError("The requested exact package version is not installed")
            previous = (str(connection["extension_version"]), str(connection["package_digest"]))
            database.execute(
                "UPDATE connections SET extension_version=?,package_digest=?,health_state='offline',health_message=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (version, digest, connection_id),
            )
            database.execute(
                "INSERT INTO update_history(kind,target,from_version,to_version,status,detail) VALUES('extension',?,?,?,'switched',?)",
                (connection_id, previous[0], version, json.dumps({"fromDigest": previous[1], "toDigest": digest})),
            )
            return previous

    def rollback_connection(self, connection_id: str) -> tuple[str, str]:
        with self._lock, self.connect() as database:
            history = database.execute(
                "SELECT from_version,detail FROM update_history WHERE kind='extension' AND target=? AND status='switched' AND from_version IS NOT NULL ORDER BY id DESC LIMIT 1",
                (connection_id,),
            ).fetchone()
            if not history:
                raise ValueError("No previous extension version is available")
            detail = json.loads(history["detail"] or "{}")
            digest = detail.get("fromDigest")
            if not digest:
                raise ValueError("Previous package digest is unavailable")
        self.switch_connection(connection_id, str(history["from_version"]), str(digest))
        return str(history["from_version"]), str(digest)

    def package_in_use(self, extension_id: str, version: str, digest: str) -> bool:
        with self._lock, self.connect() as database:
            return database.execute(
                "SELECT 1 FROM connections WHERE extension_id=? AND extension_version=? AND package_digest=? LIMIT 1",
                (extension_id, version, digest),
            ).fetchone() is not None

    def remove_package(self, extension_id: str, version: str, digest: str) -> None:
        with self._lock, self.connect() as database:
            database.execute(
                "DELETE FROM package_versions WHERE extension_id=? AND version=? AND digest=?",
                (extension_id, version, digest),
            )

    def update_health(self, connection_id: str, health: dict[str, Any]) -> None:
        with self._lock, self.connect() as database:
            database.execute(
                """
                UPDATE connections SET health_state=?,health_message=?,session_expires_at=?,
                  last_success_at=COALESCE(?,last_success_at),updated_at=CURRENT_TIMESTAMP WHERE id=?
                """,
                (
                    health.get("state", "degraded"), health.get("message"), health.get("session_expires_at"),
                    health.get("last_success_at"), connection_id,
                ),
            )

    def register_package(self, manifest: dict[str, Any], directory: Path) -> None:
        extension = manifest["extension"]
        with self._lock, self.connect() as database:
            database.execute(
                """
                INSERT INTO package_versions(extension_id,version,digest,directory,manifest_json)
                VALUES(?,?,?,?,?) ON CONFLICT(extension_id,version,digest) DO NOTHING
                """,
                (extension["id"], extension["version"], manifest["package"]["digest"], str(directory), json.dumps(manifest, ensure_ascii=False)),
            )

    def package(self, extension_id: str, version: str, digest: str) -> tuple[dict[str, Any], Path] | None:
        with self._lock, self.connect() as database:
            row = database.execute(
                "SELECT manifest_json,directory FROM package_versions WHERE extension_id=? AND version=? AND digest=?",
                (extension_id, version, digest),
            ).fetchone()
        return (json.loads(row["manifest_json"]), Path(row["directory"])) if row else None

    def installed_packages(self) -> list[tuple[dict[str, Any], Path]]:
        with self._lock, self.connect() as database:
            rows = database.execute(
                "SELECT manifest_json,directory FROM package_versions "
                "ORDER BY extension_id,version,digest"
            ).fetchall()
        return [
            (json.loads(row["manifest_json"]), Path(row["directory"]))
            for row in rows
        ]

    def update_history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock, self.connect() as database:
            rows = database.execute(
                "SELECT kind,target,from_version,to_version,status,detail,created_at "
                "FROM update_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {
                "kind": row["kind"],
                "target": row["target"],
                "fromVersion": row["from_version"],
                "toVersion": row["to_version"],
                "status": row["status"],
                "detail": json.loads(row["detail"]) if row["detail"] else None,
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def idempotency_result(self, key: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as database:
            row = database.execute("SELECT status,value_json FROM idempotency WHERE key=?", (key,)).fetchone()
        return {"status": row["status"], "value": json.loads(row["value_json"]) if row["value_json"] else None} if row else None

    def begin_idempotent(self, key: str) -> bool:
        with self._lock, self.connect() as database:
            cursor = database.execute("INSERT OR IGNORE INTO idempotency(key,status) VALUES(?,'running')", (key,))
            return cursor.rowcount == 1

    def finish_idempotent(self, key: str, status: str, value: dict[str, Any]) -> None:
        with self._lock, self.connect() as database:
            database.execute(
                "UPDATE idempotency SET status=?,value_json=?,updated_at=CURRENT_TIMESTAMP WHERE key=?",
                (status, json.dumps(value, ensure_ascii=False), key),
            )

    def enqueue_event(self, connection_id: str, payload: dict[str, Any]) -> str:
        event_id = str(payload.get("eventId") or uuid.uuid4())
        value = {**payload, "eventId": event_id}
        with self._lock, self.connect() as database:
            database.execute(
                "INSERT OR IGNORE INTO event_outbox(id,connection_id,payload_json,available_at) VALUES(?,?,?,?)",
                (event_id, connection_id, json.dumps(value, ensure_ascii=False), time.time()),
            )
        return event_id

    def pending_events(self, limit: int = 100) -> list[tuple[str, str, dict[str, Any]]]:
        with self._lock, self.connect() as database:
            rows = database.execute(
                "SELECT id,connection_id,payload_json FROM event_outbox WHERE available_at<=? ORDER BY created_at,id LIMIT ?",
                (time.time(), limit),
            ).fetchall()
            for row in rows:
                database.execute(
                    "UPDATE event_outbox SET attempts=attempts+1,available_at=? WHERE id=?",
                    (time.time() + 5, row["id"]),
                )
        return [(str(row["id"]), str(row["connection_id"]), json.loads(row["payload_json"])) for row in rows]

    def acknowledge_event(self, event_id: str) -> None:
        with self._lock, self.connect() as database:
            database.execute("DELETE FROM event_outbox WHERE id=?", (event_id,))
