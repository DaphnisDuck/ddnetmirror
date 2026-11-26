import os

import pytest

from ddnetmirror import build, fetch_config

PROJECT = "server: cv.example.invalid\nname: lab1\ntag: site=lab\n"


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """A project directory with the fetch step faked (`fetch_rc`, calls in `fetch`)."""
    state = {"fetch_rc": 0, "fetch": []}

    def fake_fetch(argv, prog=None, base_dir=None):
        state["fetch"].append((argv, prog, base_dir))
        return state["fetch_rc"]

    monkeypatch.setattr(fetch_config, "main", fake_fetch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ddnetmirror.yml").write_text(PROJECT)
    return state


@pytest.mark.parametrize("extra, force", [([], []), (["--force"], ["--force"])])
def test_build_runs_fetch_for_the_project_with_force_only_when_asked(lab, extra, force):
    with open("ddnetmirror.yml", "a") as f:
        f.write("cacert: ca.pem\nfixup-rules: rules/lab.yml\nimage: ceos:4.35.7.2M\n")

    assert build.main(extra, prog="ddn build") == 0

    assert lab["fetch"] == [([
        "--server=cv.example.invalid", "--tag=site=lab", "--output-dir=.", "--lldp",
        "--lab-name=lab1", "--ca-file=./ca.pem", "--image=ceos:4.35.7.2M",
        "--fixup-rules=rules/lab.yml", *force,
    ], "ddn build", ".")]  # the fixup path is resolved against the project directory (base_dir)


@pytest.mark.parametrize("fetch_rc", [1, 2])
def test_fetch_exit_status_is_returned(lab, fetch_rc):
    lab["fetch_rc"] = fetch_rc

    assert build.main([]) == fetch_rc


def test_token_file_is_taken_from_the_command_line_and_forwarded_to_fetch(
        lab, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLOUDVISION_TOKEN", "env-token")
    (tmp_path / "tokens").mkdir(mode=0o700)
    (tmp_path / "tokens" / "staging").write_text("staging-token\n")
    (tmp_path / "tokens" / "staging").chmod(0o600)
    tokens = []

    def fetch(argv, prog=None, base_dir=None):  # the real fetch parser and token reading
        tokens.append(fetch_config.read_token(fetch_config.build_parser().parse_args(argv).token_file))
        return 0

    monkeypatch.setattr(fetch_config, "main", fetch)

    assert build.main(["--token-file", "~/tokens/staging"]) == 0

    assert tokens == ["staging-token"]
    assert "staging-token" not in str(lab["fetch"]) + str(capsys.readouterr())
    assert build.fetch_args(build.load_project("."), ".", "~/tokens/staging")[-1] == (
        "--token-file=~/tokens/staging")


def test_generated_fetch_arguments_are_valid_for_ddn_fetch():
    project = {"server": "cv", "name": "lab1", "tag": "site=lab", "fixup-rules": "none"}
    args = fetch_config.build_parser().parse_args(build.fetch_args(project))

    assert (args.output_dir, args.lab_name, args.lldp, args.force, args.fixup_rules) == (
        ".", "lab1", True, False, "none")


def test_values_starting_with_a_dash_stay_values():
    project = {"server": "-cv", "name": "lab1", "tag": "-site=lab", "fixup-rules": "-rules.yml",
               "image": "-ceos:1", "cacert": "-ca.pem"}
    args = fetch_config.build_parser().parse_args(build.fetch_args(project))

    assert (args.server, args.tag[1], args.fixup_rules, args.image, args.ca_file) == (
        "-cv", ("tag", "-site", "lab"), "-rules.yml", "-ceos:1", "-ca.pem")


@pytest.mark.parametrize("expression", [
    "(site=lab or site=dr) and not role=spine",
    "(site=lab OR site=dr) And NOT role=spine",
])
def test_project_tag_may_be_an_expression_passed_to_fetch_unchanged(lab, tmp_path, expression):
    (tmp_path / "ddnetmirror.yml").write_text(f"server: cv\nname: lab1\ntag: {expression}\n")

    assert build.main([]) == 0

    argv = lab["fetch"][0][0]
    assert f"--tag={expression}" in argv
    assert fetch_config.build_parser().parse_args(argv).tag == fetch_config.parse_tag(expression)


def test_exclude_hosts_become_exclude_host_options(lab, tmp_path):
    (tmp_path / "ddnetmirror.yml").write_text(PROJECT + "exclude-hosts:\n  - leaf3\n  - -leaf4\n")

    assert build.main([]) == 0

    argv = lab["fetch"][0][0]
    assert argv[-2:] == ["--exclude-host=leaf3", "--exclude-host=-leaf4"]
    assert fetch_config.build_parser().parse_args(argv).exclude_host == ["leaf3", "-leaf4"]


@pytest.mark.parametrize("project, message", [
    (PROJECT + "token: abc\n", "unknown key token (allowed: server, name, tag, cacert, fixup-rules, image"),
    ("name: lab1\ntag: site=lab\n", "missing server"),
    ("server: cv\ntag: site=lab\n", "missing name"),
    ("server: cv\nname: lab1\n", "missing tag"),
    ("server: cv\nname: lab1\ntag: not\n", "'not' is an operator, not a tag key"),
    ("server: cv\nname: lab1\ntag: site=lab and\n", "bad tag expression 'site=lab and'"),
    ("server: cv\nname: -lab\ntag: site=lab\n", "is not a valid Containerlab lab name"),
    ("server: 443\nname: lab1\ntag: site=lab\n", "server must be a non-empty string"),
    (PROJECT + "image: auto\n", "leave image out"),
    (PROJECT + "exclude-hosts: leaf3\n", "exclude-hosts must be a list of hostnames"),
    ("- server: cv\n", "must be a mapping"),
    (None, "no ./ddnetmirror.yml: run ddn in a lab project directory"),
])
def test_bad_project_file_stops_before_cloudvision(lab, tmp_path, capsys, project, message):
    if project is None:
        os.remove("ddnetmirror.yml")
    else:
        (tmp_path / "ddnetmirror.yml").write_text(project)

    assert build.main([]) == 1

    assert message in capsys.readouterr().err
    assert lab["fetch"] == []


def test_loading_the_project_runs_nothing(lab, tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "ddnetmirror.yml").write_text(
        PROJECT + "cacert: /etc/ca.pem\nfixup-rules: none\n")

    project = build.load_project("proj")

    assert project == {"server": "cv.example.invalid", "name": "lab1", "tag": "site=lab",
                       "cacert": "/etc/ca.pem", "fixup-rules": "none"}
    assert build.load_project(".")["name"] == "lab1"
    assert lab["fetch"] == []


def test_tilde_in_project_paths_is_expanded(lab, monkeypatch, tmp_path):
    # relative paths stay relative to the project directory (first test above)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    with open("ddnetmirror.yml", "a") as f:
        f.write("cacert: ~/ca.pem\nfixup-rules: ~/rules.yml\n")

    project = build.load_project(".")

    assert project["cacert"] == str(tmp_path / "home" / "ca.pem")
    assert project["fixup-rules"] == str(tmp_path / "home" / "rules.yml")
