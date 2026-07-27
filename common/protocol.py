"""
Protocole de communication JSON pour le système de supervision.
Définit la structure des messages échangés entre agents et serveur.
"""

import json
import time
from datetime import datetime

# ─── Types de messages ────────────────────────────────────────────────────────
MSG_TYPE_METRICS   = "METRICS"    # Agent → Serveur : métriques périodiques
MSG_TYPE_ALERT     = "ALERT"      # Agent → Serveur : alerte seuil dépassé
MSG_TYPE_ACK       = "ACK"        # Serveur → Agent : accusé de réception
MSG_TYPE_COMMAND   = "COMMAND"    # Serveur → Agent : commande (ex: UP service)
MSG_TYPE_RESPONSE  = "RESPONSE"   # Agent → Serveur : réponse à une commande
MSG_TYPE_HEARTBEAT = "HEARTBEAT"  # Agent → Serveur : signal de vie

# ─── Fréquence d'envoi (secondes) ────────────────────────────────────────────
SEND_INTERVAL      = 10   # envoi métriques toutes les 10 secondes
HEARTBEAT_INTERVAL = 30   # heartbeat toutes les 30 secondes
TIMEOUT_NODE       = 90   # nœud considéré en panne après 90 s sans données

# ─── Seuils d'alerte ─────────────────────────────────────────────────────────
THRESHOLD_CPU      = 90   # % CPU
THRESHOLD_MEM      = 90   # % mémoire
THRESHOLD_DISK     = 90   # % disque

# ─── Services et ports surveillés ────────────────────────────────────────────
MONITORED_SERVICES = {
    # Services réseau
    "ssh":    {"type": "network", "port": 22},
    "http":   {"type": "network", "port": 80},
    "https":  {"type": "network", "port": 443},
    # Applications grand public
    "firefox":  {"type": "app", "process": "firefox"},
    "vlc":      {"type": "app", "process": "vlc"},
    "thunderbird": {"type": "app", "process": "thunderbird"},
}

MONITORED_PORTS = [22, 80, 443, 8080]

# ─── Encodage / Décodage des messages ────────────────────────────────────────

def encode_message(msg_type: str, payload: dict) -> bytes:
    """Sérialise un message en JSON + délimiteur newline."""
    message = {
        "type":      msg_type,
        "timestamp": datetime.utcnow().isoformat(),
        "payload":   payload,
    }
    return (json.dumps(message) + "\n").encode("utf-8")


def decode_message(raw: str) -> dict:
    """Désérialise un message JSON. Lève ValueError si format invalide."""
    try:
        msg = json.loads(raw.strip())
        if "type" not in msg or "payload" not in msg:
            raise ValueError("Champs obligatoires manquants (type, payload)")
        return msg
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON invalide : {e}")


def build_metrics_message(node_id: str, metrics: dict) -> bytes:
    return encode_message(MSG_TYPE_METRICS, {"node_id": node_id, **metrics})


def build_alert_message(node_id: str, metric: str, value: float) -> bytes:
    return encode_message(MSG_TYPE_ALERT, {
        "node_id": node_id,
        "metric":  metric,
        "value":   value,
        "threshold": globals()[f"THRESHOLD_{metric.upper()}"],
    })


def build_heartbeat_message(node_id: str) -> bytes:
    return encode_message(MSG_TYPE_HEARTBEAT, {"node_id": node_id})


def build_ack_message(status: str = "OK", info: str = "") -> bytes:
    return encode_message(MSG_TYPE_ACK, {"status": status, "info": info})


def build_command_message(command: str, target_service: str = "") -> bytes:
    return encode_message(MSG_TYPE_COMMAND, {
        "command": command,          # ex: "UP", "DOWN", "STATUS"
        "service": target_service,
    })


def build_response_message(node_id: str, command: str, result: str) -> bytes:
    return encode_message(MSG_TYPE_RESPONSE, {
        "node_id": node_id,
        "command": command,
        "result":  result,
    })
