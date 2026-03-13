"""
Gestion de la base de données SQLite avec pool de connexions.

Comparaison des pools de threads (cf. rapport) :
  - ThreadPoolExecutor  : pool générique, tâches asynchrones
  - Queue-based pool    : contrôle précis, bloquant, adapté aux connexions BD
  → Choix : Queue-based pool (threading.Queue) pour l'accès BD, car
    il garantit qu'au plus N connexions sont ouvertes simultanément et
    que chaque thread récupère/relâche une connexion de façon FIFO.
"""

import sqlite3
import threading
import queue
import logging
import os
from datetime import datetime

logger = logging.getLogger("database")

DB_PATH      = os.path.join(os.path.dirname(__file__), "..", "db", "supervision.db")
POOL_SIZE    = 5   # taille du pool de connexions BD


# ─── Pool de connexions SQLite ────────────────────────────────────────────────

class ConnectionPool:
    """Pool de connexions SQLite basé sur threading.Queue (FIFO, thread-safe)."""

    def __init__(self, db_path: str, pool_size: int = POOL_SIZE):
        self._db_path   = db_path
        self._pool_size = pool_size
        self._pool: queue.Queue = queue.Queue(maxsize=pool_size)
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        for _ in range(pool_size):
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            self._pool.put(conn)
        logger.info("Pool BD initialisé (%d connexions) → %s", pool_size, db_path)

    def acquire(self, timeout: float = 5.0) -> sqlite3.Connection:
        """Récupère une connexion disponible (bloque jusqu'à timeout)."""
        try:
            return self._pool.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError("Pool BD épuisé : aucune connexion disponible")

    def release(self, conn: sqlite3.Connection):
        """Remet la connexion dans le pool."""
        self._pool.put(conn)

    def execute(self, sql: str, params: tuple = ()):
        """Exécute une requête INSERT/UPDATE/DELETE (auto-commit)."""
        conn = self.acquire()
        try:
            cursor = conn.execute(sql, params)
            conn.commit()
            return cursor.lastrowid
        finally:
            self.release(conn)

    def fetchall(self, sql: str, params: tuple = ()) -> list:
        conn = self.acquire()
        try:
            cursor = conn.execute(sql, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            self.release(conn)

    def fetchone(self, sql: str, params: tuple = ()):
        conn = self.acquire()
        try:
            cursor = conn.execute(sql, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            self.release(conn)

    def close_all(self):
        while not self._pool.empty():
            conn = self._pool.get_nowait()
            conn.close()


# ─── Initialisation du schéma ─────────────────────────────────────────────────

CREATE_NODES = """
CREATE TABLE IF NOT EXISTS nodes (
    node_id     TEXT PRIMARY KEY,
    os          TEXT,
    cpu_type    TEXT,
    ip_address  TEXT,
    first_seen  TEXT,
    last_seen   TEXT,
    status      TEXT DEFAULT 'ACTIVE'   -- ACTIVE | DOWN
);"""

CREATE_METRICS = """
CREATE TABLE IF NOT EXISTS metrics (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    cpu         REAL,
    memory      REAL,
    disk        REAL,
    uptime      INTEGER,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);"""

CREATE_SERVICES = """
CREATE TABLE IF NOT EXISTS service_status (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    service     TEXT NOT NULL,
    status      TEXT NOT NULL   -- OK | FAIL
);"""

CREATE_PORTS = """
CREATE TABLE IF NOT EXISTS port_status (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    port        INTEGER NOT NULL,
    status      TEXT NOT NULL   -- OPEN | CLOSED
);"""

CREATE_ALERTS = """
CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    metric      TEXT NOT NULL,
    value       REAL NOT NULL,
    threshold   REAL NOT NULL,
    acknowledged INTEGER DEFAULT 0
);"""

CREATE_LOGS = """
CREATE TABLE IF NOT EXISTS event_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    level       TEXT NOT NULL,   -- INFO | WARNING | ERROR
    node_id     TEXT,
    message     TEXT NOT NULL
);"""


def init_db(pool: ConnectionPool):
    """Crée les tables si elles n'existent pas."""
    for ddl in [CREATE_NODES, CREATE_METRICS, CREATE_SERVICES,
                CREATE_PORTS, CREATE_ALERTS, CREATE_LOGS]:
        pool.execute(ddl)
    logger.info("Schéma BD initialisé.")


# ─── Fonctions métier ─────────────────────────────────────────────────────────

def upsert_node(pool: ConnectionPool, node_id: str, info: dict):
    now = datetime.utcnow().isoformat()
    existing = pool.fetchone("SELECT node_id FROM nodes WHERE node_id=?", (node_id,))
    if existing:
        pool.execute(
            "UPDATE nodes SET last_seen=?, status='ACTIVE', os=?, cpu_type=?, ip_address=? WHERE node_id=?",
            (now, info.get("os","?"), info.get("cpu_type","?"), info.get("ip","?"), node_id)
        )
    else:
        pool.execute(
            "INSERT INTO nodes(node_id,os,cpu_type,ip_address,first_seen,last_seen,status) VALUES(?,?,?,?,?,?,?)",
            (node_id, info.get("os","?"), info.get("cpu_type","?"),
             info.get("ip","?"), now, now, "ACTIVE")
        )


def insert_metrics(pool: ConnectionPool, node_id: str, ts: str, m: dict):
    pool.execute(
        "INSERT INTO metrics(node_id,timestamp,cpu,memory,disk,uptime) VALUES(?,?,?,?,?,?)",
        (node_id, ts, m.get("cpu"), m.get("memory"), m.get("disk"), m.get("uptime"))
    )


def insert_services(pool: ConnectionPool, node_id: str, ts: str, services: dict):
    for svc, status in services.items():
        pool.execute(
            "INSERT INTO service_status(node_id,timestamp,service,status) VALUES(?,?,?,?)",
            (node_id, ts, svc, status)
        )


def insert_ports(pool: ConnectionPool, node_id: str, ts: str, ports: dict):
    for port, status in ports.items():
        pool.execute(
            "INSERT INTO port_status(node_id,timestamp,port,status) VALUES(?,?,?,?)",
            (node_id, ts, int(port), status)
        )


def insert_alert(pool: ConnectionPool, node_id: str, ts: str,
                 metric: str, value: float, threshold: float):
    pool.execute(
        "INSERT INTO alerts(node_id,timestamp,metric,value,threshold) VALUES(?,?,?,?,?)",
        (node_id, ts, metric, value, threshold)
    )


def log_event(pool: ConnectionPool, level: str, message: str, node_id: str = None):
    pool.execute(
        "INSERT INTO event_logs(timestamp,level,node_id,message) VALUES(?,?,?,?)",
        (datetime.utcnow().isoformat(), level, node_id, message)
    )


def mark_node_down(pool: ConnectionPool, node_id: str):
    pool.execute("UPDATE nodes SET status='DOWN' WHERE node_id=?", (node_id,))


def get_all_nodes(pool: ConnectionPool) -> list:
    return pool.fetchall("SELECT * FROM nodes ORDER BY last_seen DESC")


def get_latest_metrics(pool: ConnectionPool, node_id: str) -> dict:
    return pool.fetchone(
        "SELECT * FROM metrics WHERE node_id=? ORDER BY timestamp DESC LIMIT 1",
        (node_id,)
    )


def get_metrics_history(pool: ConnectionPool, node_id: str, limit: int = 50) -> list:
    return pool.fetchall(
        "SELECT * FROM metrics WHERE node_id=? ORDER BY timestamp DESC LIMIT ?",
        (node_id, limit)
    )


def get_active_alerts(pool: ConnectionPool) -> list:
    return pool.fetchall(
        "SELECT * FROM alerts WHERE acknowledged=0 ORDER BY timestamp DESC"
    )


def get_recent_logs(pool: ConnectionPool, limit: int = 100) -> list:
    return pool.fetchall(
        "SELECT * FROM event_logs ORDER BY timestamp DESC LIMIT ?", (limit,)
    )
