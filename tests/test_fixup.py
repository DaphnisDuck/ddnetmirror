import stat

import pytest

from ddnetmirror import fixup


def apply(rules_yaml, text):
    return fixup.apply_rules(fixup.load_rules(rules_yaml), text, report=lambda _: None)


def test_literal_and_regex_replace_only_change_matched_text():
    text = "! device: x\nhostname leaf1\n!\nip name-server 192.0.2.1\n"
    rules = """
- {op: replace, match: "192.0.2.1", with: "198.51.100.1"}
- {op: replace, match: '^hostname (\\S+)$', with: 'hostname lab-\\1', regex: true}
"""
    assert apply(rules, text) == (
        "! device: x\nhostname lab-leaf1\n!\nip name-server 198.51.100.1\n"
    )


def test_delete_line_keeps_indented_children():
    text = "daemon TerminAttr\n   exec /usr/bin/TerminAttr\n   no shutdown\n!\nend\n"
    assert apply("- {op: delete-line, match: daemon TerminAttr}", text) == (
        "   exec /usr/bin/TerminAttr\n   no shutdown\n!\nend\n"
    )


def test_delete_block_removes_nested_children_and_indented_separators():
    text = (
        "router bgp 65001\n"
        "   router-id 10.0.0.1\n"
        "   !\n"
        "   vrf blue\n"
        "      rd 10.0.0.1:1\n"
        "      !\n"
        "\n"
        "      neighbor 10.1.1.1 remote-as 65002\n"
        "   !\n"
        "   vrf red\n"
        "      rd 10.0.0.1:2\n"
        "!\n"
        "!\n"
        "management api http-commands\n"
        "   no shutdown\n"
    )
    # A nested start line stops at equal indentation (the indented "!" after it).
    assert apply("- {op: delete-block, match: '^   vrf blue$', regex: true}", text) == (
        text.replace(
            "   vrf blue\n      rd 10.0.0.1:1\n      !\n\n      neighbor 10.1.1.1 remote-as 65002\n", ""
        )
    )
    # A top-level block stops at the unindented "!", which is kept; redundant "!" stays.
    assert apply("- {op: delete-block, match: router bgp}", text) == (
        "!\n!\nmanagement api http-commands\n   no shutdown\n"
    )


@pytest.mark.parametrize("present", [False, True])
def test_insert_if_missing_appends_fragment_verbatim(present):
    text = "hostname leaf1\n!\n" + ("interface Management0\n   ip address 192.0.2.9/24\n!\n" if present else "")
    rules = """
- op: insert-if-missing
  match: '^interface Management0$'
  regex: true
  text: |
    !
    interface Management0
       ip address 172.20.20.2/24
    !
"""
    fragment = "!\ninterface Management0\n   ip address 172.20.20.2/24\n!\n"
    assert apply(rules, text) == (text if present else text + fragment)


def test_rules_run_in_order_and_line_endings_are_preserved():
    text = "hostname leaf1\r\n!\r\nlogging host 192.0.2.5\r\nend"  # CRLF, no final newline
    rules = """
- {op: replace, match: logging host, with: "no-op-marker logging host"}
- {op: delete-line, match: no-op-marker}
- {op: insert-if-missing, match: banner, text: "banner motd\\nEOF\\n"}
"""
    assert apply(rules, text) == "hostname leaf1\r\n!\r\nend\r\nbanner motd\nEOF\n"


def test_replacement_with_line_break_is_split_for_later_rules():
    text = "hostname leaf1\r\n!\r\nend\r\n"
    rules = r"""
- {op: replace, match: '^hostname (\S+)$', with: "hostname lab-\\1\r\nmarker", regex: true}
- {op: delete-line, match: marker}
"""
    assert apply(rules, text) == "hostname lab-leaf1\r\n!\r\nend\r\n"


def test_collapse_separators_only_merges_adjacent_unindented_bang_lines():
    text = (
        "! Command: show running-config\r\n"
        "!\r\n"
        "!\r\n"
        "!\r\n"
        "hostname leaf1\r\n"
        "!\r\n"
        "\r\n"
        "!\r\n"
        "router bgp 1\r\n"
        "   !\r\n"
        "   !\r\n"
        "!\r\n"
        "! end of bgp\r\n"
        "!\r\n"
        "!"  # no final newline
    )
    assert apply("- op: collapse-separators", text) == (
        "! Command: show running-config\r\n"
        "!\r\n"
        "hostname leaf1\r\n"
        "!\r\n"
        "\r\n"
        "!\r\n"
        "router bgp 1\r\n"
        "   !\r\n"
        "   !\r\n"
        "!\r\n"
        "! end of bgp\r\n"
        "!"
    )


def test_collapse_separators_sees_earlier_rules_output():
    text = "hostname leaf1\n!\ndaemon TerminAttr\n   no shutdown\n!\nend\n"
    collapse_then_delete = "- op: collapse-separators\n- {op: delete-block, match: daemon}"
    delete_then_collapse = "- {op: delete-block, match: daemon}\n- op: collapse-separators"
    assert apply(collapse_then_delete, text) == "hostname leaf1\n!\n!\nend\n"
    assert apply(delete_then_collapse, text) == "hostname leaf1\n!\nend\n"


@pytest.mark.parametrize("template", ["\\2", "\\g<nope>", "\\g<1", "\\q"])
def test_invalid_replacement_template_is_rejected_at_load(template):
    rules = f"- {{op: replace, match: 'host(name)', regex: true, with: '{template}'}}"
    with pytest.raises(fixup.RuleError, match=r"rule 1 \(replace\): invalid replacement"):
        fixup.load_rules(rules)


def test_realistic_config_combining_all_operations():
    before = """\
! Command: show running-config
! device: leaf1 (cEOSLab, EOS-4.35.7.2M)
!
hostname leaf1
!
daemon TerminAttr
   exec /usr/bin/TerminAttr -cvaddr=apiserver.example.net:443 -taillogs
   no shutdown
!
management api gnmi
   transport grpc default
   !
   provider eos-native
!
interface Ethernet1
   description to-spine1
   no switchport
   ip address 10.0.0.1/31
!
ip routing
!
end
"""
    rules = """
- {op: replace, match: '^hostname (\\S+)$', with: 'hostname lab-\\1', regex: true}
- {op: delete-block, match: daemon TerminAttr}
- {op: delete-block, match: '^management api gnmi$', regex: true}
- {op: delete-line, match: '^end$', regex: true}
- op: insert-if-missing
  match: '^interface Management0$'
  regex: true
  text: |
    interface Management0
       ip address 172.20.20.2/24
    !
- {op: insert-if-missing, match: '^end$', regex: true, text: "end\\n"}
"""
    assert apply(rules, before) == """\
! Command: show running-config
! device: leaf1 (cEOSLab, EOS-4.35.7.2M)
!
hostname lab-leaf1
!
!
!
interface Ethernet1
   description to-spine1
   no switchport
   ip address 10.0.0.1/31
!
ip routing
!
interface Management0
   ip address 172.20.20.2/24
!
end
"""


def test_cli_writes_new_owner_only_file_and_keeps_original(tmp_path):
    original = "hostname leaf1\n!\nend\n"
    (tmp_path / "in.cfg").write_text(original)
    (tmp_path / "rules.yml").write_text("- {op: replace, match: leaf1, with: lab-leaf1}\n")
    out = tmp_path / "out.cfg"

    args = [str(tmp_path / "in.cfg"), "--rules", str(tmp_path / "rules.yml"), "--output", str(out)]
    assert fixup.main(args) == 0

    assert (tmp_path / "in.cfg").read_text() == original
    assert out.read_text() == "hostname lab-leaf1\n!\nend\n"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600

    assert fixup.main(args) == 1  # never overwrites
    assert out.read_text() == "hostname lab-leaf1\n!\nend\n"


@pytest.mark.parametrize(
    "rules, message",
    [
        ("- {op: rename, match: x}", "unknown op 'rename'"),
        ("- {op: delete-line, match: '(', regex: true}", "invalid regex"),
        ("- {op: replace, match: x}", "missing with"),
        ("- {op: delete-line, match: x, regx: true}", "unknown field regx"),
        ("rules: [", "not valid YAML"),
        ("- {op: replace, match: 'x(y)', with: '\\2', regex: true}", "invalid replacement"),
        ("- {op: collapse-separators, match: '!'}", "unknown field match"),
    ],
)
def test_bad_rule_file_fails_without_output(tmp_path, capsys, rules, message):
    (tmp_path / "in.cfg").write_text("hostname leaf1\n")
    (tmp_path / "rules.yml").write_text(rules)
    out = tmp_path / "out.cfg"

    assert fixup.main([str(tmp_path / "in.cfg"), "--rules", str(tmp_path / "rules.yml"),
                       "--output", str(out)]) == 1

    assert not out.exists()
    assert message in capsys.readouterr().err


VERBOSE_RULES = """\
- {op: replace, match: leaf1, with: lab-leaf1}
- {op: delete-line, match: nothing-matches}
- {op: delete-block, match: daemon TerminAttr}
- {op: insert-if-missing, match: '^end$', regex: true, text: "end\\n"}
- op: collapse-separators
"""
VERBOSE_INPUT = "hostname leaf1\n!\ndaemon TerminAttr\n   no shutdown\n!\nend\n"


@pytest.mark.parametrize("verbose", [False, True])
def test_cli_reports_each_rule_and_verbose_adds_only_detail(tmp_path, capsys, verbose):
    (tmp_path / "in.cfg").write_text(VERBOSE_INPUT)
    (tmp_path / "rules.yml").write_text(VERBOSE_RULES)
    out = tmp_path / "out.cfg"
    args = [str(tmp_path / "in.cfg"), "--rules", str(tmp_path / "rules.yml"), "--output", str(out)]

    assert fixup.main(args + ["--verbose"] * verbose) == 0

    details = [" (1 line(s) replaced)", " (0 line(s) deleted)", " (1 block(s) removed)",
               " (match present, nothing inserted)", " (1 separator run(s) collapsed)"]
    lines = ["rule 1 replace: 1 line(s) affected", "rule 2 delete-line: 0 line(s) affected",
             "rule 3 delete-block: 2 line(s) affected", "rule 4 insert-if-missing: 0 line(s) affected",
             "rule 5 collapse-separators: 1 line(s) affected"]
    expected = [line + (detail if verbose else "") for line, detail in zip(lines, details)]
    assert capsys.readouterr().out.splitlines() == [*expected, f"wrote {out}"]
    assert out.read_text() == "hostname lab-leaf1\n!\nend\n"


@pytest.mark.parametrize("rules, code, message", [
    (VERBOSE_RULES, 0, "rules.yml: 5 rule(s) OK"),
    ("- {op: delete-line, match: '(', regex: true}", 1, "rule 1 (delete-line): invalid regex"),
])
def test_check_validates_rules_only(tmp_path, capsys, monkeypatch, rules, code, message):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rules.yml").write_text(rules)

    assert fixup.main(["--check", "rules.yml"]) == code

    assert message in "".join(capsys.readouterr())
    assert [p.name for p in tmp_path.iterdir()] == ["rules.yml"]


def test_check_rejects_input_and_output_arguments(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        fixup.main(["--check", "rules.yml", "in.cfg", "--output", str(tmp_path / "out.cfg")])

    assert exc.value.code == 2
    assert "--check takes only a rule file" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
