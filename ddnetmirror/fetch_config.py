"""Fetch one device's running config from CloudVision and save it unchanged."""

import argparse
import asyncio
import contextlib
import importlib.metadata
import os
import sys
from datetime import datetime, timezone

from cloudvision.api.arista.inventory import v1 as inventory
from cloudvision.api.arista.tag import v2 as tag
from cloudvision.api.client import AsyncCVClient
from cloudvision.compliance.client import ComplianceClient

from ddnetmirror import lldp, topology

TOKEN_ENV = "CLOUDVISION_TOKEN"
COLLECTED, STARTUP = "collected", "startup"  # subdirectories used with fixup rules
RPC_TIMEOUT = 30


class FetchError(Exception):
    """A failure reported to the user as a one-line message."""


async def _get_all(host, port, token, ca_file, stub_class, request):
    client = AsyncCVClient.from_token(token, host, port=port, cacert=ca_file)
    with client as channel:
        stub = stub_class(channel)
        return [
            resp.value async for resp in stub.get_all(request, timeout=RPC_TIMEOUT)
        ]


def find_device(host, port, token, ca_file, device_filter, described):
    """Return the single inventory record matching device_filter."""
    request = inventory.DeviceStreamRequest(partial_eq_filter=[device_filter])
    try:
        devices = asyncio.run(
            _get_all(host, port, token, ca_file, inventory.DeviceServiceStub, request)
        )
    except Exception as exc:
        raise FetchError(f"inventory lookup failed: {exc}") from exc
    if not devices:
        raise FetchError(f"no device with {described} in inventory")
    if len(devices) > 1:
        serials = ", ".join(d.key.device_id for d in devices)
        raise FetchError(f"{len(devices)} devices with {described}: {serials}")
    return devices[0]


def find_tagged_device_ids(host, port, token, ca_file, label, value):
    """Return the sorted distinct device_ids assigned tag label=value (at least one)."""
    key = tag.TagAssignmentKey(
        workspace_id="",  # merged (mainline) assignments
        element_type=tag.ElementType.DEVICE,
        label=label,
        value=value,
    )
    request = tag.TagAssignmentStreamRequest(partial_eq_filter=[tag.TagAssignment(key=key)])
    try:
        assignments = asyncio.run(
            _get_all(host, port, token, ca_file, tag.TagAssignmentServiceStub, request)
        )
    except Exception as exc:
        raise FetchError(f"tag lookup failed: {exc}") from exc
    device_ids = sorted({a.key.device_id for a in assignments})
    if not device_ids:
        raise FetchError(f"no device with tag {label}={value}")
    return device_ids


def find_tagged_device(host, port, token, ca_file, label, value, device_id):
    device_filter = inventory.Device(key=inventory.DeviceKey(device_id=device_id))
    described = f"device_id {device_id!r} (tag {label}={value})"
    return find_device(host, port, token, ca_file, device_filter, described)


def fetch_running_config(server, token, ca_bytes, serial):
    with ComplianceClient(server, token, cert=ca_bytes) as client:
        result = client.get_config(serial)
    if result["errors"]:
        raise FetchError(f"get_config failed: {'; '.join(result['errors'])}")
    if not result["config"]:
        raise FetchError(f"empty running config returned for {serial}")
    return result["config"]


def write_new_file(path, text):
    """Write text as UTF-8 without any changes to a new owner-only file.

    Refuses to overwrite. If writing or closing fails, removes the file it created.
    """
    data = text.encode("utf-8")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise FetchError(f"{path} already exists; not overwriting") from None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise


def ensure_absent(path):
    if os.path.exists(path):
        raise FetchError(f"{path} already exists; not overwriting")


def check_safe_name(name, hint):
    separators = {"/", "\0", os.sep, os.altsep} - {None}
    if name in (".", "..") or any(c in name for c in separators):
        raise FetchError(f"inventory hostname {name!r} is not a safe file name{hint}")


def parse_tag(text):
    label, sep, value = text.partition("=")
    if not (sep and label and value):
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    return label, value


def parse_lab_name(text):
    if not topology.valid_name(text):
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a valid Containerlab lab name "
            "(letters, digits, '.', '_', '-'; must start with a letter or digit)"
        )
    return text


def parse_server(server):
    host, sep, port = server.rpartition(":")
    if not sep:
        return server, 443
    if not port.isdigit():
        raise FetchError(f"invalid port in --server {server!r}")
    return host, int(port)


def run(args):
    """Return True if every selected device was collected."""
    rules = load_fixup_rules(args.fixup_rules) if args.fixup_rules is not None else None
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        raise FetchError(f"{TOKEN_ENV} is not set")
    by_hostname = args.tag is None
    output = args.output or (f"{args.hostname}.cfg" if by_hostname else None)
    if output:
        ensure_absent(output)
    host, port = parse_server(args.server)
    ca_bytes = None
    if args.ca_file:
        with open(args.ca_file, "rb") as f:
            ca_bytes = f.read()
    conn = (host, port, token, args.ca_file)
    server = f"{host}:{port}"

    if args.output_dir:
        lab = None
        if args.lab_name:
            lab = (args.lab_name, args.image)
            ensure_absent(topology_path(args.output_dir, args.lab_name))
        return collect_tag(
            conn, server, ca_bytes, args.tag, args.output_dir, args.lldp, lab, rules, args.verbose
        )

    if by_hostname:
        device_filter = inventory.Device(hostname=args.hostname)
        device = find_device(*conn, device_filter, f"hostname {args.hostname!r}")
    else:
        label, value = args.tag
        device_ids = find_tagged_device_ids(*conn, label, value)
        if len(device_ids) > 1:
            raise FetchError(
                f"{len(device_ids)} devices with tag {label}={value}: {', '.join(device_ids)}"
            )
        device = find_tagged_device(*conn, label, value, device_ids[0])
    serial = device.key.device_id
    name = args.hostname if by_hostname else (device.hostname or serial)
    if not output:
        check_safe_name(name, "; pass --output")
        output = f"{name}.cfg"
        ensure_absent(output)
    fetch_and_write(server, token, ca_bytes, device, name, output)
    return True


def load_fixup_rules(path):
    """Read and validate a fixup rule file before anything is fetched or written."""
    from ddnetmirror import fixup  # imported here: fixup imports this module

    try:
        with open(path, "rb") as f:
            return fixup.load_rules(f.read().decode("utf-8"))
    except (fixup.RuleError, UnicodeDecodeError) as exc:
        raise FetchError(f"{path}: {exc}") from None


def write_startup(rules, name, config, output, verbose=False):
    """Apply the fixup rules to a copy of config and write it as a new file.
    With verbose, print what each rule changed (never config text)."""
    from ddnetmirror import fixup

    report = (lambda line: print(f"fixup {name}: {line}")) if verbose else (lambda _: None)
    write_new_file(output, fixup.apply_rules(rules, config, report=report, verbose=verbose))
    print(f"wrote startup config for {name} with fixups to {output}")


def fetch_and_write(server, token, ca_bytes, device, name, output):
    serial = device.key.device_id
    version = device.software_version or "unknown"
    config = fetch_running_config(server, token, ca_bytes, serial)
    write_new_file(output, config)
    print(f"wrote running config for {name} ({serial}, EOS {version}) to {output}")
    return config


def topology_path(output_dir, lab_name):
    return os.path.join(output_dir, f"{lab_name}.clab.yml")


def collect_tag(
    conn, server, ca_bytes, selected_tag, output_dir, report_lldp=False, lab=None, rules=None,
    verbose=False,
):
    """Collect every device with the tag into output_dir, sequentially.

    Successes are kept; each failed device is reported and the rest continue.
    Returns True only if every device was collected (and, with lab, the topology
    was written). With report_lldp, also prints LLDP links between the found
    devices (problems there are non-fatal). lab=(name, image) also writes a
    Containerlab topology for the saved configs (requires report_lldp).

    With fixup rules, originals go to output_dir/collected and a transformed copy
    of each to output_dir/startup; the topology then lists only devices with a
    startup config. A device whose fixup fails keeps its original and counts as
    failed. verbose prints per-device, per-rule fixup counts.
    """
    label, value = selected_tag
    token = conn[2]
    device_ids = find_tagged_device_ids(*conn, label, value)
    collected_dir = os.path.join(output_dir, COLLECTED) if rules else output_dir
    os.makedirs(collected_dir, exist_ok=True)
    if rules:
        os.makedirs(os.path.join(output_dir, STARTUP), exist_ok=True)
    failures, fixup_failures = [], []
    startup = None if rules is None else set()  # names with a startup config

    def fail(name, reason, into=failures):
        into.append(name)
        print(f"failed: {name}: {reason}", file=sys.stderr)

    found = []
    saved = {}  # name -> device, for configs written in this run
    for device_id in device_ids:
        try:
            device = find_tagged_device(*conn, label, value, device_id)
        except FetchError as exc:
            fail(device_id, exc)
            continue
        found.append((device.hostname or device.key.device_id, device))

    names = [name for name, _ in found]
    written = 0
    for name, device in found:
        label_for_report = f"{name} ({device.key.device_id})"
        try:
            check_safe_name(name, "")
            if names.count(name) > 1:
                raise FetchError(f"hostname {name!r} is shared by several tagged devices")
            output = os.path.join(collected_dir, f"{name}.cfg")
            ensure_absent(output)
            config = fetch_and_write(server, token, ca_bytes, device, name, output)
            saved[name] = device
            written += 1
        except (FetchError, OSError) as exc:
            fail(label_for_report, exc)
            continue
        if rules:
            try:
                write_startup(rules, name, config, os.path.join(output_dir, STARTUP, f"{name}.cfg"),
                              verbose)
                startup.add(name)
            except Exception as exc:  # the original stays; only this device is affected
                fail(label_for_report, f"fixup failed, original kept: {exc}", fixup_failures)

    print(
        f"collected {written} of {len(device_ids)} devices with tag {label}={value} "
        f"into {collected_dir}; {len(failures)} failed"
    )
    if rules:
        print(f"wrote {len(startup)} of {written} startup configs with fixups into "
              f"{os.path.join(output_dir, STARTUP)}; {len(fixup_failures)} failed")
    links, lldp_available = None, False
    if report_lldp:
        devices = []
        for _, device in found:
            if device.hostname:
                devices.append((device.hostname, device.key.device_id))
            else:
                print(f"skipped LLDP: {device.key.device_id}: no inventory hostname", file=sys.stderr)
        try:
            links, lldp_available = lldp.report_links(server, token, conn[3], devices)
        except Exception as exc:
            print(f"LLDP report unavailable: {exc}", file=sys.stderr)
    if lab:
        written_topology = write_topology(
            output_dir, lab, len(device_ids), saved, links, lldp_available, startup
        )
        return written_topology and not failures and not fixup_failures
    return not failures and not fixup_failures


def package_version():
    try:
        return importlib.metadata.version("ddnetmirror")
    except importlib.metadata.PackageNotFoundError:
        return "(version unknown)"


def write_topology(output_dir, lab, total, saved, links, lldp_available, startup=None):
    """Write <lab>.clab.yml for the saved configs; return True if written.

    startup: names with a fixed-up startup config (None without fixups); only
    those become nodes, referencing startup/<name>.cfg.
    """
    lab_name, image = lab
    if startup is not None:
        saved = {n: d for n, d in saved.items() if n in startup}

    def refuse(reason):
        print(f"error: topology not written: {reason}", file=sys.stderr)
        return False

    if links is None or not lldp_available:
        return refuse("LLDP data unavailable for every device")
    if not saved:
        return refuse("no device config was saved" if startup is None
                      else "no startup config was written")
    bad = sorted(
        n for n, d in saved.items() if not d.hostname or not topology.valid_name(n)
    )
    if bad:
        return refuse(
            "not valid Containerlab node names (inventory hostnames are used as-is): "
            + ", ".join(lldp.shown(n) for n in bad)
        )
    reserved = sorted(n for n in saved if topology.reserved_node_name(n))
    if reserved:
        return refuse(
            "hostnames reserved by Containerlab link endpoints "
            f"({', '.join(sorted(topology.RESERVED_NODE_NAMES))}, any case): "
            + ", ".join(reserved)
        )
    complete = len(saved) == total
    endpoints, omitted = topology.translate_links(saved, links)
    what = "devices collected" if startup is None else "devices with a fixed-up startup config"
    header = [
        f"Generated by ddnetmirror {package_version()} at "
        f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} (generation time, UTC).",
        f"{len(saved)} of {total} {what}; {len(endpoints)} links.",
    ]
    if not complete:
        missing = "a saved config" if startup is None else "a startup config"
        header.append(f"INCOMPLETE: devices without {missing}, and their links, are missing.")
    config_dir = "" if startup is None else STARTUP  # relative to the topology file
    text = topology.build(lab_name, image, saved, config_dir, endpoints, header)
    for reason in omitted:
        print(f"omitted link: {reason}", file=sys.stderr)
    path = topology_path(output_dir, lab_name)
    write_new_file(path, text)
    print(f"wrote Containerlab topology {path} ({len(saved)} nodes, {len(endpoints)} links)")
    if not complete:
        print(f"warning: {path} is INCOMPLETE: only {len(saved)} of {total} devices",
              file=sys.stderr)
    return True


def build_parser(prog=None):
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Fetch running configs from CloudVision and save them unchanged. "
        f"The service-account token is read from {TOKEN_ENV}."
    )
    parser.add_argument("--server", required=True, help="CloudVision host[:port] (default port 443)")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--hostname", help="device hostname as shown in CloudVision inventory")
    selector.add_argument("--tag", type=parse_tag, metavar="KEY=VALUE", help="device tag; must match exactly one device unless --output-dir is given")
    parser.add_argument("--ca-file", help="PEM CA bundle for on-prem CVP with a private CA")
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--output", help="output file (default: <device hostname>.cfg); never overwritten")
    outputs.add_argument(
        "--output-dir",
        help="with --tag: collect every device with the tag into DIR as <hostname>.cfg",
    )
    parser.add_argument(
        "--lldp",
        action="store_true",
        help="with --output-dir: also report LLDP links between the collected devices",
    )
    parser.add_argument(
        "--lab-name",
        type=parse_lab_name,
        help="with --lldp: also write a Containerlab topology DIR/<NAME>.clab.yml",
    )
    parser.add_argument("--image", help="cEOS image for every topology node (with --lab-name)")
    parser.add_argument(
        "--fixup-rules",
        metavar="RULES.yml",
        help="with --lab-name: keep originals in DIR/collected, write copies transformed "
        "by these rules to DIR/startup, and use those as startup configs",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="with --fixup-rules: print what each rule changed per device; "
        "never prints config text",
    )
    return parser


def main(argv=None, prog=None):
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    if args.output_dir and args.tag is None:
        parser.error("--output-dir requires --tag")
    if args.lldp and not args.output_dir:
        parser.error("--lldp requires --tag and --output-dir")
    if bool(args.lab_name) != bool(args.image):
        parser.error("--lab-name and --image must be given together")
    if args.lab_name and not args.lldp:
        parser.error("--lab-name requires --tag, --output-dir and --lldp")
    if args.fixup_rules is not None and not args.lab_name:
        parser.error("--fixup-rules requires --lab-name, --image, --tag, --output-dir and --lldp")
    if args.verbose and args.fixup_rules is None:
        parser.error("--verbose requires --fixup-rules")
    try:
        complete = run(args)
    except (FetchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
