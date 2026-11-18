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
    """Stub CloudVision: `serials`/`version`/`hostname` for inventory (`hostnames`,
    `versions` override per serial), `tagged` device_ids for tag assignments,
    `result` for get_config (`results` override per serial), `lldp` notifications
    per serial for the Connector (an Exception value is raised)."""
    state = {
        "serials": ["SN1"],
        "version": None,
        "hostname": "leaf1",
        "hostnames": {},
        "versions": {},
        "results": {},
        "config_ids": [],
        "lldp": {},
        "connector": [],
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
            wanted = request.partial_eq_filter[0].key.device_id
            for serial in state["serials"]:
                if wanted is not None and wanted != serial:
                    continue
                yield inventory.DeviceStreamResponse(
                    value=inventory.Device(
                        key=inventory.DeviceKey(device_id=serial),
                        hostname=state["hostnames"].get(serial, state["hostname"]),
                        software_version=state["versions"].get(serial, state["version"]),
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
            state["config_ids"].append(device_id)
            return state["results"].get(device_id, state["result"])

    class FakeConnector:
        def __init__(self, addr, tokenValue=None, ca=None):
            state["connector"].append((addr, tokenValue, ca))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def get(self, queries):
            data = state["lldp"].get(queries[0].dataset.name, [])
            if isinstance(data, Exception):
                raise data
            yield {"notifications": data}

    monkeypatch.setattr(fetch_config.lldp, "GRPCClient", FakeConnector)
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
        ("--hostname", "leaf1", "--output-dir", "configs"),
        ("--tag", "role=leaf", "--output", "x.cfg", "--output-dir", "configs"),
        ("--tag", "role=leaf", "--lldp"),
        (),
    ],
)
def test_bad_selector_is_rejected(cv, tmp_path, selector):
    with pytest.raises(SystemExit) as exc:
        run(selector=selector)

    assert exc.value.code == 2
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def batch(cv):
    """Three devices tagged site=lab, each with its own config."""
    cv["serials"] = cv["tagged"] = ["SN1", "SN2", "SN3"]
    cv["hostnames"] = {"SN1": "leaf1", "SN2": "leaf2", "SN3": "spine1"}
    cv["versions"] = {"SN1": "4.32.1F", "SN2": "4.31.0F"}
    for serial in cv["serials"]:
        cv["results"][serial] = {"config": f"! {serial} café\r\nend", "errors": []}
    return cv


def run_batch():
    return run(selector=("--tag", "site=lab", "--output-dir", "configs"))


def test_batch_collects_every_tagged_device(batch, tmp_path, capsys):
    assert run_batch() == 0

    for serial, name in batch["hostnames"].items():
        path = tmp_path / "configs" / f"{name}.cfg"
        assert path.read_bytes() == f"! {serial} café\r\nend".encode("utf-8")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    out = capsys.readouterr().out
    assert "leaf1 (SN1, EOS 4.32.1F)" in out
    assert "spine1 (SN3, EOS unknown)" in out
    assert "collected 3 of 3" in out


def test_batch_keeps_successes_when_one_device_fails(batch, tmp_path, capsys):
    batch["results"]["SN2"] = {"config": "partial", "errors": ["gRPC error: unavailable"]}

    assert run_batch() == 1

    assert sorted(p.name for p in (tmp_path / "configs").iterdir()) == ["leaf1.cfg", "spine1.cfg"]
    out = capsys.readouterr()
    assert "leaf2 (SN2)" in out.err and "unavailable" in out.err
    assert "collected 2 of 3" in out.out and "1 failed" in out.out


def test_batch_with_no_tagged_devices_fails(batch, tmp_path, capsys):
    batch["tagged"] = []

    assert run_batch() == 1

    assert list(tmp_path.iterdir()) == []
    assert "no device with tag site=lab" in capsys.readouterr().err


def test_batch_never_overwrites_existing_file(batch, tmp_path, capsys):
    (tmp_path / "configs").mkdir()
    existing = tmp_path / "configs" / "leaf2.cfg"
    existing.write_text("keep me")

    assert run_batch() == 1

    assert existing.read_text() == "keep me"
    assert "SN2" not in batch["config_ids"]
    assert (tmp_path / "configs" / "spine1.cfg").exists()


@pytest.mark.parametrize(
    "hostnames, rejected",
    [
        ({"SN2": "../escaped"}, ["SN2"]),
        ({"SN2": "leaf1"}, ["SN1", "SN2"]),
    ],
)
def test_batch_rejects_unsafe_or_colliding_names(batch, tmp_path, capsys, hostnames, rejected):
    batch["hostnames"].update(hostnames)

    assert run_batch() == 1

    assert not any(serial in batch["config_ids"] for serial in rejected)
    assert (tmp_path / "configs" / "spine1.cfg").exists()
    assert sorted(p.name for p in tmp_path.rglob("*.cfg")) == sorted(
        f"{batch['hostnames'][s]}.cfg" for s in batch["serials"] if s not in rejected
    )
    err = capsys.readouterr().err
    assert all(f"({serial})" in err for serial in rejected)


LLDP_LOCAL = ["Sysdb", "l2discovery", "lldp", "status", "local", "1", "portStatus"]


def port(intf):
    return {"path_elements": LLDP_LOCAL + [intf], "updates": {"name": intf}}


def nbr(intf, sysname, port_id, idx=1):
    """A remoteSystem notification shaped as read by get_lldp_nei.py."""
    return {
        "path_elements": LLDP_LOCAL + [intf, "remoteSystem", idx],
        "updates": {
            "sysName": {"value": {"value": sysname}},
            "msap": {"portIdentifier": {"portId": port_id}},
        },
    }


def run_lldp():
    return run(selector=("--tag", "site=lab", "--output-dir", "configs", "--lldp"))


def test_lldp_reports_reciprocal_and_one_sided_links_once(batch, capsys):
    batch["lldp"] = {
        "SN1": [  # leaf1, with a duplicate observation of the same neighbor
            port("Ethernet1"),
            nbr("Ethernet1", "spine1", "Ethernet49/1"),
            nbr("Ethernet1", "spine1", "Ethernet49/1", idx=2),
        ],
        "SN2": [nbr("Ethernet12", "spine1", "Ethernet50/1")],  # leaf2
        "SN3": [  # spine1 sees leaf1 but not leaf2
            port("Ethernet49/1"),
            port("Ethernet50/1"),
            nbr("Ethernet49/1", "leaf1", "Ethernet1"),
        ],
    }

    assert run_lldp() == 0

    out = capsys.readouterr()
    assert out.out.splitlines()[-3:] == [
        "LLDP links between selected devices: 2",
        "leaf1 Ethernet1 <-> spine1 Ethernet49/1 [reciprocal]",
        "leaf2 Ethernet12 <-> spine1 Ethernet50/1 [one-sided]",
    ]
    assert "skipped LLDP" not in out.err
    assert batch["connector"] == [("cv.example:443", TOKEN, None)]


@pytest.mark.parametrize(
    "case, reason",
    [
        ("out_of_scope", "leaf1 Ethernet3 -> host7 eth0: not in selection"),
        ("ambiguous", "leaf1 Ethernet1 -> spine1: ambiguous hostname"),
        ("missing_port_id", "leaf1 Ethernet1: neighbor without sysName/port id"),
        ("empty_local_port", "leaf1 : neighbor without sysName/port id"),
        ("port_id_not_interface", "not an LLDP interface reported by spine1"),
        ("unavailable", "spine1: LLDP data unavailable"),
        ("query_error", "spine1: LLDP data unavailable"),
    ],
)
def test_lldp_never_turns_unclear_observations_into_links(batch, capsys, case, reason):
    to_spine = nbr("Ethernet1", "spine1", "Ethernet49/1")
    spine = [port("Ethernet49/1")]
    leaf1 = {
        "out_of_scope": [nbr("Ethernet3", "host7", "eth0")],
        "missing_port_id": [nbr("Ethernet1", "spine1", "")],
        "empty_local_port": [nbr("", "spine1", "Ethernet49/1")],
        "port_id_not_interface": [nbr("Ethernet1", "spine1", "001c.73aa.bbcc")],
    }.get(case, [to_spine])
    batch["lldp"] = {"SN1": leaf1, "SN3": spine}
    if case == "ambiguous":
        batch["hostnames"]["SN2"] = "spine1"
    if case == "unavailable":
        del batch["lldp"]["SN3"]
    if case == "query_error":
        batch["lldp"]["SN3"] = RuntimeError("unavailable")

    # LLDP problems never change the exit status; "ambiguous" fails its config writes.
    assert run_lldp() == (1 if case == "ambiguous" else 0)

    out = capsys.readouterr()
    assert "LLDP links between selected devices: 0" in out.out
    assert "<->" not in out.out
    assert reason in out.err


def test_lldp_skips_links_sharing_an_interface(batch, capsys):
    batch["lldp"] = {
        "SN1": [nbr("Ethernet1", "spine1", "Ethernet49/1"), nbr("Ethernet2", "spine1", "Ethernet50/1")],
        "SN2": [nbr("Ethernet12", "spine1", "Ethernet49/1")],
        "SN3": [port("Ethernet49/1"), port("Ethernet50/1")],
    }

    assert run_lldp() == 0

    out = capsys.readouterr()
    assert out.out.splitlines()[-2:] == [
        "LLDP links between selected devices: 1",
        "leaf1 Ethernet2 <-> spine1 Ethernet50/1 [one-sided]",
    ]
    assert "leaf1 Ethernet1 <-> spine1 Ethernet49/1: interface seen in several links" in out.err
    assert "leaf2 Ethernet12 <-> spine1 Ethernet49/1: interface seen in several links" in out.err


def test_lldp_diagnostics_escape_control_characters(batch, capsys):
    batch["lldp"] = {"SN1": [nbr("Ethernet3", "evil\n\x1b[31mhost", "eth0")]}

    run_lldp()

    err = capsys.readouterr().err
    assert "evil\\x0a\\x1b[31mhost eth0: not in selection" in err
    assert "\x1b" not in err
