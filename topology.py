#!/usr/bin/env python3
"""Mininet topology: 3 switches in a triangle (two paths between s1 and s2).

  p1, p2 hang off s1; the broker b1 hangs off s2; sub1, sub2 hang off s3.
  s1-s2 is the direct (bottleneck, 5 Mbps) path; s1-s3-s2 is the alternate path.
Run interactively:  sudo python3 topology.py   (controller must already be running)
"""
from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController


def build(ctrl_ip="127.0.0.1", ctrl_port=6653, bottleneck_mbps=5):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=True)
    net.addController("c0", controller=RemoteController, ip=ctrl_ip, port=ctrl_port)
    s1 = net.addSwitch("s1", dpid="0000000000000001", protocols="OpenFlow13")
    s2 = net.addSwitch("s2", dpid="0000000000000002", protocols="OpenFlow13")
    s3 = net.addSwitch("s3", dpid="0000000000000003", protocols="OpenFlow13")
    p1 = net.addHost("p1", ip="10.0.0.1/24")
    p2 = net.addHost("p2", ip="10.0.0.2/24")
    b1 = net.addHost("b1", ip="10.0.0.3/24")
    sub1 = net.addHost("sub1", ip="10.0.0.4/24")
    sub2 = net.addHost("sub2", ip="10.0.0.5/24")
    edge = dict(bw=50, delay="1ms")
    # Add links in port order so switch port numbers match routing.py
    net.addLink(p1, s1, port1=0, port2=1, **edge)
    net.addLink(p2, s1, port1=0, port2=2, **edge)
    net.addLink(b1, s2, port1=0, port2=1, **edge)
    net.addLink(sub1, s3, port1=0, port2=1, **edge)
    net.addLink(sub2, s3, port1=0, port2=2, **edge)
    net.addLink(s1, s2, port1=3, port2=2, bw=bottleneck_mbps, delay="5ms", max_queue_size=100)
    net.addLink(s1, s3, port1=4, port2=3, bw=50, delay="5ms")
    net.addLink(s2, s3, port1=3, port2=4, bw=50, delay="5ms")
    return net


if __name__ == "__main__":
    setLogLevel("info")
    net = build()
    net.start()
    try:
        net.waitConnected()
    except Exception:
        pass
    net.staticArp()   # no ARP flooding needed (the triangle has a loop)
    CLI(net)
    net.stop()
