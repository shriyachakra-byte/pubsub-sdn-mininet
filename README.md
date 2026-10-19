# Publish-Subscribe Messaging System over TCP + SDN (Mininet + Ryu)

PES University - Computer Networks (UE25CS243A) - Jackfruit mini project, topic 9.

## 1. What is this project? (read this first, you need it for the viva)

**Pub-Sub** = publishers send messages labelled with a *topic* (e.g. `alerts`) to a **broker**.
Subscribers tell the broker which topics they want; the broker forwards each message only to those subscribers.
Publishers and subscribers never talk to each other directly.

**TCP sockets** = the reliable byte-stream connections that publishers/subscribers use to talk to the broker.
Our messages are one JSON object per line (our *application-layer protocol*).

**SDN (Software Defined Networking)** = switches don't decide anything themselves. A central **controller**
(Ryu) tells each switch which flow rules to use (OpenFlow). So the controller can change the path of
traffic while the system runs.

**Mininet** = creates a virtual network (hosts + switches) on your one VM so we can test all of this.

### Architecture

```
 p1 (alerts pub) ─┐                         ┌─ sub1 (subscribes 'alerts' on port 9001)
                  s1 ═══ 5 Mbps ═══ s2(b1 = BROKER)
 p2 (news pub)  ──┘     \            /
                         s3 ────────┘── sub2 (subscribes 'news' on port 9000)
                    (alternate path s1-s3-s2)

              Ryu controller (OpenFlow 1.3)  <── events from broker (Unix socket)
```

- **Topic classes (traffic classification):** broker port **9000 = normal**, **9001 = priority**.
  The controller installs higher-priority flow entries for TCP port 9001.
- **Dynamic SDN behaviour** (controller, `SDN_MODE=dynamic`):
  1. *Congestion:* polls port statistics every 2 s. If the s1-s2 link is loaded (>3 Mbps), priority traffic is moved to s1-s3-s2.
  2. *Link failure:* on OpenFlow port-down event, all traffic is re-routed around the dead link.
  3. *Application event -> network:* broker detects a publisher exceeding the rate limit and tells the controller, which installs a DROP rule (blocking).
- **Baseline:** `SDN_MODE=static` installs the same initial rules but never changes them.

### Files

| File | What it does |
|---|---|
| `broker.py` | TCP broker, threads per connection, topics, rate limit, notifies controller |
| `publisher.py` / `subscriber.py` | Clients; messages carry a timestamp so subscribers measure latency |
| `bgtraffic.py` | UDP background traffic to congest a link |
| `routing.py` | Topology description + shortest-path with "avoid these links" |
| `controller.py` | Ryu SDN controller |
| `topology.py` | Mininet topology (3 switches, 5 hosts) |
| `run_experiments.py` | Runs static vs dynamic in 4 scenarios, writes `results/results.csv` + plot |

## 2. Setup from zero (Ubuntu VM)

1. Install the VM (VirtualBox/VMware, Ubuntu 22.04), give it 2+ CPUs, 4 GB RAM, NAT network.
2. Update and install Mininet (from your lab manual):
   ```
   sudo apt update && sudo apt upgrade -y
   sudo apt install mininet openvswitch-switch git python3-pip -y
   ```
3. Verify Mininet: `sudo mn --test pingall` should say `0% dropped`. Clean up with `sudo mn -c`.
4. Install the Ryu controller (system-wide, because Mininet needs sudo):
   ```
   sudo pip3 install eventlet==0.30.2 ryu
   ```
   (Ubuntu 24.04: add `--break-system-packages`. If Ryu fails to import on a newer Python, use Ubuntu 22.04 / Python 3.10 or lower.)
   Check: `ryu-manager --version`.
   **Ubuntu 24.04/26.04 (Python 3.12+), where Ryu will not install:** run Ryu in its own Python 3.9 environment:
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh      # then close and reopen the terminal
   uv venv --python 3.9 ~/ryu-env
   uv pip install --python ~/ryu-env/bin/python "setuptools<68" wheel "eventlet==0.30.2" ryu
   ~/ryu-env/bin/ryu-manager --version
   ```
   Start the controller with `SDN_MODE=static ~/ryu-env/bin/ryu-manager controller.py`, and run the experiments with
   `sudo RYU_MANAGER=$HOME/ryu-env/bin/ryu-manager python3 run_experiments.py`.
5. Optional for the plot: `sudo apt install python3-matplotlib -y`.
6. Get the code: `git clone <your-repo-url>` and `cd` into it.

## 3. Quick manual demo (do this first, 10 minutes)

Terminal 1 - controller:
```
cd <project folder>
SDN_MODE=dynamic ryu-manager controller.py
```
Terminal 2 - network:
```
sudo python3 topology.py
mininet> pingall                     # expect 0% dropped
mininet> b1 python3 broker.py > b.log 2>&1 &
mininet> sub1 python3 subscriber.py --broker 10.0.0.3 --port 9001 --topics alerts --duration 60 --verbose > s1.log 2>&1 &
mininet> p1 python3 publisher.py --broker 10.0.0.3 --port 9001 --topic alerts --rate 20 --duration 20
mininet> sh cat s1.log | tail            # subscriber got messages with latencies
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1     # see the flow rules the controller installed
```
Show the **link failure**: while the publisher runs, type `link s1 s2 down` in the Mininet CLI; the
controller log prints `LINK DOWN ... re-routing` and messages keep arriving. Bring it back with `link s1 s2 up`.

Show **blocking**: `p2 python3 publisher.py --broker 10.0.0.3 --port 9000 --topic news --rate 3000 --duration 5`
-> broker prints `BLOCK 10.0.0.2`, controller installs DROP rules, then `p2 ping -c 2 b1` fails.

## 4. Run the full experiments (produces the performance numbers)

Stop everything (`exit`, Ctrl-C), then:
```
sudo python3 run_experiments.py --duration 20
```
~10 minutes. Output: console table, `results/results.csv`, `results/comparison.png`, plus logs per run
(`results/<scenario>_<mode>/controller.log` shows the reroute decisions - useful screenshots).

Scenarios: `normal`, `congestion` (10 Mbps UDP over the 5 Mbps link), `link_failure` (s1-s2 down after 1/3 of the run), `flood` (rogue publisher).
Metrics: mean and p95 latency of topic `alerts`, delivery ratio, longest gap between messages, and whether the rogue host is isolated.
**Baseline = static** (same rules, never updated) vs **proposed = dynamic**.

## 5. Troubleshooting

- `ryu-manager: command not found` under sudo -> reinstall Ryu with `sudo pip3`.
- Switch ports differ from `routing.py` -> `sudo ovs-ofctl -O OpenFlow13 show s1`; port 3 must be the link to s2, 4 to s3.
- Stale network state -> `sudo mn -c`. Port 6653 busy -> `sudo pkill -f ryu-manager`.
- pingall fails right after start -> wait 3-5 s for the switches to connect to the controller.
- Controller log shows nothing from the broker -> the broker and controller must run on the same VM (they use `/tmp/sdn_ctrl.sock`).

## 6. Viva cheat-sheet

- Why TCP? Reliable, ordered delivery; pub-sub messages must not be lost.
- How is concurrency handled? One thread per connection in the broker; a lock per subscriber for sends.
- How does the controller know a topic's class? By TCP port (9001 vs 9000) - flow match on `tcp_dst/tcp_src`.
- Why static ARP? The triangle has a loop; flooding ARP would loop forever without spanning tree.
- Why a Unix socket between broker and controller? Mininet hosts are separate network namespaces but share the filesystem; it is an out-of-band control channel (like a management network).
- Limitations: latency uses one machine clock (fine in Mininet); rate-limit is per publisher IP; congestion threshold is configured for the 5 Mbps bottleneck.
