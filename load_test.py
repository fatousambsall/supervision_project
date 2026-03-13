"""
Script de test de charge — simule N agents simultanés.
Usage : python load_test.py --clients 10 --duration 60
"""

import socket
import threading
import time
import random
import json
import argparse
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.protocol import build_metrics_message, build_heartbeat_message

HOST = "127.0.0.1"
PORT = 9000

results = {"sent": 0, "errors": 0, "latencies": []}
lock    = threading.Lock()


def fake_agent(client_id: int, duration: int, stop_event: threading.Event):
    node_id = f"load-node-{client_id:04d}"
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((HOST, PORT))
        sock.settimeout(5)
    except Exception as e:
        with lock:
            results["errors"] += 1
        return

    end_time = time.time() + duration
    while not stop_event.is_set() and time.time() < end_time:
        payload = {
            "os":       "Linux SimTest",
            "cpu_type": "SimCPU",
            "cpu":      round(random.uniform(10, 95), 1),
            "memory":   round(random.uniform(20, 90), 1),
            "disk":     round(random.uniform(30, 80), 1),
            "uptime":   random.randint(100, 100000),
            "services": {"ssh": "OK", "http": random.choice(["OK", "FAIL"])},
            "ports":    {"22": "OPEN", "80": random.choice(["OPEN", "CLOSED"])},
        }
        data = build_metrics_message(node_id, payload)
        try:
            t0 = time.time()
            sock.sendall(data)
            sock.recv(512)
            latency = (time.time() - t0) * 1000  # ms
            with lock:
                results["sent"]      += 1
                results["latencies"].append(latency)
        except Exception:
            with lock:
                results["errors"] += 1

        time.sleep(random.uniform(5, 15))

    sock.close()


def main():
    parser = argparse.ArgumentParser(description="Test de charge supervision")
    parser.add_argument("--clients",  type=int, default=10,  help="Nombre de clients")
    parser.add_argument("--duration", type=int, default=60,  help="Durée du test (secondes)")
    parser.add_argument("--host",     default="127.0.0.1")
    parser.add_argument("--port",     type=int, default=9000)
    args = parser.parse_args()

    global HOST, PORT
    HOST = args.host
    PORT = args.port

    print(f"\n🔥 Test de charge : {args.clients} clients, {args.duration}s\n")
    stop_event = threading.Event()
    threads    = []

    start = time.time()
    for i in range(args.clients):
        t = threading.Thread(
            target=fake_agent, args=(i, args.duration, stop_event), daemon=True
        )
        threads.append(t)
        t.start()
        time.sleep(0.05)  # légère rampe de montée

    for t in threads:
        t.join(timeout=args.duration + 10)

    elapsed = time.time() - start
    lats    = results["latencies"]
    avg_lat = sum(lats) / len(lats) if lats else 0
    max_lat = max(lats) if lats else 0
    throughput = results["sent"] / elapsed if elapsed > 0 else 0

    print("─" * 50)
    print(f"✅ Messages envoyés  : {results['sent']}")
    print(f"❌ Erreurs           : {results['errors']}")
    print(f"⏱  Latence moyenne   : {avg_lat:.1f} ms")
    print(f"⏱  Latence max       : {max_lat:.1f} ms")
    print(f"📈 Débit             : {throughput:.1f} msg/s")
    print(f"⏳ Durée totale      : {elapsed:.1f}s")
    print("─" * 50)


if __name__ == "__main__":
    main()
