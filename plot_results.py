#!/usr/bin/env python3
"""Make results/comparison.png from results/results.csv (static vs dynamic SDN).

Usage:  python3 plot_results.py            (run in the project folder)
Needs matplotlib:  sudo apt install python3-matplotlib -y
"""
import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

rows = list(csv.DictReader(open("results/results.csv")))
scen = ["normal", "congestion", "link_failure", "flood"]
labels = ["normal", "congestion", "link failure", "flood"]


def val(s, m, key):
    for r in rows:
        if r["scenario"] == s and r["mode"] == m:
            return float(r[key])
    return 0.0


STATIC, DYNAMIC = "#8a8f98", "#1f6feb"
fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 4.2))
x = range(len(scen))
for ax, key, title, ylab in (
    (a1, "mean_ms", "Mean latency of 'alerts' (lower is better)", "milliseconds (log scale)"),
    (a2, "delivery", "Delivery ratio of 'alerts' (higher is better)", "fraction of messages received"),
):
    s = [val(c, "static", key) for c in scen]
    d = [val(c, "dynamic", key) for c in scen]
    b1 = ax.bar([i - 0.2 for i in x], s, 0.4, label="static (baseline)", color=STATIC)
    b2 = ax.bar([i + 0.2 for i in x], d, 0.4, label="dynamic SDN", color=DYNAMIC)
    for bars in (b1, b2):
        for b in bars:
            h = b.get_height()
            ax.annotate(f"{h:g}" if key == "delivery" else f"{h:,.1f}",
                        (b.get_x() + b.get_width() / 2, h), ha="center", va="bottom", fontsize=8,
                        xytext=(0, 2), textcoords="offset points")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_title(title, fontsize=11)
    ax.set_ylabel(ylab)
    ax.spines[["top", "right"]].set_visible(False)
a1.set_yscale("log")
a1.set_ylim(top=a1.get_ylim()[1] * 3)
a2.set_ylim(0, 1.15)
a1.legend(frameon=False, loc="upper right")
fig.tight_layout()
fig.savefig("results/comparison.png", dpi=160)
print("wrote results/comparison.png")
