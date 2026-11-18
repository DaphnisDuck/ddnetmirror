"""Fetch one device's running config from CloudVision and save it unchanged."""

import argparse
import asyncio
import contextlib
import os
import sys

from cloudvision.api.arista.inventory import v1 as inventory
from cloudvision.api.client import AsyncCVClient
from cloudvision.compliance.client import ComplianceClient

TOKEN_ENV = "CLOUDVISION_TOKEN"
RPC_TIMEOUT = 30


class FetchError(Exception):
    """A failure reported to the user as a one-line message."""


async def _lookup_devices(host, port, token, ca_file, hostname):
    client = AsyncCVClient.from_token(token, host, port=port, cacert=ca_file)
    request = inventory.DeviceStreamRequest(
        partial_eq_filter=[inventory.Device(hostname=hostname)]
    )
    with client as channel:
        stub = inventory.DeviceServiceStub(channel)
        return [
            resp.value async for resp in stub.get_all(request, timeout=RPC_TIMEOUT)
        ]


def find_device(host, port, token, ca_file, hostname):
    try:
        devices = asyncio.run(_lookup_devices(host, port, token, ca_file, hostname))
    except Exception as exc:
        raise FetchError(f"inventory lookup failed: {exc}") from exc
    if not devices:
        raise FetchError(f"no device with hostname {hostname!r} in inventory")
    if len(devices) > 1:
        serials = ", ".join(d.key.device_id for d in devices)
        raise FetchError(f"{len(devices)} devices with hostname {hostname!r}: {serials}")
    return devices[0]


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
    output = args.output or f"{args.hostname}.cfg"
    if os.path.exists(output):
        raise FetchError(f"{output} already exists; not overwriting")
    host, port = parse_server(args.server)
    ca_bytes = None
    if args.ca_file:
        with open(args.ca_file, "rb") as f:
            ca_bytes = f.read()

    device = find_device(host, port, token, args.ca_file, args.hostname)
    serial = device.key.device_id
    version = device.software_version or "unknown"
    config = fetch_running_config(f"{host}:{port}", token, ca_bytes, serial)
    write_new_file(output, config)
    print(
        f"wrote running config for {args.hostname} ({serial}, EOS {version}) to {output}"
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch one device's running config from CloudVision "
        f"and save it unchanged. The service-account token is read from {TOKEN_ENV}."
    )
    parser.add_argument("--server", required=True, help="CloudVision host[:port] (default port 443)")
    parser.add_argument("--hostname", required=True, help="device hostname as shown in CloudVision inventory")
    parser.add_argument("--ca-file", help="PEM CA bundle for on-prem CVP with a private CA")
    parser.add_argument("--output", help="output file (default: <hostname>.cfg); never overwritten")
    args = parser.parse_args(argv)
    try:
        run(args)
    except (FetchError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
