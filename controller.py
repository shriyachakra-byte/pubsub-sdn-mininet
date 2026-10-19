"""Ryu (OpenFlow 1.3) controller for the Pub-Sub SDN project.

Run:  SDN_MODE=dynamic ryu-manager controller.py      (or SDN_MODE=static for the baseline)

What it does
  * Proactively installs IP forwarding rules on all 3 switches (topology known from routing.py).
  * Classifies topic traffic by TCP port: 9001 = priority class (higher-priority flow entries).
  * dynamic mode only:
      - monitors link utilisation (port stats every 2 s); if a link is congested, the
        priority class is re-routed over the alternate path
      - reacts to link failure (OFPT_PORT_STATUS) by re-routing all traffic
      - receives BLOCK events from the broker (Unix socket) and installs DROP rules for the publisher
  * static mode: installs the same initial rules but never changes them (baseline).
"""
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.lib import hub
from ryu.ofproto import ofproto_v1_3

from routing import HOSTS, LINKS, PORT_TO_LINK, PRIO_PORT, next_port

CTRL_SOCK = "/tmp/sdn_ctrl.sock"
PRIO_NORMAL, PRIO_CLASS, PRIO_BLOCK = 10, 20, 100


class PubSubSDN(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.mode = os.environ.get("SDN_MODE", "dynamic")
        self.cong_hi = float(os.environ.get("CONG_HI_BPS", 3e6))   # mark congested above
        self.cong_lo = float(os.environ.get("CONG_LO_BPS", 1.5e6))  # clear below
        self.dps = {}
        self.down = set()        # failed links (frozenset of two dpids)
        self.congested = set()   # congested links
        self.blocked = set()     # blocked publisher IPs
        self.topics = {}         # topic -> set(subscriber ip)
        self.prev, self.rate = {}, {}
        self.logger.info("controller started, mode=%s", self.mode)
        hub.spawn(self._ctrl_server)
        hub.spawn(self._monitor)

    # ------------------------------------------------------------ switches
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        self.dps[dp.id] = dp
        self.logger.info("switch s%s connected", dp.id)
        self.install(dp)

    def add_flow(self, dp, prio, match, out_port):
        ofp, p = dp.ofproto, dp.ofproto_parser
        inst = []
        if out_port is not None:
            inst = [p.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, [p.OFPActionOutput(out_port)])]
        dp.send_msg(p.OFPFlowMod(datapath=dp, priority=prio, match=match, instructions=inst))

    def install(self, dp):
        p = dp.ofproto_parser
        dyn = self.mode == "dynamic"
        avoid_norm = frozenset(self.down) if dyn else frozenset()
        avoid_prio = frozenset(self.down | self.congested) if dyn else frozenset()
        for ip, (hd, hp) in HOSTS.items():
            port = next_port(dp.id, hd, hp, avoid_norm)
            self.add_flow(dp, PRIO_NORMAL, p.OFPMatch(eth_type=0x0800, ipv4_dst=ip), port)
            port = next_port(dp.id, hd, hp, avoid_prio)
            # priority topic class: client->broker (tcp_dst) and broker->client (tcp_src)
            self.add_flow(dp, PRIO_CLASS,
                          p.OFPMatch(eth_type=0x0800, ip_proto=6, ipv4_dst=ip, tcp_dst=PRIO_PORT), port)
            self.add_flow(dp, PRIO_CLASS,
                          p.OFPMatch(eth_type=0x0800, ip_proto=6, ipv4_dst=ip, tcp_src=PRIO_PORT), port)
        for ip in self.blocked:
            self.add_flow(dp, PRIO_BLOCK, p.OFPMatch(eth_type=0x0800, ipv4_src=ip), None)

    def reinstall_all(self):
        for dp in list(self.dps.values()):
            self.install(dp)

    # ------------------------------------------------------- link failure
    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status(self, ev):
        msg = ev.msg
        dp, port = msg.datapath, msg.desc
        link = PORT_TO_LINK.get((dp.id, port.port_no))
        if link is None or self.mode != "dynamic":
            return
        ofp = dp.ofproto
        is_down = bool(port.state & ofp.OFPPS_LINK_DOWN) or bool(port.config & ofp.OFPPC_PORT_DOWN)
        if is_down and link not in self.down:
            self.down.add(link)
            self.logger.info("LINK DOWN %s -> re-routing", sorted(link))
            self.reinstall_all()
        elif not is_down and link in self.down:
            self.down.discard(link)
            self.logger.info("LINK UP %s -> restoring paths", sorted(link))
            self.reinstall_all()

    # --------------------------------------------------------- monitoring
    def _monitor(self):
        while True:
            hub.sleep(2)
            for dp in list(self.dps.values()):
                p = dp.ofproto_parser
                dp.send_msg(p.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY))

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _stats(self, ev):
        dp, now = ev.msg.datapath, time.time()
        for st in ev.msg.body:
            key = (dp.id, st.port_no)
            if key not in PORT_TO_LINK:
                continue
            prev = self.prev.get(key)
            if prev and now - prev[0] > 0:
                self.rate[key] = (st.tx_bytes - prev[1]) * 8 / (now - prev[0])
            self.prev[key] = (now, st.tx_bytes)
        if self.mode == "dynamic":
            self._evaluate()

    def _evaluate(self):
        changed = False
        for (a, b), (pa, pb) in LINKS.items():
            link = frozenset((a, b))
            r = max(self.rate.get((a, pa), 0), self.rate.get((b, pb), 0))
            if link not in self.congested and r > self.cong_hi:
                self.congested.add(link)
                self.logger.info("CONGESTION on link s%s-s%s (%.1f Mbps) -> priority class rerouted", a, b, r / 1e6)
                changed = True
            elif link in self.congested and r < self.cong_lo:
                self.congested.discard(link)
                self.logger.info("link s%s-s%s recovered (%.1f Mbps) -> priority class back", a, b, r / 1e6)
                changed = True
        if changed:
            self.reinstall_all()

    # ----------------------------------------- events from the broker (app)
    def _ctrl_server(self):
        if os.path.exists(CTRL_SOCK):
            os.unlink(CTRL_SOCK)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(CTRL_SOCK)
        os.chmod(CTRL_SOCK, 0o777)
        srv.listen(16)
        while True:
            conn, _ = srv.accept()
            hub.spawn(self._ctrl_client, conn)

    def _ctrl_client(self, conn):
        try:
            with conn.makefile("r") as f:
                for line in f:
                    try:
                        self._on_app_event(json.loads(line))
                    except ValueError:
                        pass
        finally:
            conn.close()

    def _on_app_event(self, ev):
        kind = ev.get("event")
        if kind == "SUBSCRIBE":
            self.topics.setdefault(ev["topic"], set()).add(ev["ip"])
            self.logger.info("APP: %s subscribed to '%s' via port %s", ev["ip"], ev["topic"], ev.get("port"))
        elif kind == "BLOCK":
            if self.mode != "dynamic":
                self.logger.info("APP: BLOCK %s ignored (static mode)", ev["ip"])
                return
            self.blocked.add(ev["ip"])
            self.logger.info("APP: BLOCK %s (%s) -> installing DROP rules", ev["ip"], ev.get("reason"))
            self.reinstall_all()
