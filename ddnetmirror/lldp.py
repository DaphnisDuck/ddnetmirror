"""Report LLDP links between selected devices from CloudVision Sysdb state.

Uses the Connector approach of the official cloudvision-python example
examples/Connector/get_lldp_nei.py. Nothing is inferred: a link is reported
only when both endpoints and both interface names come straight from LLDP data.
"""

import sys

from cloudvision.Connector.codec import Wildcard
from cloudvision.Connector.grpc_client import GRPCClient, create_query

PORT_STATUS = ["Sysdb", "l2discovery", "lldp", "status", "local", Wildcard(), "portStatus", Wildcard()]
REMOTE_SYSTEM = PORT_STATUS + ["remoteSystem", Wildcard()]


def read_lldp(client, serial):
    """Return (ports, neighbors) for one device, or None if it has no LLDP state.

    ports: LLDP port names (the Sysdb portStatus keys).
    neighbors: {(local port, remote key): merged remoteSystem fields}, one entry
    per remote system, so several neighbors on one port stay separate.
    """
    query = create_query([(PORT_STATUS, []), (REMOTE_SYSTEM, [])], serial)
    ports, neighbors, seen = set(), {}, False
    for batch in client.get([query]):
        for notif in batch["notifications"]:
            seen = True
            path = notif["path_elements"]
            if len(path) == len(PORT_STATUS):
                ports.add(path[7])
            elif len(path) == len(REMOTE_SYSTEM) and notif["updates"]:
                ports.add(path[7])
                neighbors.setdefault((path[7], path[9]), {}).update(notif["updates"])
    return (ports, neighbors) if seen else None


def remote_endpoint(fields):
    """Return (sysName, portId) from remoteSystem fields, or None if missing/malformed."""
    try:
        name = fields["sysName"]["value"]["value"]
        port = fields["msap"]["portIdentifier"]["portId"]
    except (KeyError, TypeError):
        return None
    if isinstance(name, str) and name and isinstance(port, str) and port:
        return name, port
    return None


def shown(value):
    """Escape control characters (C0, DEL, C1) in an LLDP value for display."""
    return "".join(
        f"\\x{ord(c):02x}" if ord(c) < 0x20 or 0x7F <= ord(c) <= 0x9F else c
        for c in str(value)
    )


def find_links(hostnames, lldp):
    """Build unique links between selected devices.

    hostnames: inventory hostnames of the selected devices (may repeat).
    lldp: {hostname: read_lldp() result} for unambiguous hostnames.
    Returns (links, skipped): links as [((host, intf), (host, intf), kind)],
    skipped as human-readable reasons.
    """
    ambiguous = {h for h in hostnames if hostnames.count(h) > 1}
    skipped = [f"{h}: hostname shared by several selected devices" for h in sorted(ambiguous)]
    observed = set()  # ((host, intf), (host, intf)) as seen from the first endpoint
    for host, data in sorted(lldp.items()):
        if data is None:
            skipped.append(f"{host}: LLDP data unavailable")
            continue
        _, neighbors = data
        for (port, _), fields in sorted(neighbors.items(), key=lambda kv: str(kv[0])):
            where = f"{host} {shown(port)}"
            remote = remote_endpoint(fields)
            if not isinstance(port, str) or not port or remote is None:
                skipped.append(f"{where}: neighbor without sysName/port id")
                continue
            name, remote_port = remote
            if name in ambiguous:
                skipped.append(f"{where} -> {shown(name)}: ambiguous hostname")
            elif name not in lldp:
                skipped.append(f"{where} -> {shown(name)} {shown(remote_port)}: not in selection")
            elif lldp[name] is None or remote_port not in lldp[name][0]:
                skipped.append(
                    f"{where} -> {name} {shown(remote_port)}: "
                    f"not an LLDP interface reported by {name}"
                )
            else:
                observed.add(((host, port), (name, remote_port)))

    pairs = {tuple(sorted(o)) for o in observed}
    use = {}
    for pair in pairs:
        for end in pair:
            use[end] = use.get(end, 0) + 1
    links = []
    for a, b in sorted(pairs):
        if use[a] > 1 or use[b] > 1:
            skipped.append(
                f"{a[0]} {shown(a[1])} <-> {b[0]} {shown(b[1])}: interface seen in several links"
            )
            continue
        kind = "reciprocal" if (a, b) in observed and (b, a) in observed else "one-sided"
        links.append((a, b, kind))
    return links, skipped


def report_links(grpc_addr, token, ca_file, devices):
    """Query LLDP for devices [(hostname, serial)] and print the link report."""
    hostnames = [h for h, _ in devices]
    lldp = {}
    with GRPCClient(grpc_addr, tokenValue=token, ca=ca_file) as client:
        for host, serial in devices:
            if hostnames.count(host) > 1:
                continue
            try:
                lldp[host] = read_lldp(client, serial)
            except Exception as exc:
                print(f"LLDP query failed for {host} ({serial}): {exc}", file=sys.stderr)
                lldp[host] = None
    links, skipped = find_links(hostnames, lldp)
    print(f"LLDP links between selected devices: {len(links)}")
    for (ah, ai), (bh, bi), kind in links:
        print(f"{ah} {ai} <-> {bh} {bi} [{kind}]")
    for reason in skipped:
        print(f"skipped LLDP: {reason}", file=sys.stderr)
