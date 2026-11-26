"""Fetch one device's running config from CloudVision and save it unchanged."""

import argparse
import asyncio
import importlib.metadata
import os
import re
import stat
import sys
from datetime import datetime, timezone

from cloudvision.api.arista.inventory import v1 as inventory
from cloudvision.api.arista.tag import v2 as tag
from cloudvision.api.client import AsyncCVClient
from cloudvision.compliance.client import ComplianceClient

from ddnetmirror import fixup, images, lldp, topology
from ddnetmirror.output import FetchError, ensure_absent, shown, write_new_file

TOKEN_ENV = "CLOUDVISION_TOKEN"
# under --output-dir: collected configs always, fixed-up startup configs with fixup rules
COLLECTED, STARTUP = "configs/collected", "configs/startup"
RPC_TIMEOUT = 30


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


def list_inventory(host, port, token, ca_file):
    """Return every inventory record."""
    request = inventory.DeviceStreamRequest()
    try:
        return asyncio.run(
            _get_all(host, port, token, ca_file, inventory.DeviceServiceStub, request)
        )
    except Exception as exc:
        raise FetchError(f"inventory lookup failed: {exc}") from exc


def find_device_by_hostname(host, port, token, ca_file, hostname):
    """Return the one eligible EOS inventory record (see eos_device_ids) whose
    hostname equals hostname ignoring case.

    Lists the whole inventory (CloudVision filters match exactly) and compares
    casefolded; the record keeps its CloudVision hostname. Several devices whose
    hostnames differ only in case are an error, never a guess.
    """
    devices = list_inventory(host, port, token, ca_file)
    wanted = hostname.casefold()
    matches = [d for d in devices if (d.hostname or "").casefold() == wanted]
    if not matches:
        raise FetchError(f"no device with hostname {hostname!r} (any case) in inventory")
    eligible = eos_device_ids(host, port, token, ca_file)
    found = [d for d in matches if d.key.device_id in eligible]
    if not found:
        listed = ", ".join(f"{shown(d.hostname)} ({shown(d.key.device_id)})" for d in matches)
        raise FetchError(f"hostname {hostname!r} is not an eligible EOS device (no "
                         f"system-generated eos tag in CloudVision): {listed}")
    if len(found) > 1:
        listed = ", ".join(f"{d.hostname} ({d.key.device_id})" for d in found)
        raise FetchError(f"{len(found)} devices with hostname {hostname!r} ignoring case: {listed}")
    return found[0]


def tagged_device_ids(host, port, token, ca_file, label, value):
    """Return the set of device_ids assigned tag label=value (exact, case-sensitive),
    or, with value None, assigned tag label with any value: an unset value is
    "any" to partial_eq_filter, as in eos_device_ids."""
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
    return {a.key.device_id for a in assignments}


EOS_TAG_LABEL = "eos"


def eos_device_ids(host, port, token, ca_file):
    """Return the set of device_ids eligible for collection: devices with a
    system-generated eos tag (any value) in mainline.

    One query for every value: the tag value is left unset, which CloudVision's
    partial_eq_filter treats as "any" (an empty string would match only an
    empty value). The creator type is filtered by the server, as Arista's cvlib
    does, and checked again here so an operator-created eos tag never counts.
    A failed query is an error, never an unrestricted selection."""
    key = tag.TagAssignmentKey(
        workspace_id="", element_type=tag.ElementType.DEVICE, label=EOS_TAG_LABEL
    )
    request = tag.TagAssignmentStreamRequest(partial_eq_filter=[
        tag.TagAssignment(key=key, tag_creator_type=tag.CreatorType.SYSTEM)
    ])
    try:
        assignments = asyncio.run(
            _get_all(host, port, token, ca_file, tag.TagAssignmentServiceStub, request)
        )
    except Exception as exc:
        raise FetchError(f"EOS eligibility lookup (system eos tag) failed: {exc}") from exc
    return {a.key.device_id for a in assignments
            if a.tag_creator_type == tag.CreatorType.SYSTEM and a.key.label == EOS_TAG_LABEL}


def find_tagged_device_ids(conn, selection, exclude=()):
    """Return the sorted distinct eligible EOS device_ids (see eos_device_ids)
    selected by a parsed --tag (at least one).

    Each KEY=VALUE or bare KEY term is one tag assignment query; "not" is taken
    against the eligible EOS inventory, so "not role=spine" includes EOS devices
    without a role tag and "not role" is exactly those. Tagged devices that are
    not eligible are left out with a note.
    exclude: --exclude-host hostnames, dropped from the selection (see exclude_hosts)."""
    text, tree = selection
    eligible = eos_device_ids(*conn)
    terms, universe = {}, []

    def ids(node):
        if node[0] == "tag":
            if node not in terms:
                terms[node] = tagged_device_ids(*conn, node[1], node[2])
            return terms[node]
        if node[0] == "not":
            if not universe:
                universe.append({d.key.device_id for d in list_inventory(*conn)} & eligible)
            return universe[0] - ids(node[1])
        left, right = ids(node[1]), ids(node[2])
        return left & right if node[0] == "and" else left | right

    selected = ids(tree)
    device_ids = sorted(selected & eligible)
    left_out = sorted(selected - eligible)
    if left_out:
        print(f"left out (not EOS: no system-generated eos tag): "
              f"{', '.join(shown(i) for i in left_out)}", file=sys.stderr)
    if not device_ids:
        what = "eligible EOS device" if left_out else "device"
        raise FetchError(f"no {what} with tag {text}")
    return exclude_hosts(conn, text, device_ids, exclude) if exclude else device_ids


def exclude_hosts(conn, text, device_ids, exclude):
    """Return device_ids without the devices whose inventory hostname equals an
    excluded hostname ignoring case (exact otherwise, as --hostname matches).

    An exclusion matching no selected device is a warning; one matching several
    (hostnames that differ only in case) is an error, never a guess. Excluding
    every selected device is an error."""
    selected = set(device_ids)
    hostnames = {d.key.device_id: d.hostname or "" for d in list_inventory(*conn)
                 if d.key.device_id in selected}
    dropped = set()
    for name in {h.casefold(): h for h in exclude}.values():  # each hostname once
        found = sorted(i for i, h in hostnames.items() if h.casefold() == name.casefold())
        if not found:
            print(f"warning: excluded host '{shown(name)}' matches no device selected by tag "
                  f"{text}", file=sys.stderr)
        elif len(found) > 1:
            listed = ", ".join(f"{shown(hostnames[i])} ({shown(i)})" for i in found)
            raise FetchError(f"excluded host '{shown(name)}' is ambiguous: {len(found)} selected "
                             f"devices with that hostname ignoring case: {listed}")
        else:
            dropped.add(found[0])
            print(f"excluded: {shown(hostnames[found[0]])} ({shown(found[0])})", file=sys.stderr)
    remaining = [i for i in device_ids if i not in dropped]
    if not remaining:
        raise FetchError(f"every device with tag {text} is excluded")
    return remaining


def find_tagged_device(host, port, token, ca_file, text, device_id):
    device_filter = inventory.Device(key=inventory.DeviceKey(device_id=device_id))
    described = f"device_id {device_id!r} (tag {text})"
    return find_device(host, port, token, ca_file, device_filter, described)


def fetch_running_config(server, token, ca_bytes, serial):
    with ComplianceClient(server, token, cert=ca_bytes) as client:
        result = client.get_config(serial)
    if result["errors"]:
        raise FetchError(f"get_config failed: {'; '.join(result['errors'])}")
    if not result["config"]:
        raise FetchError(f"empty running config returned for {serial}")
    return result["config"]


def check_safe_name(name, hint):
    separators = {"/", "\0", os.sep, os.altsep} - {None}
    if name in (".", "..") or any(c in name for c in separators):
        raise FetchError(f"inventory hostname {name!r} is not a safe file name{hint}")


def token_path():
    """$XDG_CONFIG_HOME/ddnetmirror/token, default ~/.config/ddnetmirror/token."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not os.path.isabs(base):  # unset, empty or relative: the XDG default
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ddnetmirror", "token")


def read_token(token_file=None):
    """Return the CloudVision token: from token_file if given (an unusable file is
    an error, never a fallback), else $CLOUDVISION_TOKEN if set, else the default
    token file.

    Only reads; never writes or copies the token anywhere. A token file must be a
    regular file (not a symlink) owned by the current user with no group/other
    permissions, in a directory that group/other cannot write. Error messages
    name paths and modes only, never file content.
    """
    if token_file is not None:
        path = os.path.expanduser(token_file)
        return read_token_file(path, f"token file {path} (--token-file) does not exist")
    token = os.environ.get(TOKEN_ENV, "").strip()
    if token:
        return checked_token(token, TOKEN_ENV)
    path = token_path()
    return read_token_file(path, f"no CloudVision token: set {TOKEN_ENV} or create {path} "
                                 "(mode 0600, directory 0700)")


def read_token_file(path, missing):
    """Read and check one token file; missing is the error message if it does not exist.
    A relative path is relative to the working directory."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        raise FetchError(missing) from None
    except OSError as exc:
        if os.path.islink(path):
            raise FetchError(f"token file {path} is a symbolic link; refusing it") from None
        raise FetchError(f"cannot read token file {path}: {exc.strerror}") from None
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise FetchError(f"token file {path} is not a regular file; refusing it")
        if info.st_uid != os.geteuid():
            raise FetchError(f"token file {path} is not owned by you; refusing it")
        if info.st_mode & 0o077:
            raise FetchError(f"token file {path} has mode {stat.S_IMODE(info.st_mode):04o}; "
                             f"group/other access is refused (chmod 600 {path})")
        directory = os.path.dirname(path) or "."  # as given: the kernel resolved "..", not abspath
        dir_mode = os.stat(directory).st_mode
        if dir_mode & 0o022:
            raise FetchError(f"directory {directory} has mode {stat.S_IMODE(dir_mode):04o}; "
                             f"group/other write access is refused (chmod 700 {directory})")
        data = f.read()
    try:
        token = data.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise FetchError(f"token file {path} is not UTF-8 text") from None
    if not token:
        raise FetchError(f"token file {path} is empty")
    return checked_token(token, f"token file {path}")


def checked_token(token, source):
    """Refuse inner whitespace, control or non-ASCII characters: gRPC metadata
    validation would otherwise echo the token in its error message."""
    if not all("!" <= c <= "~" for c in token):
        raise FetchError(f"{source}: the token contains whitespace, control or non-ASCII "
                         "characters (it must be a single line of printable ASCII)")
    return token


TAG_OPERATORS = ("and", "or", "not")


def parse_term(text, bare=False):
    """KEY=VALUE, or with bare=True also a bare KEY (any value: ("tag", KEY, None))."""
    label, sep, value = text.partition("=")
    if bare and not sep:
        return ("tag", label, None)
    if not (sep and label and value):
        expected = "KEY or KEY=VALUE" if bare else "KEY=VALUE"
        raise argparse.ArgumentTypeError(f"expected {expected}, got {text!r}")
    return ("tag", label, value)


def parse_tag(text):
    """Parse --tag into (text, tree); tree nodes are ("tag", KEY, VALUE),
    ("tag", KEY, None) for a bare KEY (the tag with any value), ("not", x),
    ("and", x, y) and ("or", x, y).

    With "=" and without an and/or/not word in any case (separated by whitespace or
    parentheses), text is one plain KEY=VALUE exactly as before (the value may
    contain spaces or parentheses). Otherwise it is an expression: not > and > or,
    with parentheses; terms are KEY=VALUE or a bare KEY without whitespace or
    parentheses, separated by whitespace or parentheses. A standalone and/or/not
    word is always an operator, never a bare KEY.
    """
    tokens = [token.lower() if token.lower() in TAG_OPERATORS else token  # AND, Or, nOt
              for token in re.findall(r"[()]|[^\s()]+", text)]  # parentheses separate words too
    if "=" in text and not any(token in TAG_OPERATORS for token in tokens):
        return text, parse_term(text)
    pos = 0

    def error(reason):
        return argparse.ArgumentTypeError(f"bad tag expression {text!r}: {reason}")

    if not tokens:
        raise error("it is empty")
    if len(tokens) == 1 and tokens[0] in TAG_OPERATORS:
        raise error(f"{tokens[0]!r} is an operator, not a tag key "
                    "(and, or, not are reserved words)")

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take():
        nonlocal pos
        pos += 1
        return tokens[pos - 1]

    def either():  # x or y or ...
        node = both()
        while peek() == "or":
            take()
            node = ("or", node, both())
        return node

    def both():  # x and y and ...
        node = single()
        while peek() == "and":
            take()
            node = ("and", node, single())
        return node

    def single():  # not x, (x), KEY=VALUE, KEY
        if peek() is None:
            raise error("it ends where a KEY, KEY=VALUE, 'not' or '(' is expected")
        token = take()
        if token == "not":
            return ("not", single())
        if token == "(":
            node = either()
            if peek() != ")":
                raise error("missing ')'")
            take()
            return node
        if token in ("and", "or", ")"):
            raise error(f"unexpected {token!r}")
        try:
            return parse_term(token, bare=True)
        except argparse.ArgumentTypeError as exc:
            raise error(exc) from None

    tree = either()
    if peek() is not None:
        raise error(f"unexpected {peek()!r}")
    return " ".join(text.split()), tree


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


def run(args, base_dir=None):
    """Return True if every selected device was collected.

    base_dir: directory that a relative --fixup-rules path is relative to
    (ddn up passes the project directory); None means the working directory."""
    rules = None
    if args.lab_name:  # fixups exist only for a generated lab
        path, source = resolve_fixup_rules(args.fixup_rules, base_dir)
        print(f"fixup rules: {'none' if path is None else path} ({source})")
        if path is not None:
            rules = load_fixup_rules(path)
    image = args.image
    if args.lab_name and image is None:  # pick images by EOS version: list them before collecting
        try:
            image = images.Selector()
        except images.ImageError as exc:
            raise FetchError(str(exc)) from None
    token = read_token(args.token_file)
    by_hostname = args.tag is None
    output = args.output
    if output:
        ensure_absent(output)
    host, port = parse_server(args.server)
    ca_bytes = None
    if args.ca_file:
        with open(args.ca_file, "rb") as f:
            ca_bytes = f.read()
    conn = (host, port, token, args.ca_file)
    server = f"{host}:{port}"

    if args.list:
        return list_selection(conn, args.hostname, args.tag, args.exclude_host)

    if args.output_dir:
        lab = None
        if args.lab_name:
            lab = (args.lab_name, image)
            ensure_absent(topology_path(args.output_dir, args.lab_name), args.force)
        return collect_tag(
            conn, server, ca_bytes, args.tag, args.output_dir, args.lldp, lab, rules, args.verbose,
            args.force, args.exclude_host,
        )

    if by_hostname:
        device = find_device_by_hostname(*conn, args.hostname)
    else:
        text = args.tag[0]
        device_ids = find_tagged_device_ids(conn, args.tag, args.exclude_host)
        if len(device_ids) > 1:
            raise FetchError(f"{len(device_ids)} devices with tag {text}: {', '.join(device_ids)}")
        device = find_tagged_device(*conn, text, device_ids[0])
    serial = device.key.device_id
    name = device.hostname or serial
    if not output:
        check_safe_name(name, "; pass --output")
        output = f"{name}.cfg"
        ensure_absent(output)
    fetch_and_write(server, token, ca_bytes, device, name, output)
    return True


def list_selection(conn, hostname, selected_tag, exclude=()):
    """Print the devices a selection picks, as collection selects them; fetch and write nothing.

    Rows (hostname, serial, EOS version; sorted, aligned) go to stdout, the summary
    to stderr. Returns True if every selected device was found in inventory."""
    if selected_tag is None:
        devices = [find_device_by_hostname(*conn, hostname)]
        count, described = 1, f"hostname {hostname}"
    else:
        text = selected_tag[0]
        device_ids = find_tagged_device_ids(conn, selected_tag, exclude)
        count, described = len(device_ids), f"tag {text}"
        devices = []
        for device_id in device_ids:
            try:
                devices.append(find_tagged_device(*conn, text, device_id))
            except FetchError as exc:  # as in collection: report it, list the rest
                print(f"failed: {shown(device_id)}: {shown(exc)}", file=sys.stderr)
    print(f"selected {count} device{'s' * (count != 1)} with {described}", file=sys.stderr)
    rows = sorted(
        (shown(d.hostname or "-"), shown(d.key.device_id), shown(d.software_version or "unknown"))
        for d in devices
    )
    widths = [max((len(row[i]) for row in rows), default=0) for i in range(2)]
    for name, serial, version in rows:
        print(f"{name:<{widths[0]}}  {serial:<{widths[1]}}  {version}")
    return len(devices) == count  # False if a selected device is missing from inventory


NO_FIXUPS = "none"  # --fixup-rules none: no fixups, not even the user default


def user_fixups_path():
    """$XDG_CONFIG_HOME/ddnetmirror/fixups.yml, default ~/.config/ddnetmirror/fixups.yml."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not os.path.isabs(base):  # unset, empty or relative: the XDG default
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "ddnetmirror", "fixups.yml")


def resolve_fixup_rules(value, base_dir=None):
    """Return (rule file path or None, where the choice came from).

    value: the explicit setting (--fixup-rules), or None if not given. An
    explicit path wins (relative to base_dir if given, e.g. a project
    directory); "none" disables fixups; otherwise the user default file is used
    if it exists, else no fixups. Only chooses; the file is validated by
    load_fixup_rules. Never creates or changes any file.
    """
    if value == NO_FIXUPS:
        return None, "disabled with --fixup-rules none"
    if value is not None:
        return (os.path.join(base_dir, value) if base_dir else value), "--fixup-rules"
    path = user_fixups_path()
    try:
        os.lstat(path)
    except FileNotFoundError:  # only a missing file means "no user default"
        return None, f"no --fixup-rules and no {path}"
    except OSError as exc:  # e.g. an unreadable directory: never silently skip the rules
        raise FetchError(f"cannot check user default fixup rules {path}: {exc.strerror}") from None
    if not os.path.isfile(path):
        raise FetchError(f"{path} (user default fixup rules) is not a regular file; "
                         "remove it or pass --fixup-rules")
    return path, "user default"


def load_fixup_rules(path):
    """Read and validate a fixup rule file before anything is fetched or written."""
    try:
        with open(path, "rb") as f:
            return fixup.load_rules(f.read().decode("utf-8"))
    except (fixup.RuleError, UnicodeDecodeError) as exc:
        raise FetchError(f"{path}: {exc}") from None
    except OSError as exc:
        raise FetchError(f"cannot read fixup rules {path}: {exc.strerror}") from None


def write_startup(rules, name, config, output, verbose=False, force=False):
    """Apply the fixup rules to a copy of config and write it as a new file.
    With verbose, print what each rule changed (never config text)."""
    report = (lambda line: print(f"fixup {shown(name)}: {line}")) if verbose else (lambda _: None)
    write_new_file(output, fixup.apply_rules(rules, config, report=report, verbose=verbose), force)
    print(f"wrote startup config for {shown(name)} with fixups to {shown(output)}")


def fetch_and_write(server, token, ca_bytes, device, name, output, force=False):
    serial = device.key.device_id
    version = device.software_version or "unknown"
    config = fetch_running_config(server, token, ca_bytes, serial)
    write_new_file(output, config, force)
    print(f"wrote running config for {shown(name)} ({shown(serial)}, EOS {shown(version)}) "
          f"to {shown(output)}")
    return config


def topology_path(output_dir, lab_name):
    return os.path.join(output_dir, f"{lab_name}.clab.yml")


def collect_tag(
    conn, server, ca_bytes, selected_tag, output_dir, report_lldp=False, lab=None, rules=None,
    verbose=False, force=False, exclude=(),
):
    """Collect every device selected by the parsed --tag into output_dir, sequentially.

    Successes are kept; each failed device is reported and the rest continue.
    Returns True only if every device was collected (and, with lab, the topology
    was written). With report_lldp, also prints LLDP links between the found
    devices (problems there are non-fatal). lab=(name, image) also writes a
    Containerlab topology for the saved configs (requires report_lldp); image is
    an image name (--image) or an images.Selector (the default).

    Originals always go to output_dir/configs/collected. With fixup rules, a
    transformed copy of each goes to output_dir/configs/startup; the topology
    then lists only devices with a startup config. A device whose fixup fails keeps its original and counts as
    failed. verbose prints per-device, per-rule fixup counts.

    force replaces existing regular files at the generated paths (one at a time,
    via a temporary file). A file whose write fails keeps its previous version,
    and a failed device never counts as this run's output. Files of that device
    replaced before the failure (e.g. configs/collected/<name>.cfg when its fixup
    fails)
    are not restored: there is no rollback. Other files are never looked at.

    exclude: --exclude-host hostnames, dropped before anything is collected.
    """
    text = selected_tag[0]
    token = conn[2]
    device_ids = find_tagged_device_ids(conn, selected_tag, exclude)
    print(f"selected {len(device_ids)} device{'s' * (len(device_ids) != 1)} with tag {text}")
    collected_dir = os.path.join(output_dir, COLLECTED)
    os.makedirs(collected_dir, exist_ok=True)
    if rules:
        os.makedirs(os.path.join(output_dir, STARTUP), exist_ok=True)
    failures, fixup_failures = [], []
    startup = None if rules is None else set()  # names with a startup config

    def fail(name, reason, into=failures):
        into.append(name)
        print(f"failed: {shown(name)}: {shown(reason)}", file=sys.stderr)

    found = []
    saved = {}  # name -> device, for configs written in this run
    for device_id in device_ids:
        try:
            device = find_tagged_device(*conn, text, device_id)
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
            ensure_absent(output, force)
            config = fetch_and_write(server, token, ca_bytes, device, name, output, force)
            saved[name] = device
            written += 1
        except (FetchError, OSError) as exc:
            fail(label_for_report, exc)
            continue
        if rules:
            try:
                write_startup(rules, name, config, os.path.join(output_dir, STARTUP, f"{name}.cfg"),
                              verbose, force)
                startup.add(name)
            except Exception as exc:  # the original stays; only this device is affected
                fail(label_for_report, f"fixup failed, original kept: {exc}", fixup_failures)

    print(
        f"collected {written} of {len(device_ids)} devices with tag {text} "
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
                print(f"skipped LLDP: {shown(device.key.device_id)}: no inventory hostname",
                      file=sys.stderr)
        try:
            links, lldp_available = lldp.report_links(server, token, conn[3], devices)
        except Exception as exc:
            print(f"LLDP report unavailable: {shown(exc)}", file=sys.stderr)
    if lab:
        written_topology = write_topology(
            output_dir, lab, len(device_ids), saved, links, lldp_available, startup, force
        )
        return written_topology and not failures and not fixup_failures
    return not failures and not fixup_failures


def package_version():
    try:
        return importlib.metadata.version("ddnetmirror")
    except importlib.metadata.PackageNotFoundError:
        return "(version unknown)"


def write_topology(output_dir, lab, total, saved, links, lldp_available, startup=None, force=False):
    """Write <lab>.clab.yml for the saved configs; return True if written.

    startup: names with a fixed-up startup config (None without fixups); only
    those become nodes, referencing configs/startup/<name>.cfg; otherwise nodes
    reference configs/collected/<name>.cfg.
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
            + ", ".join(shown(n) for n in bad)
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
    overrides = {}
    if isinstance(image, images.Selector):
        image, overrides = choose_images(image, saved)
        header.append(f"Images: picked per device by EOS version from local {images.REPOSITORY} "
                      f"images; default is the most used, {image}. Not a compatibility check.")
    config_dir = COLLECTED if startup is None else STARTUP  # relative to the topology file
    text = topology.build(lab_name, image, saved, config_dir, endpoints, header, overrides)
    for reason in omitted:
        print(f"omitted link: {reason}", file=sys.stderr)
    path = topology_path(output_dir, lab_name)
    write_new_file(path, text, force)
    print(f"wrote Containerlab topology {path} ({len(saved)} nodes, {len(endpoints)} links)")
    if not complete:
        print(f"warning: {path} is INCOMPLETE: only {len(saved)} of {total} devices",
              file=sys.stderr)
    return True


def choose_images(selector, saved):
    """Pick and explain an image per node; return (default image, {node: other image}).

    The default is the image most nodes got (ties: the first in name order)."""
    if selector.ignored:
        print("note: ignoring local images without a version tag: "
              + ", ".join(selector.image(t) for t in selector.ignored))
    chosen = {}
    for name in sorted(saved):
        version = saved[name].software_version
        chosen[name], reason, cross_train = selector.choose(version)
        print(f"image for {name} (EOS {shown(version or 'unknown')}): {chosen[name]} ({reason})")
        if cross_train:
            print(f"warning: {name} gets {chosen[name]} from another release train than its "
                  f"EOS {shown(version)}; version matching does not establish configuration "
                  "compatibility", file=sys.stderr)
    picks = list(chosen.values())
    default = max(picks, key=picks.count)  # first maximum: deterministic
    return default, {n: i for n, i in chosen.items() if i != default}


def add_token_file_argument(parser):
    """--token-file, shared by ddn fetch and ddn up."""
    parser.add_argument(
        "--token-file",
        metavar="PATH",
        help=f"read the CloudVision token from this file instead of {TOKEN_ENV} or the "
        "default token file (e.g. one file per environment). Same checks as the default "
        "file: owner-only regular file, not a symlink; an unusable file is an error",
    )


def build_parser(prog=None):
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Fetch running configs from CloudVision and save them unchanged. "
        "Only EOS devices are selected: those with CloudVision's system-generated eos tag. "
        f"The service-account token is read from --token-file, else from {TOKEN_ENV}, "
        "else from $XDG_CONFIG_HOME/ddnetmirror/token (default ~/.config/ddnetmirror/token)."
    )
    parser.add_argument("--server", required=True, help="CloudVision host[:port] (default port 443)")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument(
        "--hostname", help="device hostname as shown in CloudVision inventory (any case)"
    )
    selector.add_argument(
        "--tag",
        type=parse_tag,
        metavar="EXPR",
        help="device tag KEY=VALUE (exact, case-sensitive), a bare KEY (the tag with any "
        "value), or an expression of such terms with not, and, or (in that precedence) and "
        "parentheses, e.g. '(site=lab or site=dr) and not role=spine' or 'site=lab and not "
        "role'; 'not' covers EOS devices without the tag. and, or, not are reserved words. "
        "Must match exactly one device unless --output-dir or --list is given",
    )
    parser.add_argument(
        "--exclude-host",
        action="append",
        default=[],
        metavar="HOSTNAME",
        help="with --tag: leave out the selected device with this inventory hostname "
        "(exact, any case) before anything is collected; repeat for more devices. "
        "A hostname matching no selected device is a warning",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the devices --hostname or --tag selects (hostname, serial, EOS version) "
        "and stop: no configs are fetched and no files are written. --tag may match many devices",
    )
    parser.add_argument("--ca-file", help="PEM CA bundle for on-prem CVP with a private CA")
    add_token_file_argument(parser)
    outputs = parser.add_mutually_exclusive_group()
    outputs.add_argument("--output", help="output file (default: <device hostname>.cfg); never overwritten")
    outputs.add_argument(
        "--output-dir",
        help="with --tag: collect every device with the tag into DIR/configs/collected "
        "as <hostname>.cfg",
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
    parser.add_argument(
        "--image",
        help="with --lab-name: cEOS image for every topology node, no Docker check. Without it, "
        "a local ceos:VERSION image is picked per device by EOS version (needs Docker; "
        "version matching only, not a compatibility check)",
    )
    parser.add_argument("--fallback-image", help=argparse.SUPPRESS)  # removed; refused in main
    parser.add_argument(
        "--fixup-rules",
        metavar="RULES.yml",
        help="with --lab-name: write copies of the collected configs transformed by these "
        "rules to DIR/configs/startup, and use those as startup configs. Default: "
        "$XDG_CONFIG_HOME/ddnetmirror/fixups.yml (~/.config/ddnetmirror/fixups.yml) if it "
        "exists; 'none' disables fixups",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="with --lab-name and fixup rules: print what each rule changed per device; "
        "never prints config text",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --output-dir: replace existing generated files (collected and startup "
        "configs, topology) instead of refusing; no backups, other files untouched",
    )
    return parser


LIST_EXCLUDES = {  # collection-only options: option -> argparse dest
    "--output": "output", "--output-dir": "output_dir", "--lldp": "lldp",
    "--lab-name": "lab_name", "--image": "image", "--fixup-rules": "fixup_rules",
    "--verbose": "verbose", "--force": "force",
}


def main(argv=None, prog=None, base_dir=None):
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    if args.list:
        for option, value in LIST_EXCLUDES.items():
            if getattr(args, value) not in (None, False):
                parser.error(f"--list cannot be combined with {option}")
    if args.exclude_host and args.tag is None:
        parser.error("--exclude-host requires --tag (not --hostname)")
    if not all(h.strip() for h in args.exclude_host):
        parser.error("--exclude-host must not be empty")
    if args.output_dir and args.tag is None:
        parser.error("--output-dir requires --tag")
    if args.lldp and not args.output_dir:
        parser.error("--lldp requires --tag and --output-dir")
    if args.fallback_image is not None:
        parser.error("--fallback-image was removed: without --image, a local ceos image is "
                     "picked per device (the newest one when no version matches); "
                     "pass --image IMAGE to use one image for every node")
    if args.image is not None and not args.image.strip():
        parser.error("--image must not be empty (omit it to pick images by EOS version)")
    if args.image is not None and not args.lab_name:
        parser.error("--image requires --lab-name")
    if args.image == "auto":
        print("note: --image auto is no longer needed; picking images by EOS version is the "
              "default when --image is omitted", file=sys.stderr)
        args.image = None
    if args.lab_name and not args.lldp:
        parser.error("--lab-name requires --tag, --output-dir and --lldp")
    if args.fixup_rules is not None and not args.lab_name:
        parser.error("--fixup-rules requires --lab-name, --tag, --output-dir and --lldp")
    if args.verbose and (not args.lab_name or args.fixup_rules == NO_FIXUPS):
        parser.error("--verbose requires fixup rules (--lab-name with --fixup-rules or "
                     "the user default rules file)")
    if args.token_file is not None and not args.token_file.strip():
        parser.error("--token-file must not be empty")
    if args.force and not args.output_dir:
        parser.error("--force requires --tag and --output-dir")
    try:
        complete = run(args, base_dir)
    except (FetchError, OSError) as exc:
        print(f"error: {shown(exc)}", file=sys.stderr)
        return 1
    return 0 if complete else 1


if __name__ == "__main__":
    sys.exit(main())
