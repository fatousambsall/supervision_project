"""
Serveur central de supervision — multi-clients avec pool de threads.

Comparaison des pools de threads :
  ┌──────────────────────────┬───────────────────────────────────────────────┐
  │ Type de pool             │ Caractéristiques                              │
  ├──────────────────────────┼───────────────────────────────────────────────┤
  │ ThreadPoolExecutor       │ API haut niveau, futures, tâches ponctuelles  │
  │ (concurrent.futures)     │ Moins adapté aux connexions longue durée      │
  ├──────────────────────────┼───────────────────────────────────────────────┤
  │ Queue-based manual pool  │ Contrôle total, connexions persistantes,      │
  │ (threading + Queue)      │ idéal pour sockets clients long-running       │
  └──────────────────────────┴───────────────────────────────────────────────┘
  → Choix : ThreadPoolExecutor pour les handlers clients (chaque connexion
    est une tâche soumise au pool) combiné avec Queue-based pool pour la BD.
"""

import socket
import threading
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# Ajout du répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.protocol import (
    decode_message, build_ack_message,
    MSG_TYPE_METRICS, MSG_TYPE_ALERT,
    MSG_TYPE_HEARTBEAT, MSG_TYPE_RESPONSE,
    TIMEOUT_NODE,
)
from server.database import (
    ConnectionPool, init_db,
    upsert_node, insert_metrics, insert_services,
    insert_ports, insert_alert, log_event,
    mark_node_down, get_all_nodes,
)

# ─── Configuration ─────────────────────────────────────────────────────────────
HOST             = "0.0.0.0"
PORT             = 9000
MAX_WORKERS      = 20       # taille du pool de threads pour les clients
BUFFER_SIZE      = 4096
LOG_DIR          = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# ─── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "server.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("server")


# ─── État global partagé (thread-safe) ────────────────────────────────────────
class SharedState:
    def __init__(self):
        self._lock = threading.Lock()
        self.connected_clients: dict = {}   # node_id → {"socket", "addr", "last_seen"}
        self.node_sockets: dict = {}        # node_id → socket (pour envoyer des cmds)

    def register(self, node_id: str, sock: socket.socket, addr):
        with self._lock:
            self.connected_clients[node_id] = {
                "socket":    sock,
                "addr":      addr,
                "last_seen": time.time(),
            }
            self.node_sockets[node_id] = sock

    def update_heartbeat(self, node_id: str):
        with self._lock:
            if node_id in self.connected_clients:
                self.connected_clients[node_id]["last_seen"] = time.time()

    def unregister(self, node_id: str):
        with self._lock:
            self.connected_clients.pop(node_id, None)
            self.node_sockets.pop(node_id, None)

    def get_stale_nodes(self) -> list:
        now = time.time()
        with self._lock:
            return [
                nid for nid, info in self.connected_clients.items()
                if now - info["last_seen"] > TIMEOUT_NODE
            ]

    def send_command(self, node_id: str, cmd_bytes: bytes) -> bool:
        with self._lock:
            sock = self.node_sockets.get(node_id)
        if sock:
            try:
                sock.sendall(cmd_bytes)
                return True
            except Exception:
                return False
        return False

    def snapshot(self) -> dict:
        with self._lock:
            return {nid: dict(info) for nid, info in self.connected_clients.items()}


state = SharedState()


# ─── Handler d'un client (exécuté dans le pool de threads) ────────────────────

def handle_client(conn: socket.socket, addr, pool: ConnectionPool):
    node_id = None
    buffer  = ""
    logger.info("Nouvelle connexion : %s", addr)

    try:
        conn.settimeout(TIMEOUT_NODE + 10)
        while True:
            data = conn.recv(BUFFER_SIZE)
            if not data:
                break
            buffer += data.decode("utf-8", errors="replace")

            # Traitement ligne par ligne (délimiteur \n)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = decode_message(line)
                except ValueError as e:
                    logger.warning("Message invalide de %s : %s", addr, e)
                    conn.sendall(build_ack_message("ERROR", str(e)))
                    continue

                node_id = _process_message(msg, addr, conn, pool)
                conn.sendall(build_ack_message("OK"))

    except socket.timeout:
        logger.warning("Timeout client %s (node=%s)", addr, node_id)
    except ConnectionResetError:
        logger.info("Connexion fermée par %s (node=%s)", addr, node_id)
    except Exception as e:
        logger.error("Erreur handler %s : %s", addr, e, exc_info=True)
    finally:
        if node_id:
            state.unregister(node_id)
            log_event(pool, "WARNING", f"Nœud déconnecté : {node_id}", node_id)
            logger.info("Nœud %s déconnecté.", node_id)
        conn.close()


def _process_message(msg: dict, addr, conn, pool: ConnectionPool) -> str:
    """Traite un message reçu et retourne le node_id."""
    mtype   = msg["type"]
    payload = msg["payload"]
    ts      = msg.get("timestamp", datetime.utcnow().isoformat())
    node_id = payload.get("node_id", str(addr))

    # Mise à jour heartbeat
    state.update_heartbeat(node_id)

    if mtype == MSG_TYPE_HEARTBEAT:
        logger.debug("Heartbeat reçu de %s", node_id)

    elif mtype in (MSG_TYPE_METRICS, MSG_TYPE_ALERT):
        # Enregistrement/mise à jour du nœud
        upsert_node(pool, node_id, {
            "os":       payload.get("os"),
            "cpu_type": payload.get("cpu_type"),
            "ip":       str(addr[0]),
        })
        state.register(node_id, conn, addr)

        # Sauvegarde métriques
        insert_metrics(pool, node_id, ts, {
            "cpu":    payload.get("cpu"),
            "memory": payload.get("memory"),
            "disk":   payload.get("disk"),
            "uptime": payload.get("uptime"),
        })

        # Sauvegarde services et ports
        if "services" in payload:
            insert_services(pool, node_id, ts, payload["services"])
        if "ports" in payload:
            insert_ports(pool, node_id, ts, payload["ports"])

        # Traitement des alertes
        if mtype == MSG_TYPE_ALERT:
            metric    = payload.get("metric", "?")
            value     = payload.get("value", 0)
            threshold = payload.get("threshold", 90)
            insert_alert(pool, node_id, ts, metric, value, threshold)
            log_event(pool, "WARNING",
                      f"ALERTE {metric}={value}% (seuil={threshold}%) sur {node_id}",
                      node_id)
            logger.warning("⚠  ALERTE %s : %s=%.1f%%", node_id, metric, value)

        elif mtype == MSG_TYPE_METRICS:
            logger.info("Métriques reçues de %s — CPU=%.1f%% MEM=%.1f%%",
                        node_id,
                        payload.get("cpu", 0),
                        payload.get("memory", 0))

    elif mtype == MSG_TYPE_RESPONSE:
        cmd    = payload.get("command", "?")
        result = payload.get("result", "?")
        log_event(pool, "INFO", f"Réponse commande {cmd}: {result}", node_id)
        logger.info("Réponse commande de %s — %s: %s", node_id, cmd, result)

    return node_id


# ─── Watchdog : détection des nœuds en panne ──────────────────────────────────

def watchdog(pool: ConnectionPool, stop_event: threading.Event):
    logger.info("Watchdog démarré (timeout=%ds)", TIMEOUT_NODE)
    while not stop_event.is_set():
        stale = state.get_stale_nodes()
        for node_id in stale:
            logger.warning("🔴 Nœud EN PANNE (timeout) : %s", node_id)
            mark_node_down(pool, node_id)
            log_event(pool, "ERROR", f"Nœud en panne (timeout) : {node_id}", node_id)
            state.unregister(node_id)
        stop_event.wait(timeout=15)


# ─── Serveur principal ─────────────────────────────────────────────────────────

class SupervisionServer:

    def __init__(self, host=HOST, port=PORT):
        self.host       = host
        self.port       = port
        self.pool_bd    = ConnectionPool(
            os.path.join(os.path.dirname(__file__), "..", "db", "supervision.db")
        )
        self.stop_event = threading.Event()
        self._server_sock = None

    def start(self):
        init_db(self.pool_bd)

        # Watchdog dans un thread daemon
        wd = threading.Thread(
            target=watchdog, args=(self.pool_bd, self.stop_event), daemon=True
        )
        wd.start()

        self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server_sock.bind((self.host, self.port))
        self._server_sock.listen(50)
        logger.info("Serveur en écoute sur %s:%d (pool=%d threads)",
                    self.host, self.port, MAX_WORKERS)

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            try:
                while not self.stop_event.is_set():
                    try:
                        self._server_sock.settimeout(1.0)
                        conn, addr = self._server_sock.accept()
                        executor.submit(handle_client, conn, addr, self.pool_bd)
                    except socket.timeout:
                        continue
            except KeyboardInterrupt:
                logger.info("Arrêt du serveur demandé.")
            finally:
                self.stop()

    def stop(self):
        self.stop_event.set()
        if self._server_sock:
            self._server_sock.close()
        self.pool_bd.close_all()
        logger.info("Serveur arrêté.")

    def send_command_to_node(self, node_id: str, command: str, service: str = "") -> bool:
        from common.protocol import build_command_message
        cmd_bytes = build_command_message(command, service)
        ok = state.send_command(node_id, cmd_bytes)
        if ok:
            log_event(self.pool_bd, "INFO",
                      f"Commande {command} envoyée à {node_id} (service={service})",
                      node_id)
        return ok

    def get_connected_nodes(self) -> dict:
        return state.snapshot()

    def get_all_nodes_db(self) -> list:
        return get_all_nodes(self.pool_bd)


if __name__ == "__main__":
    srv = SupervisionServer()
    srv.start()
