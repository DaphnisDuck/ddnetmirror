import pytest

from ddnetmirror import cli, fetch_config, fixup


@pytest.mark.parametrize("command, module", [("fetch", fetch_config), ("fixup", fixup)])
def test_ddn_dispatches_arguments_unchanged(monkeypatch, command, module):
    assert cli.COMMANDS[command][0] is module.main  # the alias entry point itself
    calls = []
    monkeypatch.setitem(
        cli.COMMANDS, command, (lambda argv, prog: calls.append((argv, prog)) or 7, "")
    )
    argv = ["--rules", "r.yml", "in.cfg", "--output", "-x"]

    assert cli.main([command, *argv]) == 7  # exit status passed through
    assert calls == [(argv, f"ddn {command}")]


def test_ddn_fixup_runs_the_same_logic_as_the_alias(tmp_path):
    (tmp_path / "in.cfg").write_text("hostname leaf1\n")
    (tmp_path / "rules.yml").write_text("- {op: replace, match: leaf1, with: lab-leaf1}\n")
    common = [str(tmp_path / "in.cfg"), "--rules", str(tmp_path / "rules.yml"), "--output"]

    assert cli.main(["fixup", *common, str(tmp_path / "ddn.cfg")]) == 0
    assert fixup.main([*common, str(tmp_path / "alias.cfg")]) == 0
    assert (tmp_path / "ddn.cfg").read_bytes() == (tmp_path / "alias.cfg").read_bytes()
    assert cli.main(["fixup", *common, str(tmp_path / "ddn.cfg")]) == 1  # never overwrites


@pytest.mark.parametrize("command, usage", [("fetch", "usage: ddn fetch"), ("fixup", "usage: ddn fixup")])
def test_subcommand_help_names_ddn(capsys, command, usage):
    with pytest.raises(SystemExit) as exc:
        cli.main([command, "--help"])

    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith(usage)


@pytest.mark.parametrize("argv, message", [([], "required: COMMAND"), (["nope"], "invalid choice: 'nope'")])
def test_missing_or_unknown_command_is_a_usage_error(capsys, argv, message):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("usage: ddn") and message in err
