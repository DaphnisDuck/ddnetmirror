"""Render a Containerlab topology from collected configs and accepted LLDP links."""

import json
import re

from ddnetmirror.lldp import shown

# Containerlab requires the container name clab-<lab>-<node> to match
# ^[a-zA-Z0-9][a-zA-Z0-9-._]+$ (nodes/default_node.go). Lab and node names are
# held to the same character set and must start with a letter or digit.
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# Brief link endpoints whose node part (case-insensitive) is one of these become
# special links instead of a node link (links/link_brief.go, links/link.go).
RESERVED_NODE_NAMES = {"host", "mgmt-net", "macvlan"}
EOS_ETHERNET_RE = re.compile(r"Ethernet([1-9][0-9]*(?:/[1-9][0-9]*){0,2})")


def valid_name(name):
    return bool(NAME_RE.fullmatch(name))


def reserved_node_name(name):
    return name.lower() in RESERVED_NODE_NAMES


def ceos_interface(eos_name):
    """Ethernet1 -> eth1, Ethernet49/1 -> eth49_1, Ethernet49/1/1 -> eth49_1_1, else None."""
    m = EOS_ETHERNET_RE.fullmatch(eos_name)
    return "eth" + m.group(1).replace("/", "_") if m else None


def build(lab_name, image, nodes, links, header):
    """Return (yaml_text, omitted_link_reasons).

    nodes: {node name: startup-config path relative to the topology file}.
    links: accepted LLDP links [((host, eos_intf), (host, eos_intf), kind)].
    header: comment lines placed at the top of the file.
    """
    omitted = []
    endpoints = []
    for (ah, ai), (bh, bi), _ in links:
        where = f"{shown(ah)} {shown(ai)} <-> {shown(bh)} {shown(bi)}"
        missing = [h for h in (ah, bh) if h not in nodes]
        if missing:
            omitted.append(f"{where}: no saved config for {', '.join(map(shown, missing))}")
            continue
        a, b = ceos_interface(ai), ceos_interface(bi)
        if a is None or b is None:
            bad = ai if a is None else bi
            omitted.append(f"{where}: cannot translate {shown(bad)} to a cEOS interface")
            continue
        endpoints.append((f"{ah}:{a}", f"{bh}:{b}"))

    q = json.dumps  # a JSON string is a valid YAML double-quoted scalar
    lines = [f"# {line}" for line in header]
    lines += [f"name: {q(lab_name)}", "topology:", "  nodes:"]
    for name in sorted(nodes):
        lines += [
            f"    {q(name)}:",
            "      kind: arista_ceos",
            f"      image: {q(image)}",
            f"      startup-config: {q(nodes[name])}",
        ]
    lines.append("  links:" if endpoints else "  links: []")
    for a, b in sorted(endpoints):
        lines.append(f"    - endpoints: [{q(a)}, {q(b)}]")
    return "\n".join(lines) + "\n", omitted
