import contextlib
import os
import stat

import pytest
from cloudvision.api.arista.inventory import v1 as inventory
from cloudvision.api.arista.tag import v2 as tag

from ddnetmirror import fetch_config

TOKEN = "secret-token-value"


@pytest.fixture
def cv(monkeypatch, tmp_path):
    """Stub CloudVision: `serials`/`version` for inventory, `tagged` device_ids for
    tag assignments, `result` for get_config."""
    state = {
        "serials": ["SN1"],
        "version": None,
        "hostname": "leaf1",
        "tagged": [],
        "tag_filters": [],
        "result": {"config": "", "errors": [], "filter_codes": [], "sources": []},
        "filters": [],
        "compliance_calls": [],
    }

    class FakeDeviceStub:
        def __init__(self, channel):
            pass

        async def get_all(self, request, timeout=None):
            state["filters"].extend(request.partial_eq_filter)
            for serial in state["serials"]:
                yield inventory.DeviceStreamResponse(
                    value=inventory.Device(
                        key=inventory.DeviceKey(device_id=serial),
                        hostname=state["hostname"],
                        software_version=state["version"],
                    )
                )

    class FakeTagStub:
        def __init__(self, channel):
            pass

        async def get_all(self, request, timeout=None):
            state["tag_filters"].extend(request.partial_eq_filter)
            for device_id in state["tagged"]:
                yield tag.TagAssignmentStreamResponse(
                    value=tag.TagAssignment(key=tag.TagAssignmentKey(device_id=device_id))
                )

    class FakeComplianceClient:
        def __init__(self, server, token, cert=None):
            state["compliance_calls"].append((server, token, cert))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def get_config(self, device_id):
            return state["result"]

    monkeypatch.setattr(
        fetch_config.AsyncCVClient,
        "from_token",
        classmethod(lambda cls, *a, **kw: contextlib.nullcontext()),
    )
    monkeypatch.setattr(fetch_config.inventory, "DeviceServiceStub", FakeDeviceStub)
    monkeypatch.setattr(fetch_config.tag, "TagAssignmentServiceStub", FakeTagStub)
    monkeypatch.setattr(fetch_config, "ComplianceClient", FakeComplianceClient)
    monkeypatch.setenv("CLOUDVISION_TOKEN", TOKEN)
    monkeypatch.chdir(tmp_path)
    return state


def run(*extra, selector=("--hostname", "leaf1")):
    return fetch_config.main(["--server", "cv.example:443", *selector, *extra])


def test_writes_config_verbatim(cv, tmp_path):
    config = "! café\r\nhostname leaf1  \n\ninterface Ethernet1\n   no shutdown"
    cv["result"]["config"] = config

    assert run() == 0

    assert (tmp_path / "leaf1.cfg").read_bytes() == config.encode("utf-8")
    assert cv["filters"][0].hostname == "leaf1"
    assert cv["compliance_calls"] == [("cv.example:443", TOKEN, None)]


@pytest.mark.parametrize(
    "version, shown", [("4.32.1F", "EOS 4.32.1F"), (None, "EOS unknown"), ("", "EOS unknown")]
)
def test_reports_hostname_and_version(cv, tmp_path, capsys, version, shown):
    cv["version"] = version
    cv["result"]["config"] = "hostname leaf1\n"

    assert run() == 0

    out = capsys.readouterr().out
    assert "leaf1" in out and shown in out
    assert (tmp_path / "leaf1.cfg").read_bytes() == b"hostname leaf1\n"


@pytest.mark.parametrize("serials", [[], ["SN1", "SN2"]])
def test_no_or_multiple_matches_write_nothing(cv, tmp_path, capsys, serials):
    cv["serials"] = serials
    cv["result"]["config"] = "hostname leaf1\n"

    assert run() == 1

    assert list(tmp_path.iterdir()) == []
    assert "leaf1" in capsys.readouterr().err
    assert cv["compliance_calls"] == []


@pytest.mark.parametrize(
    "result",
    [
        {"config": "partial", "errors": ["gRPC error: unavailable"]},
        {"config": "", "errors": []},
    ],
)
def test_api_error_or_empty_config_writes_nothing(cv, tmp_path, capsys, result):
    cv["result"] = result

    assert run() == 1

    assert list(tmp_path.iterdir()) == []
    out = capsys.readouterr()
    assert "error:" in out.err
    assert TOKEN not in out.out + out.err


def test_existing_output_is_not_overwritten(cv, tmp_path):
    existing = tmp_path / "leaf1.cfg"
    existing.write_text("keep me")
    cv["result"]["config"] = "hostname leaf1\n"

    assert run() == 1

    assert existing.read_text() == "keep me"


def test_output_is_owner_only(cv, tmp_path):
    cv["result"]["config"] = "hostname leaf1\n"
    old_umask = os.umask(0o022)
    try:
        assert run() == 0
    finally:
        os.umask(old_umask)

    assert stat.S_IMODE((tmp_path / "leaf1.cfg").stat().st_mode) == 0o600


@pytest.mark.parametrize("partial_write", [False, True])
def test_write_or_close_failure_leaves_no_file(cv, monkeypatch, tmp_path, partial_write):
    cv["result"]["config"] = "hostname leaf1\n"
    real_fdopen = os.fdopen

    class FailingFile:
        def __init__(self, fd, mode):
            self.f = real_fdopen(fd, mode)

        def __enter__(self):
            return self

        def write(self, data):
            if partial_write:
                self.f.write(data[:3])
                self.f.flush()
                raise OSError("disk full")
            self.f.write(data)

        def __exit__(self, *exc):
            self.f.close()
            raise OSError("close failed")

    monkeypatch.setattr(fetch_config.os, "fdopen", FailingFile)

    assert run() == 1

    assert list(tmp_path.iterdir()) == []


def test_missing_token_fails(cv, monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDVISION_TOKEN")

    assert run() == 1

    assert list(tmp_path.iterdir()) == []


def test_selects_device_by_tag(cv, tmp_path, capsys):
    cv["tagged"] = ["SN1"]
    cv["version"] = "4.32.1F"
    config = "! café\r\nhostname leaf1  \n"
    cv["result"]["config"] = config

    assert run(selector=("--tag", "role=leaf")) == 0

    assert (tmp_path / "leaf1.cfg").read_bytes() == config.encode("utf-8")
    out = capsys.readouterr().out
    assert "leaf1" in out and "EOS 4.32.1F" in out
    key = cv["tag_filters"][0].key
    assert (key.workspace_id, key.element_type, key.label, key.value) == (
        "", tag.ElementType.DEVICE, "role", "leaf",
    )
    assert cv["filters"][0].key.device_id == "SN1"


@pytest.mark.parametrize("tagged", [[], ["SN1", "SN2"]])
def test_no_or_multiple_tagged_devices_write_nothing(cv, tmp_path, capsys, tagged):
    cv["tagged"] = tagged
    cv["result"]["config"] = "hostname leaf1\n"

    assert run(selector=("--tag", "role=leaf")) == 1

    assert list(tmp_path.iterdir()) == []
    assert "role=leaf" in capsys.readouterr().err
    assert cv["compliance_calls"] == []


def test_tag_rejects_unsafe_inventory_hostname(cv, tmp_path, capsys):
    (tmp_path / "work").mkdir()
    os.chdir(tmp_path / "work")
    cv["tagged"] = ["SN1"]
    cv["hostname"] = "../escaped"
    cv["result"]["config"] = "hostname leaf1\n"

    assert run(selector=("--tag", "role=leaf")) == 1

    assert "--output" in capsys.readouterr().err
    assert sorted(p.name for p in tmp_path.rglob("*")) == ["work"]
    assert cv["compliance_calls"] == []


def test_tag_does_not_overwrite_default_output(cv, tmp_path):
    existing = tmp_path / "leaf1.cfg"
    existing.write_text("keep me")
    cv["tagged"] = ["SN1"]
    cv["result"]["config"] = "hostname leaf1\n"

    assert run(selector=("--tag", "role=leaf")) == 1

    assert existing.read_text() == "keep me"
    assert cv["compliance_calls"] == []


@pytest.mark.parametrize(
    "selector",
    [
        ("--tag", "role"),
        ("--tag", "=leaf"),
        ("--tag", "role="),
        ("--hostname", "leaf1", "--tag", "role=leaf"),
        (),
    ],
)
def test_bad_selector_is_rejected(cv, tmp_path, selector):
    with pytest.raises(SystemExit) as exc:
        run(selector=selector)

    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == []
