# Système Distribué de Supervision Réseau

**Projet Systèmes Répartis — M1 SRIV UN-CHK — Session 1 Fév/Mars 2026**

---

## 📁 Structure du projet

```
supervision_project/
├── common/
│   └── protocol.py        # Protocole applicatif JSON (messages, encodage/décodage)
├── server/
│   ├── server.py          # Serveur multi-clients (ThreadPoolExecutor)
│   ├── database.py        # Base de données SQLite + pool de connexions (Queue)
│   └── gui.py             # Interface GUI (tkinter) ou CLI (console)
├── agent/
│   └── agent.py           # Agent de supervision (client TCP)
├── db/                    # Base de données générée automatiquement
├── logs/                  # Fichiers de logs
├── main.py                # Point d'entrée principal
├── load_test.py           # Script de test de charge
├── requirements.txt
└── README.md
```

---

## ⚙️ Prérequis

- Python 3.8+
- `psutil` (optionnel — métriques réelles ; sinon simulation automatique)

```bash
pip install -r requirements.txt
```

---

## 🚀 Lancement

### 1. Démarrer le serveur

```bash
python main.py server
```

Le serveur écoute sur `0.0.0.0:9000` par défaut.

### 2. Démarrer un ou plusieurs agents

Dans un ou plusieurs terminaux séparés :

```bash
# Agent sur la machine locale
python main.py agent

# Agent avec paramètres personnalisés
python main.py agent --host 192.168.1.10 --port 9000 --node-id mon-serveur-web

# Simuler plusieurs agents (scripts séparés ou boucle shell)
for i in $(seq 1 5); do
    python main.py agent --node-id "node-$i" &
done
```

### 3. Lancer l'interface d'administration

```bash
# Interface GUI (tkinter) — lancée automatiquement si disponible
python main.py gui

# Forcer le mode CLI (console)
python main.py gui --cli
```

### 4. Test de charge

```bash
# 10 clients simultanés pendant 60 secondes
python load_test.py --clients 10 --duration 60

# 50 clients
python load_test.py --clients 50 --duration 120

# 100 clients
python load_test.py --clients 100 --duration 120
```

---

## 🏗️ Architecture

```
┌──────────────┐  socket TCP  ┌──────────────────────────────────────┐
│   AGENT 1    │ ──────────► │                                      │
├──────────────┤              │           SERVEUR CENTRAL            │
│   AGENT 2    │ ──────────► │   ┌─────────────────────────────┐   │
├──────────────┤              │   │  ThreadPoolExecutor (N=20)  │   │
│     ...      │ ──────────► │   │  (un thread par client TCP) │   │
├──────────────┤              │   └──────────────┬──────────────┘   │
│   AGENT n    │ ──────────► │                  │                   │
└──────────────┘              │   ┌──────────────▼──────────────┐   │
                              │   │  Queue-based ConnectionPool  │   │
                              │   │  (pool de 5 connexions BD)   │   │
                              │   └──────────────┬──────────────┘   │
                              │                  │                   │
                              └──────────────────┼───────────────────┘
                                                 │
                                    ┌────────────▼───────────┐
                                    │   SQLite (supervision   │
                                    │       .db)              │
                                    └─────────────────────────┘
```

---

## 📡 Protocole de communication

Tous les messages sont encodés en **JSON**, délimités par `\n`.

### Format général

```json
{
  "type": "METRICS",
  "timestamp": "2026-03-11T10:00:00.123456",
  "payload": { ... }
}
```

### Types de messages

| Type        | Sens           | Description                            |
|-------------|----------------|----------------------------------------|
| `METRICS`   | Agent → Serveur| Métriques périodiques (toutes les 10s) |
| `ALERT`     | Agent → Serveur| Alerte seuil dépassé (>90%)            |
| `HEARTBEAT` | Agent → Serveur| Signal de vie (toutes les 30s)         |
| `ACK`       | Serveur → Agent| Accusé de réception                    |
| `COMMAND`   | Serveur → Agent| Commande (UP, STATUS)                  |
| `RESPONSE`  | Agent → Serveur| Réponse à une commande                 |

### Exemple de message METRICS

```json
{
  "type": "METRICS",
  "timestamp": "2026-03-11T10:30:00",
  "payload": {
    "node_id": "serveur-web-1",
    "os": "Linux 5.15",
    "cpu_type": "Intel Core i7",
    "cpu": 42.5,
    "memory": 67.3,
    "disk": 55.0,
    "uptime": 86400,
    "services": {
      "ssh": "OK", "http": "OK", "https": "FAIL",
      "firefox": "FAIL", "vlc": "OK", "thunderbird": "FAIL"
    },
    "ports": {
      "22": "OPEN", "80": "OPEN", "443": "CLOSED", "8080": "CLOSED"
    }
  }
}
```

---

## 🗄️ Base de données (SQLite)

| Table           | Contenu                             |
|-----------------|-------------------------------------|
| `nodes`         | Nœuds enregistrés (ID, OS, statut)  |
| `metrics`       | Historique CPU/MEM/DISK/uptime      |
| `service_status`| Statut des services par nœud        |
| `port_status`   | Statut des ports surveillés         |
| `alerts`        | Alertes dépassement de seuil        |
| `event_logs`    | Journal des événements              |

---

## 🔄 Choix techniques — Pool de threads

### Comparaison

| Critère                 | `ThreadPoolExecutor`       | `Queue-based pool`          |
|-------------------------|----------------------------|-----------------------------|
| API                     | Haut niveau (futures)      | Bas niveau (manuel)         |
| Connexions longue durée | ⚠️ Non optimisé            | ✅ Idéal                    |
| Contrôle précis         | ❌ Limité                  | ✅ Total                    |
| Usage BD                | Possible                   | ✅ Recommandé (FIFO, borné) |

**Choix retenu :**
- `ThreadPoolExecutor` pour les **handlers clients** (connexions TCP entrantes)
- `Queue-based ConnectionPool` pour les **accès à la base de données**

---

## 📊 Services surveillés

| Service       | Type    | Port/Processus |
|---------------|---------|----------------|
| SSH           | Réseau  | Port 22        |
| HTTP          | Réseau  | Port 80        |
| HTTPS         | Réseau  | Port 443       |
| Firefox       | App     | processus      |
| VLC           | App     | processus      |
| Thunderbird   | App     | processus      |

**Ports surveillés :** 22, 80, 443, 8080

---

## 🔗 Dépôt Git

[À compléter par l'étudiant : lien GitHub/GitLab]

---

## 👤 Auteur(s)

[Nom — Prénom — Matricule]
