#!/usr/bin/env python3
"""Automated experiments: static baseline vs dynamic SDN, under 4 scenarios.

Usage:  sudo python3 run_experiments.py [--duration 20]
Starts/stops the Ryu controller itself. Results -> results/results.csv (+ results/comparison.png if matplotlib exists).
"""
import argparse
import csv
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time

from mininet.log import setLogLevel

from topology import build

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
SCENARIOS = ["normal", "congestion", "link_failure", "flood"]
MODES = ["static", "dynamic"]


def start(host, script, args, logfile):
    cmd = [PY, os.path.join(HERE, script)] + shlex.split(args)
    return host.popen(cmd, stdout=open(logfile, "w"), stderr=subprocess.STDOUT)


def finish(proc, timeout):
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def start_controller(mode, logfile):
    exe = os.environ.get("RYU_MANAGER") or shutil.which("ryu-manager")
    if not exe:
        sys.exit("ryu-manager not found. Install Ryu (see README step 4) or set RYU_MANAGER=/path/to/ryu-manager.")
    p = subprocess.Popen([exe, "--ofp-tcp-listen-port", "6653", os.path.join(HERE, "controller.py")],
                         env=dict(os.environ, SDN_MODE=mode), stdout=open(logfile, "w"),
                         stderr=subprocess.STDOUT, preexec_fn=os.setsid)
    time.sleep(5)
    return p


def analyze(csv_path, pub_json):
    res = {"mean_ms": "", "p95_ms": "", "delivery": "", "max_gap_s": ""}
    try:
        sent = json.load(open(pub_json))["sent"]
        rows = list(csv.DictReader(open(csv_path)))
    except (OSError, ValueError, KeyError):
        return res
    lat = sorted(float(r["latency_ms"]) for r in rows)
    times = sorted(float(r["recv_time"]) for r in rows)
    if lat:
        res["mean_ms"] = round(sum(lat) / len(lat), 2)
        res["p95_ms"] = round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 2)
        res["max_gap_s"] = round(max([b - a for a, b in zip(times, times[1:])] or [0]), 2)
    res["delivery"] = round(len(lat) / sent, 3) if sent else 0
    return res


def run(mode, scenario, outdir, dur):
    os.makedirs(outdir, exist_ok=True)
    subprocess.call(["mn", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ctrl = start_controller(mode, f"{outdir}/controller.log")
    net = build()
    net.start()
    try:
        net.waitConnected()
    except Exception:
        pass
    net.staticArp()
    time.sleep(3)
    p1, p2, b1, sub1, sub2 = [net.get(n) for n in ("p1", "p2", "b1", "sub1", "sub2")]
    extra = {}
    try:
        broker = start(b1, "broker.py", "--ports 9000 9001 --rate-limit 500", f"{outdir}/broker.log")
        time.sleep(1)
        sa = start(sub1, "subscriber.py", f"--broker 10.0.0.3 --port 9001 --topics alerts --duration {dur + 6} "
                   f"--out {outdir}/alerts.csv", f"{outdir}/sub_alerts.log")
        sn = start(sub2, "subscriber.py", f"--broker 10.0.0.3 --port 9000 --topics news --duration {dur + 6} "
                   f"--out {outdir}/news.csv", f"{outdir}/sub_news.log")
        time.sleep(1)
        pa = start(p1, "publisher.py", f"--broker 10.0.0.3 --port 9001 --topic alerts --rate 50 "
                   f"--duration {dur} --out {outdir}/alerts_pub.json", f"{outdir}/pub_alerts.log")
        pn = start(p2, "publisher.py", f"--broker 10.0.0.3 --port 9000 --topic news --rate 50 "
                   f"--duration {dur} --out {outdir}/news_pub.json", f"{outdir}/pub_news.log")
        bg = rogue = None
        if scenario == "congestion":
            time.sleep(2)
            bg = start(p2, "bgtraffic.py", f"--dst 10.0.0.3 --mbps 10 --duration {dur - 4}", f"{outdir}/bg.log")
        elif scenario == "link_failure":
            time.sleep(dur / 3)
            net.configLinkStatus("s1", "s2", "down")
        elif scenario == "flood":
            time.sleep(4)
            rogue = start(p2, "publisher.py", f"--broker 10.0.0.3 --port 9000 --topic news --rate 4000 "
                          f"--duration 8 --persistent", f"{outdir}/rogue.log")
            time.sleep(10)
            out = p2.cmd("ping -c 3 -W 1 10.0.0.3")
            extra["rogue_ping_loss"] = [l for l in out.splitlines() if "packet loss" in l][-1:] or ["?"]
            extra["rogue_ping_loss"] = extra["rogue_ping_loss"][0].split(",")[2].strip() \
                if len(extra["rogue_ping_loss"][0].split(",")) > 2 else "?"
        for pr in (pa, pn, rogue, bg):
            if pr:
                finish(pr, dur + 5)
        finish(sa, 10)
        finish(sn, 10)
        broker.terminate()
    finally:
        net.stop()
        os.killpg(os.getpgid(ctrl.pid), signal.SIGTERM)
        time.sleep(1)
    res = analyze(f"{outdir}/alerts.csv", f"{outdir}/alerts_pub.json")
    res.update(mode=mode, scenario=scenario, **extra)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=int, default=20)
    ap.add_argument("--scenarios", nargs="+", default=SCENARIOS)
    a = ap.parse_args()
    setLogLevel("warning")
    os.chdir(HERE)
    results = []
    for sc in a.scenarios:
        for mode in MODES:
            print(f"=== scenario={sc} mode={mode}", flush=True)
            r = run(mode, sc, f"results/{sc}_{mode}", a.duration)
            print(r, flush=True)
            results.append(r)
    cols = ["scenario", "mode", "mean_ms", "p95_ms", "delivery", "max_gap_s", "rogue_ping_loss"]
    with open("results/results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore", restval="")
        w.writeheader()
        w.writerows(results)
    print("\nscenario       mode     mean_ms  p95_ms  delivery  max_gap_s")
    for r in results:
        print(f"{r['scenario']:<14} {r['mode']:<8} {r['mean_ms']!s:<8} {r['p95_ms']!s:<7} "
              f"{r['delivery']!s:<9} {r['max_gap_s']}")
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        scs = [s for s in a.scenarios if s != "flood"]
        fig, ax = plt.subplots(figsize=(7, 4))
        for i, mode in enumerate(MODES):
            vals = [float(next((r["mean_ms"] for r in results if r["scenario"] == s and r["mode"] == mode), 0) or 0)
                    for s in scs]
            ax.bar([x + i * 0.4 for x in range(len(scs))], vals, 0.4, label=mode)
        ax.set_xticks([x + 0.2 for x in range(len(scs))])
        ax.set_xticklabels(scs)
        ax.set_ylabel("mean latency of 'alerts' topic (ms)")
        ax.set_yscale("log")
        ax.legend()
        fig.tight_layout()
        fig.savefig("results/comparison.png", dpi=150)
    except ImportError:
        print("(matplotlib not installed: skipping plot)")


if __name__ == "__main__":
    main()
