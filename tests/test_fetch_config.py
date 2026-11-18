import contextlib

import pytest
from cloudvision.api.arista.inventory import v1 as inventory

from ddnetmirror import fetch_config

TOKEN = "secret-token-value"


@pytest.fixture
def cv(monkeypatch, tmp_path):
    """Stub CloudVision: set `serials` for inventory and `result` for get_config."""
    state = {
        "serials": ["SN1"],
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
                    value=inventory.Device(key=inventory.DeviceKey(device_id=serial))
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
    monkeypatch.setattr(fetch_config, "ComplianceClient", FakeComplianceClient)
    monkeypatch.setenv("CLOUDVISION_TOKEN", TOKEN)
    monkeypatch.chdir(tmp_path)
    return state


def run(*extra):
    return fetch_config.main(["--server", "cv.example:443", "--hostname", "leaf1", *extra])


def test_writes_config_verbatim(cv, tmp_path):
    config = "! café\r\nhostname leaf1  \n\ninterface Ethernet1\n   no shutdown"
    cv["result"]["config"] = config

    assert run() == 0

    assert (tmp_path / "leaf1.cfg").read_bytes() == config.encode("utf-8")
    assert cv["filters"][0].hostname == "leaf1"
    assert cv["compliance_calls"] == [("cv.example:443", TOKEN, None)]


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


def test_missing_token_fails(cv, monkeypatch, tmp_path):
    monkeypatch.delenv("CLOUDVISION_TOKEN")

    assert run() == 1

    assert list(tmp_path.iterdir()) == []
