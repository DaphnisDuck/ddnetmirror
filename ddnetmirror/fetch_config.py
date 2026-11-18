"""Fetch one device's running config from CloudVision and save it unchanged."""

import argparse
import asyncio
import contextlib
import os
import sys

from cloudvision.api.arista.inventory import v1 as inventory
from cloudvision.api.arista.tag import v2 as tag
from cloudvision.api.client import AsyncCVClient
from cloudvision.compliance.client import ComplianceClient

TOKEN_ENV = "CLOUDVISION_TOKEN"
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


def find_tagged_device_id(host, port, token, ca_file, label, value):
    """Return the device_id of the single device assigned tag label=value."""
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
    if len(device_ids) > 1:
        raise FetchError(
            f"{len(device_ids)} devices with tag {label}={value}: {', '.join(device_ids)}"
        )
    return device_ids[0]


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


def parse_tag(text):
    label, sep, value = text.partition("=")
    if not (sep and label and value):
        raise argparse.ArgumentTypeError(f"expected KEY=VALUE, got {text!r}")
    return label, value


def parse_server(server):
    host, sep, port = server.rpartition(":")
    if not sep:
        return server, 443
    if not port.isdigit():
        raise FetchError(f"invalid port in --server {server!r}")
    return host, int(port)


def run(args):
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

    if by_hostname:
        device_filter = inventory.Device(hostname=args.hostname)
        described = f"hostname {args.hostname!r}"
    else:
        label, value = args.tag
        device_id = find_tagged_device_id(host, port, token, args.ca_file, label, value)
        device_filter = inventory.Device(key=inventory.DeviceKey(device_id=device_id))
        described = f"device_id {device_id!r} (tag {label}={value})"
    device = find_device(host, port, token, args.ca_file, device_filter, described)
    serial = device.key.device_id
    name = args.hostname if by_hostname else (device.hostname or serial)
    if not output:
        separators = {"/", "\0", os.sep, os.altsep} - {None}
        if name in (".", "..") or any(c in name for c in separators):
            raise FetchError(
                f"inventory hostname {name!r} is not a safe file name; pass --output"
            )
        output = f"{name}.cfg"
        ensure_absent(output)
    version = device.software_version or "unknown"
    config = fetch_running_config(f"{host}:{port}", token, ca_bytes, serial)
    write_new_file(output, config)
    print(
        f"wrote running config for {name} ({serial}, EOS {version}) to {output}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch one device's running config from CloudVision "
        f"and save it unchanged. The service-account token is read from {TOKEN_ENV}."
    )
    parser.add_argument("--server", required=True, help="CloudVision host[:port] (default port 443)")
    selector = parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--hostname", help="device hostname as shown in CloudVision inventory")
    selector.add_argument("--tag", type=parse_tag, metavar="KEY=VALUE", help="device tag assigned to exactly one device")
    parser.add_argument("--ca-file", help="PEM CA bundle for on-prem CVP with a private CA")
    parser.add_argument("--output", help="output file (default: <device hostname>.cfg); never overwritten")
    args = parser.parse_args(argv)
    try:
        run(args)
    except (FetchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
