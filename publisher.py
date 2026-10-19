#!/usr/bin/env python3
"""Publisher: sends timestamped messages for one topic to the broker at a fixed rate."""
import argparse
import json
import os
import signal
import socket
import time

sent = 0


def connect(host, port, tries=15):
    for _ in range(tries):
        try:
            s = socket.create_connection((host, port), timeout=3)
            s.settimeout(None)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return s
        except OSError:
            time.sleep(0.5)
    return None


def main():
    global sent
    ap = argparse.ArgumentParser()
    ap.add_argument("--broker", required=True)
    ap.add_argument("--port", type=int, default=9000)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--rate", type=float, default=50, help="messages per second")
    ap.add_argument("--duration", type=float, default=10)
    ap.add_argument("--size", type=int, default=100, help="payload bytes")
    ap.add_argument("--persistent", action="store_true", help="reconnect if the broker drops us")
    ap.add_argument("--out", help="write JSON stats here")
    a = ap.parse_args()

    def write_stats():
        if a.out:
            with open(a.out, "w") as f:
                json.dump({"topic": a.topic, "sent": sent, "rate": a.rate}, f)

    def on_term(*_):
        write_stats()
        os._exit(0)

    signal.signal(signal.SIGTERM, on_term)

    s = connect(a.broker, a.port)
    if s is None:
        print("[pub] could not connect", flush=True)
        write_stats()
        return
    payload = "x" * a.size
    interval = 1.0 / a.rate
    end = time.time() + a.duration
    next_t = time.perf_counter()
    seq = 0
    last_write = 0
    while time.time() < end:
        msg = {"type": "PUB", "topic": a.topic, "seq": seq, "ts": time.time(), "payload": payload}
        try:
            s.sendall((json.dumps(msg) + "\n").encode())
            sent += 1
            seq += 1
        except OSError:
            if not a.persistent:
                break
            s.close()
            time.sleep(0.1)
            s = connect(a.broker, a.port, tries=3)
            if s is None:
                continue
        next_t += interval
        delay = next_t - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        if time.time() - last_write > 1:
            write_stats()
            last_write = time.time()
    write_stats()
    print(f"[pub] topic={a.topic} sent={sent}", flush=True)


if __name__ == "__main__":
    main()
