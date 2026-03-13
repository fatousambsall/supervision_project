"""
Interface d'administration du serveur de supervision.
Mode GUI (tkinter) ou CLI selon disponibilité.
Permet de consulter les nœuds, métriques, alertes et d'envoyer des commandes.
"""

import os
import sys
import threading
import time
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.database import (
    ConnectionPool, init_db,
    get_all_nodes, get_latest_metrics,
    get_metrics_history, get_active_alerts, get_recent_logs,
)

logger = logging.getLogger("gui")

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "db", "supervision.db")


# ─── Interface CLI (toujours disponible) ──────────────────────────────────────

class CLIInterface:

    MENU = """
╔══════════════════════════════════════════╗
║     SUPERVISION RÉSEAU — Console         ║
╠══════════════════════════════════════════╣
║  1. Lister tous les nœuds                ║
║  2. Métriques en temps réel d'un nœud    ║
║  3. Historique métriques d'un nœud       ║
║  4. Alertes actives                      ║
║  5. Journal des événements               ║
║  6. Envoyer commande à un nœud           ║
║  0. Quitter                              ║
╚══════════════════════════════════════════╝
Choix : """

    def __init__(self, pool: ConnectionPool, server=None):
        self.pool   = pool
        self.server = server   # instance SupervisionServer pour envoyer des commandes

    def run(self):
        while True:
            try:
                choice = input(self.MENU).strip()
            except (EOFError, KeyboardInterrupt):
                print("\nAu revoir.")
                break

            if choice == "1":
                self._show_nodes()
            elif choice == "2":
                self._show_latest_metrics()
            elif choice == "3":
                self._show_history()
            elif choice == "4":
                self._show_alerts()
            elif choice == "5":
                self._show_logs()
            elif choice == "6":
                self._send_command()
            elif choice == "0":
                print("Au revoir.")
                break
            else:
                print("Option invalide.")

    def _show_nodes(self):
        nodes = get_all_nodes(self.pool)
        if not nodes:
            print("\n  Aucun nœud enregistré.\n")
            return
        print(f"\n{'ID nœud':<20} {'OS':<20} {'Statut':<8} {'Dernière vue':<25}")
        print("─" * 75)
        for n in nodes:
            status_icon = "🟢" if n["status"] == "ACTIVE" else "🔴"
            print(f"{n['node_id']:<20} {str(n['os']):<20} "
                  f"{status_icon} {n['status']:<6} {n['last_seen']:<25}")
        print()

    def _show_latest_metrics(self):
        node_id = input("  Node ID : ").strip()
        m = get_latest_metrics(self.pool, node_id)
        if not m:
            print(f"  Aucune métrique pour '{node_id}'.\n")
            return
        print(f"\n  Métriques de {node_id} à {m['timestamp']} :")
        print(f"  CPU    : {m['cpu']:.1f}%")
        print(f"  Mémoire: {m['memory']:.1f}%")
        print(f"  Disque : {m['disk']:.1f}%")
        print(f"  Uptime : {m['uptime']}s\n")

    def _show_history(self):
        node_id = input("  Node ID : ").strip()
        rows = get_metrics_history(self.pool, node_id, limit=20)
        if not rows:
            print(f"  Pas d'historique pour '{node_id}'.\n")
            return
        print(f"\n{'Timestamp':<25} {'CPU%':>6} {'MEM%':>6} {'DISK%':>6} {'Uptime':>8}")
        print("─" * 55)
        for r in rows:
            print(f"{r['timestamp']:<25} {r['cpu']:>6.1f} {r['memory']:>6.1f} "
                  f"{r['disk']:>6.1f} {r['uptime']:>8}")
        print()

    def _show_alerts(self):
        alerts = get_active_alerts(self.pool)
        if not alerts:
            print("\n  Aucune alerte active. ✅\n")
            return
        print(f"\n{'Nœud':<20} {'Métrique':<10} {'Valeur':>8} {'Seuil':>8} {'Timestamp':<25}")
        print("─" * 75)
        for a in alerts:
            print(f"{a['node_id']:<20} {a['metric']:<10} "
                  f"{a['value']:>8.1f} {a['threshold']:>8.1f} {a['timestamp']:<25}")
        print()

    def _show_logs(self):
        logs = get_recent_logs(self.pool, limit=30)
        if not logs:
            print("\n  Journal vide.\n")
            return
        for l in logs:
            lvl  = l["level"]
            icon = {"INFO": "ℹ️ ", "WARNING": "⚠️ ", "ERROR": "🔴"}.get(lvl, "  ")
            node = l["node_id"] or "—"
            print(f"  {icon} [{l['timestamp']}] [{lvl:<7}] [{node:<15}] {l['message']}")
        print()

    def _send_command(self):
        if not self.server:
            print("  Interface non connectée au serveur.\n")
            return
        node_id = input("  Node ID cible : ").strip()
        print("  Commandes : UP <service>, STATUS")
        cmd_line = input("  Commande : ").strip().split(maxsplit=1)
        command = cmd_line[0].upper() if cmd_line else ""
        service = cmd_line[1] if len(cmd_line) > 1 else ""
        ok = self.server.send_command_to_node(node_id, command, service)
        print(f"  {'✅ Commande envoyée.' if ok else '❌ Échec (nœud non connecté).'}\n")


# ─── Interface GUI tkinter (optionnelle) ──────────────────────────────────────

def try_launch_gui(pool: ConnectionPool, server=None):
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except ImportError:
        return False

    root = tk.Tk()
    root.title("Supervision Réseau — Dashboard")
    root.geometry("900x600")

    style = ttk.Style()
    style.theme_use("clam")

    # ── Notebook (onglets) ────────────────────────────────────────────────────
    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=8, pady=8)

    # Onglet Nœuds
    frame_nodes = ttk.Frame(nb)
    nb.add(frame_nodes, text=" Nœuds ")

    cols_nodes = ("node_id", "os", "cpu_type", "status", "last_seen")
    tree_nodes = ttk.Treeview(frame_nodes, columns=cols_nodes, show="headings", height=15)
    for c in cols_nodes:
        tree_nodes.heading(c, text=c.replace("_", " ").title())
        tree_nodes.column(c, width=150)
    tree_nodes.pack(fill="both", expand=True, side="left")
    sb = ttk.Scrollbar(frame_nodes, orient="vertical", command=tree_nodes.yview)
    tree_nodes.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")

    # Onglet Alertes
    frame_alerts = ttk.Frame(nb)
    nb.add(frame_alerts, text=" Alertes ⚠ ")

    cols_alerts = ("node_id", "metric", "value", "threshold", "timestamp")
    tree_alerts = ttk.Treeview(frame_alerts, columns=cols_alerts, show="headings", height=15)
    for c in cols_alerts:
        tree_alerts.heading(c, text=c.replace("_", " ").title())
        tree_alerts.column(c, width=150)
    tree_alerts.pack(fill="both", expand=True, side="left")
    sb2 = ttk.Scrollbar(frame_alerts, orient="vertical", command=tree_alerts.yview)
    tree_alerts.configure(yscrollcommand=sb2.set)
    sb2.pack(side="right", fill="y")

    # Onglet Logs
    frame_logs = ttk.Frame(nb)
    nb.add(frame_logs, text=" Journal ")

    txt_logs = tk.Text(frame_logs, font=("Courier", 9), wrap="word", state="disabled")
    txt_logs.pack(fill="both", expand=True)

    # Onglet Commande
    frame_cmd = ttk.Frame(nb)
    nb.add(frame_cmd, text=" Commandes ")

    ttk.Label(frame_cmd, text="Node ID :").grid(row=0, column=0, padx=8, pady=8, sticky="w")
    entry_node = ttk.Entry(frame_cmd, width=30)
    entry_node.grid(row=0, column=1, padx=8, pady=8)

    ttk.Label(frame_cmd, text="Service :").grid(row=1, column=0, padx=8, sticky="w")
    entry_svc = ttk.Entry(frame_cmd, width=30)
    entry_svc.grid(row=1, column=1, padx=8)

    def send_up():
        nid = entry_node.get().strip()
        svc = entry_svc.get().strip()
        if server and nid:
            ok = server.send_command_to_node(nid, "UP", svc)
            messagebox.showinfo("Commande", "✅ Envoyée" if ok else "❌ Nœud non connecté")

    ttk.Button(frame_cmd, text="Envoyer UP", command=send_up).grid(row=2, column=1, pady=12)

    # ── Rafraîchissement périodique ───────────────────────────────────────────
    def refresh():
        # Nœuds
        for item in tree_nodes.get_children():
            tree_nodes.delete(item)
        for n in get_all_nodes(pool):
            tag = "down" if n["status"] == "DOWN" else ""
            tree_nodes.insert("", "end", values=(
                n["node_id"], n["os"], n["cpu_type"], n["status"], n["last_seen"]
            ), tags=(tag,))
        tree_nodes.tag_configure("down", background="#ffcccc")

        # Alertes
        for item in tree_alerts.get_children():
            tree_alerts.delete(item)
        for a in get_active_alerts(pool):
            tree_alerts.insert("", "end", values=(
                a["node_id"], a["metric"],
                f"{a['value']:.1f}%", f"{a['threshold']:.1f}%", a["timestamp"]
            ))

        # Logs
        txt_logs.configure(state="normal")
        txt_logs.delete("1.0", "end")
        for l in get_recent_logs(pool, limit=50):
            lvl  = l["level"]
            icon = {"INFO": "ℹ", "WARNING": "⚠", "ERROR": "✖"}.get(lvl, " ")
            txt_logs.insert("end",
                f"{icon} [{l['timestamp']}] [{lvl}] {l['message']}\n")
        txt_logs.configure(state="disabled")

        root.after(5000, refresh)  # rafraîchir toutes les 5 s

    refresh()
    root.mainloop()
    return True


# ─── Point d'entrée ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print("Base de données introuvable. Lancez d'abord le serveur.")
        sys.exit(1)

    pool = ConnectionPool(DB_PATH)
    init_db(pool)

    # Tentative GUI, repli sur CLI
    if "--cli" in sys.argv or not try_launch_gui(pool):
        cli = CLIInterface(pool)
        cli.run()
