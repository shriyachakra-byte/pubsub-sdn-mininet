#!/usr/bin/env python3
"""Background UDP traffic generator (used to congest a link). No server needed."""
import argparse
import socket
import time

ap = argparse.ArgumentParser()
ap.add_argument("--dst", required=True)
ap.add_argument("--port", type=int, default=5001)
ap.add_argument("--mbps", type=float, default=10)
ap.add_argument("--duration", type=float, default=10)
a = ap.parse_args()

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
pkt = b"x" * 1400
interval = len(pkt) * 8 / (a.mbps * 1e6)
end = time.time() + a.duration
nxt = time.perf_counter()
while time.time() < end:
    try:
        s.sendto(pkt, (a.dst, a.port))
    except OSError:
        pass
    nxt += interval
    d = nxt - time.perf_counter()
    if d > 0:
        time.sleep(d)
