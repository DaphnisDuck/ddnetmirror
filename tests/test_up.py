import os
import subprocess

import pytest

from ddnetmirror import fetch_config, up

PROJECT = "server: cv.example.invalid\nname: lab1\ntag: site=lab\n"
REAL_GETEUID = os.geteuid


@pytest.fixture
def lab(monkeypatch, tmp_path):
    """A project directory with every outside action faked: docker ps (`containers`,
    `docker_rc`), containerlab deploy (`deploy_rc`) and the fetch step (`fetch_rc`)."""
    exe = tmp_path / "bin" / "containerlab"
    exe.parent.mkdir()
    exe.write_text("")
    exe.chmod(0o755)
    state = {"containers": "", "docker_rc": 0, "deploy_rc": 0, "fetch_rc": 0,
             "runs": [], "fetch": []}

    def fake_run(command, **kwargs):
        state["runs"].append((command, kwargs))
        if command[0] == "docker":
            return subprocess.CompletedProcess(command, state["docker_rc"], state["containers"],
                                               "cannot connect")
        return subprocess.CompletedProcess(command, state["deploy_rc"])

    def fake_fetch(argv, prog=None, base_dir=None):
        state["fetch"].append((argv, prog, base_dir))
        return state["fetch_rc"]

    monkeypatch.setattr(up.subprocess, "run", fake_run)
    monkeypatch.setattr(up.shutil, "which", lambda name: str(exe))
    monkeypatch.setattr(up.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(fetch_config, "main", fake_fetch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "ddnetmirror.yml").write_text(PROJECT)
    state["exe"] = str(exe)
    return state


def test_up_fetches_with_force_then_deploys_with_containerlab(lab, capsys):
    with open("ddnetmirror.yml", "a") as f:
        f.write("cacert: ca.pem\nfixup-rules: rules/lab.yml\nimage: ceos:4.35.7.2M\n")

    assert up.main([], prog="ddn up") == 0

    assert lab["fetch"] == [([
        "--server=cv.example.invalid", "--tag=site=lab", "--output-dir=.", "--lldp",
        "--lab-name=lab1", "--force", "--ca-file=./ca.pem", "--image=ceos:4.35.7.2M",
        "--fixup-rules=rules/lab.yml",
    ], "ddn up", ".")]  # the fixup path is resolved against the project directory (base_dir)
    assert lab["runs"] == [
        (["docker", "ps", "-a", "--filter", "label=containerlab=lab1", "--format", "{{.Names}}"],
         {"capture_output": True, "text": True, "timeout": 30}),
        (["sudo", lab["exe"], "deploy", "-t", "./lab1.clab.yml"], {}),  # output not captured
    ]
    assert f"deploying: sudo {lab['exe']} deploy -t ./lab1.clab.yml" in capsys.readouterr().out


def test_token_file_is_taken_from_the_command_line_and_forwarded_to_fetch(
        lab, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("CLOUDVISION_TOKEN", "env-token")
    monkeypatch.setattr(up.os, "geteuid", REAL_GETEUID)  # the token file checks need the real uid
    (tmp_path / "tokens").mkdir(mode=0o700)
    (tmp_path / "tokens" / "staging").write_text("staging-token\n")
    (tmp_path / "tokens" / "staging").chmod(0o600)
    tokens = []

    def fetch(argv, prog=None, base_dir=None):  # the real fetch parser and token reading
        tokens.append(fetch_config.read_token(fetch_config.build_parser().parse_args(argv).token_file))
        return 0

    monkeypatch.setattr(fetch_config, "main", fetch)

    assert up.main(["--token-file", "~/tokens/staging"]) == 0

    assert tokens == ["staging-token"]
    assert "staging-token" not in str(lab["runs"]) + str(capsys.readouterr())
    assert up.fetch_args(up.load_project("."), ".", "~/tokens/staging")[-1] == (
        "--token-file=~/tokens/staging")


def test_generated_fetch_arguments_are_valid_for_ddn_fetch():
    project = {"server": "cv", "name": "lab1", "tag": "site=lab", "fixup-rules": "none"}
    args = fetch_config.build_parser().parse_args(up.fetch_args(project))

    assert (args.output_dir, args.lab_name, args.lldp, args.force, args.fixup_rules) == (
        ".", "lab1", True, True, "none")


def test_values_starting_with_a_dash_stay_values():
    project = {"server": "-cv", "name": "lab1", "tag": "-site=lab", "fixup-rules": "-rules.yml",
               "image": "-ceos:1", "cacert": "-ca.pem"}
    args = fetch_config.build_parser().parse_args(up.fetch_args(project))

    assert (args.server, args.tag[1], args.fixup_rules, args.image, args.ca_file) == (
        "-cv", ("tag", "-site", "lab"), "-rules.yml", "-ceos:1", "-ca.pem")


def test_project_tag_may_be_an_expression_passed_to_fetch_unchanged(lab, tmp_path):
    expression = "(site=lab or site=dr) and not role=spine"
    (tmp_path / "ddnetmirror.yml").write_text(f"server: cv\nname: lab1\ntag: {expression}\n")

    assert up.main([]) == 0

    argv = lab["fetch"][0][0]
    assert f"--tag={expression}" in argv
    assert fetch_config.build_parser().parse_args(argv).tag == fetch_config.parse_tag(expression)


def test_exclude_hosts_become_exclude_host_options(lab, tmp_path):
    (tmp_path / "ddnetmirror.yml").write_text(PROJECT + "exclude-hosts:\n  - leaf3\n  - -leaf4\n")

    assert up.main([]) == 0

    argv = lab["fetch"][0][0]
    assert argv[-2:] == ["--exclude-host=leaf3", "--exclude-host=-leaf4"]
    assert fetch_config.build_parser().parse_args(argv).exclude_host == ["leaf3", "-leaf4"]


def test_containerlab_that_cannot_be_started_is_a_clear_error(lab, monkeypatch, capsys):
    real_run = up.subprocess.run

    def run(command, **kwargs):
        if command[0] == "sudo":
            raise FileNotFoundError(2, "No such file or directory", "sudo")
        return real_run(command, **kwargs)

    monkeypatch.setattr(up.subprocess, "run", run)

    assert up.main([]) == 1

    err = capsys.readouterr().err
    assert "could not run sudo: No such file or directory; nothing was deployed or torn down" in err
    assert f"remove a partial lab with: sudo {lab['exe']} destroy -t ./lab1.clab.yml" in err


def test_containerlab_binary_missing_at_launch_uses_the_resolved_command(lab, monkeypatch, capsys):
    real_run = up.subprocess.run

    def run(command, **kwargs):
        if command[0] == "sudo":  # the binary vanished after it was found
            monkeypatch.setattr(up.shutil, "which", lambda name: None)
            os.remove(lab["exe"])
            raise FileNotFoundError(2, "No such file or directory", "sudo")
        return real_run(command, **kwargs)

    monkeypatch.setattr(up.subprocess, "run", run)

    assert up.main([]) == 1

    err = capsys.readouterr().err
    assert f"Check with: sudo {lab['exe']} inspect -t ./lab1.clab.yml" in err
    assert f"remove a partial lab with: sudo {lab['exe']} destroy -t ./lab1.clab.yml" in err


def test_containerlab_runs_without_sudo_as_root(lab, monkeypatch):
    monkeypatch.setattr(up.os, "geteuid", lambda: 0)

    assert up.containerlab_command("deploy", "-t", "x.clab.yml") == [lab["exe"], "deploy", "-t", "x.clab.yml"]


@pytest.mark.parametrize("project, message", [
    (PROJECT + "token: abc\n", "unknown key token (allowed: server, name, tag, cacert, fixup-rules, image"),
    ("name: lab1\ntag: site=lab\n", "missing server"),
    ("server: cv\ntag: site=lab\n", "missing name"),
    ("server: cv\nname: lab1\n", "missing tag"),
    ("server: cv\nname: lab1\ntag: site\n", "expected KEY=VALUE, got 'site'"),
    ("server: cv\nname: lab1\ntag: site=lab and\n", "bad tag expression 'site=lab and'"),
    ("server: cv\nname: -lab\ntag: site=lab\n", "is not a valid Containerlab lab name"),
    ("server: 443\nname: lab1\ntag: site=lab\n", "server must be a non-empty string"),
    (PROJECT + "image: auto\n", "leave image out"),
    (PROJECT + "exclude-hosts: leaf3\n", "exclude-hosts must be a list of hostnames"),
    ("- server: cv\n", "must be a mapping"),
    (None, "no ./ddnetmirror.yml: run ddn up in a lab project directory"),
])
def test_bad_project_file_stops_before_docker_or_cloudvision(lab, tmp_path, capsys, project, message):
    if project is None:
        os.remove("ddnetmirror.yml")
    else:
        (tmp_path / "ddnetmirror.yml").write_text(project)

    assert up.main([]) == 1

    assert message in capsys.readouterr().err
    assert lab["runs"] == [] and lab["fetch"] == []


def test_loading_the_project_runs_nothing(lab, tmp_path):
    (tmp_path / "proj").mkdir()
    (tmp_path / "proj" / "ddnetmirror.yml").write_text(
        PROJECT + "cacert: /etc/ca.pem\nfixup-rules: none\n")

    project = up.load_project("proj")

    assert project == {"server": "cv.example.invalid", "name": "lab1", "tag": "site=lab",
                       "cacert": "/etc/ca.pem", "fixup-rules": "none"}
    assert up.load_project(".")["name"] == "lab1"
    assert lab["runs"] == [] and lab["fetch"] == []


def test_existing_lab_is_refused_without_touching_files(lab, tmp_path, capsys):
    lab["containers"] = "clab-lab1-leaf1\nclab-lab1-leaf2\n"
    before = sorted(p.name for p in tmp_path.iterdir())

    assert up.main([]) == 1

    assert lab["fetch"] == [] and len(lab["runs"]) == 1  # only the docker ps check
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    err = capsys.readouterr().err
    assert ("lab 'lab1' already has containers (clab-lab1-leaf1, clab-lab1-leaf2); "
            "not regenerating or deploying") in err
    assert f"sudo {lab['exe']} destroy -t ./lab1.clab.yml" in err


def test_failed_docker_check_is_a_refusal(lab, capsys):
    lab["docker_rc"] = 1

    assert up.main([]) == 1

    assert "cannot check for an existing lab 'lab1': 'docker ps' failed: cannot connect" in (
        capsys.readouterr().err)
    assert lab["fetch"] == []


@pytest.mark.parametrize("fetch_rc", [1, 2])
def test_incomplete_collection_does_not_deploy(lab, capsys, fetch_rc):
    lab["fetch_rc"] = fetch_rc

    assert up.main([]) == fetch_rc

    assert [command[0] for command, _ in lab["runs"]] == ["docker"]  # no deploy
    assert "not deploying: collection or topology generation did not complete" in (
        capsys.readouterr().err)


def test_containerlab_exit_status_is_returned_and_nothing_is_torn_down(lab, capsys):
    lab["deploy_rc"] = 3

    assert up.main([]) == 3

    assert [command[1:3] for command, _ in lab["runs"][1:]] == [[lab["exe"], "deploy"]]
    err = capsys.readouterr().err
    assert "containerlab deploy failed (exit 3); nothing was torn down" in err
    assert f"remove: sudo {lab['exe']} destroy -t ./lab1.clab.yml" in err
