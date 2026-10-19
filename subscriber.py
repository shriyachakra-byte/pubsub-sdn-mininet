#!/usr/bin/env python3
"""Subscriber: subscribes to topics and records per-message latency (ms)."""
import argparse
import csv
import json
import os
import signal
import socket
import time


def connect(host, port, tries=15):
    for _ in range(tries):
        try:
            return socket.create_connection((host, port), timeout=3)
        except OSError:
            time.sleep(0.5)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", required=True)
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--topics", required=True, help="comma separated")
    ap.add_argument("--duration", type=float, default=15)
    ap.add_argument("--out", help="CSV file: topic,seq,latency_ms,recv_time")
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()

    rows = []

    def dump():
        if a.out:
            with open(a.out, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["topic", "seq", "latency_ms", "recv_time"])
                w.writerows(rows)

    def on_term(*_):
        dump()
        os._exit(0)

    signal.signal(signal.SIGTERM, on_term)

    s = connect(a.broker, a.port)
    if s is None:
        print("[sub] could not connect", flush=True)
        dump()
        return
    for t in a.topics.split(","):
        s.sendall((json.dumps({"type": "SUB", "topic": t.strip()}) + "\n").encode())
    s.settimeout(1)
    deadline = time.time() + a.duration
    buf = b""
    while time.time() < deadline:
        try:
            chunk = s.recv(65536)
        except socket.timeout:
            continue
        except OSError:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                m = json.loads(line)
            except ValueError:
                continue
            if m.get("type") == "MSG":
                now = time.time()
                lat = (now - m["ts"]) * 1000.0
                rows.append((m["topic"], m["seq"], round(lat, 3), now))
                if a.verbose:
                    print(f"[sub] {m['topic']} seq={m['seq']} latency={lat:.2f} ms", flush=True)
    dump()
    print(f"[sub] received {len(rows)} messages", flush=True)


if __name__ == "__main__":
    main()
