"""
Point d'entrée principal — lance le serveur + interface d'admin.
Usage :
  python main.py server          # démarrer le serveur
  python main.py agent           # démarrer un agent (connexion à 127.0.0.1:9000)
  python main.py agent --host X --port 9000 --node-id monNoeud
  python main.py gui             # lancer l'interface (GUI ou CLI)
  python main.py gui --cli       # forcer le mode CLI
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def run_server():
    from server.server import SupervisionServer
    srv = SupervisionServer()
    srv.start()


def run_agent(host, port, node_id):
    from agent.agent import SupervisionAgent
    agent = SupervisionAgent(host, port, node_id)
    agent.start()


def run_gui(cli_mode: bool):
    import sys
    if cli_mode:
        sys.argv.append("--cli")
    import runpy
    runpy.run_path(
        os.path.join(os.path.dirname(__file__), "server", "gui.py"),
        run_name="__main__"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Système de supervision réseau")
    sub = parser.add_subparsers(dest="command")

    # Serveur
    sub.add_parser("server", help="Démarrer le serveur de supervision")

    # Agent
    p_agent = sub.add_parser("agent", help="Démarrer un agent")
    p_agent.add_argument("--host",    default="127.0.0.1")
    p_agent.add_argument("--port",    default=9000, type=int)
    p_agent.add_argument("--node-id", default=None)

    # Interface
    p_gui = sub.add_parser("gui", help="Interface d'administration")
    p_gui.add_argument("--cli", action="store_true", help="Forcer le mode CLI")

    args = parser.parse_args()

    if args.command == "server":
        run_server()
    elif args.command == "agent":
        run_agent(args.host, args.port, args.node_id)
    elif args.command == "gui":
        run_gui(args.cli)
    else:
        parser.print_help()
