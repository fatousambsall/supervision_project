"""
Agent de supervision — collecte les métriques système et les envoie au serveur.
Fonctionne en mode réel (psutil) ou simulé si psutil n'est pas disponible.
"""

import socket
import threading
import logging
import os
import sys
import time
import platform
import random
import subprocess
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol import (
    build_metrics_message, build_alert_message,
    build_heartbeat_message, decode_message,
    MSG_TYPE_COMMAND, MSG_TYPE_ACK,
    SEND_INTERVAL, HEARTBEAT_INTERVAL,
    THRESHOLD_CPU, THRESHOLD_MEM, THRESHOLD_DISK,
    MONITORED_SERVICES, MONITORED_PORTS,
)

# Tentative d'import psutil (métriques réelles)
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

LOG_DIR = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOG_DIR, "agent.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("agent")


# ─── Collecte des métriques ────────────────────────────────────────────────────

def collect_system_info() -> dict:
    """Informations statiques du nœud."""
    return {
        "os":       platform.system() + " " + platform.release(),
        "cpu_type": platform.processor() or "Unknown",
    }


def collect_metrics_real() -> dict:
    """Collecte réelle via psutil."""
    cpu    = psutil.cpu_percent(interval=1)
    mem    = psutil.virtual_memory().percent
    disk   = psutil.disk_usage("/").percent
    uptime = int(time.time() - psutil.boot_time())
    return {"cpu": cpu, "memory": mem, "disk": disk, "uptime": uptime}


def collect_metrics_simulated() -> dict:
    """Simulation avec valeurs aléatoires réalistes."""
    # Tendance lente pour simuler un vrai comportement
    cpu    = round(random.gauss(45, 20), 1)
    mem    = round(random.gauss(55, 15), 1)
    disk   = round(random.gauss(60, 10), 1)
    uptime = int(time.time() % 86400)   # pseudo-uptime
    return {
        "cpu":    max(0, min(100, cpu)),
        "memory": max(0, min(100, mem)),
        "disk":   max(0, min(100, disk)),
        "uptime": uptime,
    }


def collect_metrics() -> dict:
    return collect_metrics_real() if PSUTIL_AVAILABLE else collect_metrics_simulated()


def check_service_real(service_name: str, info: dict) -> str:
    """Vérifie si un service/application tourne (Linux/Windows)."""
    if info["type"] == "network":
        # Vérification par port TCP local
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        result = s.connect_ex(("127.0.0.1", info["port"]))
        s.close()
        return "OK" if result == 0 else "FAIL"
    else:
        # Vérification par nom de processus
        if PSUTIL_AVAILABLE:
            procs = [p.name().lower() for p in psutil.process_iter(["name"])]
            return "OK" if info["process"] in procs else "FAIL"
        return random.choice(["OK", "FAIL"])


def check_services() -> dict:
    statuses = {}
    for name, info in MONITORED_SERVICES.items():
        try:
            statuses[name] = check_service_real(name, info)
        except Exception:
            statuses[name] = "FAIL"
    return statuses


def check_ports() -> dict:
    statuses = {}
    for port in MONITORED_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        result = s.connect_ex(("127.0.0.1", port))
        s.close()
        statuses[str(port)] = "OPEN" if result == 0 else "CLOSED"
    return statuses


def start_service_locally(service_name: str) -> str:
    """Tente de démarrer un service (Linux systemctl)."""
    try:
        result = subprocess.run(
            ["systemctl", "start", service_name],
            capture_output=True, text=True, timeout=10
        )
        return "OK" if result.returncode == 0 else f"FAIL: {result.stderr.strip()}"
    except Exception as e:
        return f"FAIL: {e}"


# ─── Agent ─────────────────────────────────────────────────────────────────────

class SupervisionAgent:

    def __init__(self, server_host: str, server_port: int, node_id: str = None):
        self.server_host = server_host
        self.server_port = server_port
        self.node_id     = node_id or f"node-{platform.node()}"
        self.stop_event  = threading.Event()
        self._sock       = None
        self._lock       = threading.Lock()
        self._sys_info   = collect_system_info()
        logger.info("Agent initialisé : %s → %s:%d (psutil=%s)",
                    self.node_id, server_host, server_port, PSUTIL_AVAILABLE)

    # ── Connexion ──────────────────────────────────────────────────────────────

    def _connect(self):
        while not self.stop_event.is_set():
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.connect((self.server_host, self.server_port))
                self._sock = sock
                logger.info("Connecté au serveur %s:%d", self.server_host, self.server_port)
                return True
            except ConnectionRefusedError:
                logger.warning("Serveur indisponible, retry dans 5s...")
                time.sleep(5)
        return False

    def _send(self, data: bytes) -> bool:
        with self._lock:
            if not self._sock:
                return False
            try:
                self._sock.sendall(data)
                # Lecture de l'ACK
                ack_raw = self._sock.recv(1024).decode("utf-8", errors="replace")
                for line in ack_raw.splitlines():
                    if line.strip():
                        try:
                            ack = decode_message(line)
                            if ack["payload"].get("status") != "OK":
                                logger.warning("ACK non-OK : %s", ack["payload"])
                        except ValueError:
                            pass
                return True
            except Exception as e:
                logger.error("Erreur envoi : %s", e)
                self._sock = None
                return False

    # ── Threads de collecte ────────────────────────────────────────────────────

    def _metrics_loop(self):
        while not self.stop_event.is_set():
            if not self._sock:
                if not self._connect():
                    break

            metrics  = collect_metrics()
            services = check_services()
            ports    = check_ports()

            payload = {
                **self._sys_info,
                **metrics,
                "services": services,
                "ports":    ports,
            }

            # Envoi métriques normales
            self._send(build_metrics_message(self.node_id, payload))

            # Envoi alertes si seuils dépassés
            for metric, threshold in [
                ("cpu",    THRESHOLD_CPU),
                ("memory", THRESHOLD_MEM),
                ("disk",   THRESHOLD_DISK),
            ]:
                if metrics[metric] > threshold:
                    logger.warning("⚠  Seuil dépassé %s=%.1f%%", metric, metrics[metric])
                    self._send(build_alert_message(self.node_id, metric, metrics[metric]))

            self.stop_event.wait(timeout=SEND_INTERVAL)

    def _heartbeat_loop(self):
        while not self.stop_event.is_set():
            if self._sock:
                self._send(build_heartbeat_message(self.node_id))
            self.stop_event.wait(timeout=HEARTBEAT_INTERVAL)

    def _command_listener(self):
        """Écoute les commandes envoyées par le serveur."""
        buf = ""
        while not self.stop_event.is_set():
            if not self._sock:
                time.sleep(2)
                continue
            try:
                self._sock.settimeout(2.0)
                data = self._sock.recv(1024)
                if not data:
                    continue
                buf += data.decode("utf-8", errors="replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = decode_message(line)
                        if msg["type"] == MSG_TYPE_COMMAND:
                            self._handle_command(msg["payload"])
                    except ValueError:
                        pass
            except socket.timeout:
                continue
            except Exception:
                pass

    def _handle_command(self, payload: dict):
        cmd     = payload.get("command", "")
        service = payload.get("service", "")
        logger.info("Commande reçue : %s (service=%s)", cmd, service)
        if cmd == "UP" and service:
            result = start_service_locally(service)
            logger.info("Résultat démarrage %s : %s", service, result)
        elif cmd == "STATUS":
            result = str(check_services())
        else:
            result = f"Commande inconnue : {cmd}"
        from common.protocol import build_response_message
        self._send(build_response_message(self.node_id, cmd, result))

    # ── Démarrage ──────────────────────────────────────────────────────────────

    def start(self):
        if not self._connect():
            logger.error("Impossible de se connecter. Arrêt.")
            return

        threads = [
            threading.Thread(target=self._metrics_loop,    daemon=True),
            threading.Thread(target=self._heartbeat_loop,  daemon=True),
            threading.Thread(target=self._command_listener, daemon=True),
        ]
        for t in threads:
            t.start()

        try:
            while not self.stop_event.is_set():
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Arrêt de l'agent.")
        finally:
            self.stop()

    def stop(self):
        self.stop_event.set()
        if self._sock:
            self._sock.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent de supervision")
    parser.add_argument("--host",    default="127.0.0.1",  help="Adresse du serveur")
    parser.add_argument("--port",    default=9000, type=int, help="Port du serveur")
    parser.add_argument("--node-id", default=None,          help="Identifiant du nœud")
    args = parser.parse_args()

    agent = SupervisionAgent(args.host, args.port, args.node_id)
    agent.start()
