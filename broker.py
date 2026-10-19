#!/usr/bin/env python3
"""Pub-Sub broker over raw TCP sockets.

Protocol: one JSON object per line ("\n" terminated).
  client -> broker : {"type":"SUB","topic":"t"}   {"type":"UNSUB","topic":"t"}
                     {"type":"PUB","topic":"t","payload":"...","seq":1,"ts":1.23}
  broker -> client : {"type":"ACK",...}  {"type":"MSG","topic":..,"payload":..,"seq":..,"ts":..,"pub":ip}
                     {"type":"ERR","reason":".."}

The broker listens on several TCP ports. Port 9000 = normal traffic class,
port 9001 = priority traffic class. The SDN controller classifies traffic by
these ports. The broker also tells the controller (over a Unix socket) about
subscriptions and about publishers that exceed the rate limit (BLOCK).
"""
import argparse
import collections
import json
import os
import signal
import socket
import threading
import time

CTRL_SOCK = "/tmp/sdn_ctrl.sock"


def notify_controller(event):
    """Send one JSON event to the SDN controller (out-of-band control channel)."""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(CTRL_SOCK)
        s.sendall((json.dumps(event) + "\n").encode())
        s.close()
    except OSError as e:
        print(f"[broker] controller notify failed ({e})", flush=True)


class Client:
    def __init__(self, conn, addr, port):
        self.conn, self.ip, self.port = conn, addr[0], port
        self.lock = threading.Lock()

    def send(self, obj):
        data = (json.dumps(obj) + "\n").encode()
        with self.lock:
            self.conn.sendall(data)


class Broker:
    def __init__(self, rate_limit):
        self.subs = collections.defaultdict(set)   # topic -> set(Client)
        self.mu = threading.Lock()
        self.rate_limit = rate_limit
        self.windows = {}                          # ip -> [window_start, count]
        self.blocked = set()
        self.stats = {"published": 0, "delivered": 0, "blocked_ips": 0}

    # ---- helpers -------------------------------------------------------
    def over_limit(self, ip):
        now = time.time()
        with self.mu:
            w = self.windows.setdefault(ip, [now, 0])
            if now - w[0] >= 1.0:
                w[0], w[1] = now, 0
            w[1] += 1
            return w[1] > self.rate_limit

    def block(self, ip):
        with self.mu:
            if ip in self.blocked:
                return
            self.blocked.add(ip)
            self.stats["blocked_ips"] += 1
        print(f"[broker] BLOCK {ip}: rate limit {self.rate_limit} msg/s exceeded", flush=True)
        notify_controller({"event": "BLOCK", "ip": ip, "reason": "rate limit exceeded"})

    def remove(self, cl):
        with self.mu:
            for subs in self.subs.values():
                subs.discard(cl)

    def publish(self, msg, pub_ip):
        topic = msg.get("topic")
        out = {"type": "MSG", "topic": topic, "payload": msg.get("payload"),
               "seq": msg.get("seq"), "ts": msg.get("ts"), "pub": pub_ip}
        with self.mu:
            targets = list(self.subs.get(topic, ()))
            self.stats["published"] += 1
        for c in targets:
            try:
                c.send(out)
                with self.mu:
                    self.stats["delivered"] += 1
            except OSError:
                self.remove(c)

    # ---- per-connection thread -----------------------------------------
    def handle(self, conn, addr, port):
        ip = addr[0]
        if ip in self.blocked:
            conn.close()
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        cl = Client(conn, addr, port)
        try:
            for line in conn.makefile("rb"):
                try:
                    msg = json.loads(line)
                except ValueError:
                    cl.send({"type": "ERR", "reason": "bad json"})
                    continue
                t = msg.get("type")
                if t == "SUB":
                    topic = msg.get("topic")
                    with self.mu:
                        self.subs[topic].add(cl)
                    cl.send({"type": "ACK", "op": "SUB", "topic": topic})
                    print(f"[broker] {ip} subscribed '{topic}' (port {port})", flush=True)
                    notify_controller({"event": "SUBSCRIBE", "ip": ip, "topic": topic, "port": port})
                elif t == "UNSUB":
                    topic = msg.get("topic")
                    with self.mu:
                        self.subs[topic].discard(cl)
                    cl.send({"type": "ACK", "op": "UNSUB", "topic": topic})
                elif t == "PUB":
                    if self.over_limit(ip):
                        self.block(ip)
                        break
                    self.publish(msg, ip)
                else:
                    cl.send({"type": "ERR", "reason": "unknown type"})
        except OSError:
            pass
        finally:
            self.remove(cl)
            try:
                conn.close()
            except OSError:
                pass

    def serve(self, bind, port):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((bind, port))
        srv.listen(128)
        print(f"[broker] listening on {bind}:{port}", flush=True)
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=self.handle, args=(conn, addr, port), daemon=True).start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--ports", type=int, nargs="+", default=[9000, 9001])
    ap.add_argument("--rate-limit", type=int, default=500,
                    help="max PUB messages per second per publisher IP before BLOCK")
    a = ap.parse_args()
    broker = Broker(a.rate_limit)

    def done(*_):
        print(f"[broker] stats: {broker.stats}", flush=True)
        os._exit(0)

    signal.signal(signal.SIGTERM, done)
    signal.signal(signal.SIGINT, done)
    for p in a.ports:
        threading.Thread(target=broker.serve, args=(a.bind, p), daemon=True).start()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
