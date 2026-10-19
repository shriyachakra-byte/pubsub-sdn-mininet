"""Static description of the topology + path computation (pure Python, easy to test).

Switch ports (must match topology.py):
  s1: 1=p1  2=p2  3->s2  4->s3
  s2: 1=b1  2->s1 3->s3
  s3: 1=sub1 2=sub2 3->s1 4->s2
"""
from collections import deque

PRIO_PORT = 9001   # TCP port of the "priority" topic class

# ip -> (switch dpid, switch port the host hangs off)
HOSTS = {
    "10.0.0.1": (1, 1),   # p1   publisher (alerts)
    "10.0.0.2": (1, 2),   # p2   publisher (news)
    "10.0.0.3": (2, 1),   # b1   broker
    "10.0.0.4": (3, 1),   # sub1 subscriber
    "10.0.0.5": (3, 2),   # sub2 subscriber
}

# (switch a, switch b): (port on a, port on b)
LINKS = {
    (1, 2): (3, 2),
    (1, 3): (4, 3),
    (2, 3): (3, 4),
}

ADJ = {1: {}, 2: {}, 3: {}}
PORT_TO_LINK = {}
for (a, b), (pa, pb) in LINKS.items():
    ADJ[a][b] = pa
    ADJ[b][a] = pb
    PORT_TO_LINK[(a, pa)] = frozenset((a, b))
    PORT_TO_LINK[(b, pb)] = frozenset((a, b))


def next_port(src, dst_dpid, dst_port, avoid=frozenset()):
    """Output port on switch `src` for traffic towards host (dst_dpid, dst_port),
    using the shortest path that does not use any link in `avoid`. None = unreachable."""
    if src == dst_dpid:
        return dst_port
    q = deque([(src, None)])
    seen = {src}
    while q:
        node, first = q.popleft()
        for nb, port in ADJ[node].items():
            if nb in seen or frozenset((node, nb)) in avoid:
                continue
            f = first if first is not None else port
            if nb == dst_dpid:
                return f
            seen.add(nb)
            q.append((nb, f))
    return None
