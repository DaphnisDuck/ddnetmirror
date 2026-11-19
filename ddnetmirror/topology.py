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
# Containerlab replaces this in a startup-config path with the node name
# (core/config.go processStartupConfig, 0.83.0); the path is then resolved
# relative to the topology file's directory.
NODE_NAME_VAR = "__clabNodeName__"
EOS_ETHERNET_RE = re.compile(r"Ethernet([1-9][0-9]*(?:/[1-9][0-9]*){0,2})")


def valid_name(name):
    return bool(NAME_RE.fullmatch(name))


def reserved_node_name(name):
    return name.lower() in RESERVED_NODE_NAMES


def ceos_interface(eos_name):
    """Ethernet1 -> eth1, Ethernet49/1 -> eth49_1, Ethernet49/1/1 -> eth49_1_1, else None."""
    m = EOS_ETHERNET_RE.fullmatch(eos_name)
    return "eth" + m.group(1).replace("/", "_") if m else None


def translate_links(nodes, links):
    """Return (endpoints, omitted_link_reasons).

    nodes: names of the topology nodes.
    links: accepted LLDP links [((host, eos_intf), (host, eos_intf), kind)].
    endpoints: sorted [("host:ethX", "host:ethY", kind)] for links Containerlab can use.
    """
    omitted = []
    endpoints = []
    for (ah, ai), (bh, bi), kind in links:
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
        endpoints.append((f"{ah}:{a}", f"{bh}:{b}", kind))
    return sorted(endpoints), omitted


def build(lab_name, image, nodes, config_dir, endpoints, header):
    """Return the topology YAML text.

    nodes: node names; each inherits kind, image and startup-config from
    topology.defaults, where Containerlab replaces __clabNodeName__ with the node
    name. config_dir: directory of the <node>.cfg files relative to the topology
    file ("" for the topology file's own directory).
    endpoints: from translate_links(). header: comment lines for the top.
    """
    q = json.dumps  # a JSON string is a valid YAML double-quoted scalar
    startup = f"{config_dir}/{NODE_NAME_VAR}.cfg" if config_dir else f"{NODE_NAME_VAR}.cfg"
    lines = [f"# {line}" for line in header]
    lines += [
        f"name: {q(lab_name)}",
        "topology:",
        "  defaults:  # every node inherits these; override per node, e.g. \"leaf1\": {image: \"...\"}",
        "    kind: ceos",
        f"    image: {q(image)}",
        f"    startup-config: {q(startup)}",
        "  nodes:",
    ]
    lines += [f"    {q(name)}: {{}}" for name in sorted(nodes)]
    lines.append("  links:" if endpoints else "  links: []")
    for a, b, kind in endpoints:
        if kind == "one-sided":
            lines.append("    # one-sided: LLDP reported this link from one end only")
        lines.append(f"    - endpoints: [{q(a)}, {q(b)}]")
    return "\n".join(lines) + "\n"
