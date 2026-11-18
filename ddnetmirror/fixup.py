"""Apply operator-defined text fixup rules to a copy of a config file.

This is a line-based text transformer, not an EOS parser. Rules run in order,
each on the previous rule's output; the input file is never modified.
"""

import argparse
import re
import sys

import yaml

from ddnetmirror.fetch_config import FetchError, write_new_file

# op -> (required fields, optional fields)
OPS = {
    "replace": ({"match", "with"}, {"regex"}),
    "delete-line": ({"match"}, {"regex"}),
    "delete-block": ({"match"}, {"regex"}),
    "insert-if-missing": ({"match", "text"}, {"regex"}),
}


class RuleError(Exception):
    pass


class Rule:
    def __init__(self, number, spec):
        where = f"rule {number}"
        if not isinstance(spec, dict):
            raise RuleError(f"{where}: must be a mapping")
        self.op = spec.get("op")
        if not isinstance(self.op, str) or self.op not in OPS:
            raise RuleError(f"{where}: unknown op {self.op!r} (expected one of {', '.join(OPS)})")
        required, optional = OPS[self.op]
        missing = required - spec.keys()
        if missing:
            raise RuleError(f"{where} ({self.op}): missing {', '.join(sorted(missing))}")
        unknown = spec.keys() - required - optional - {"op"}
        if unknown:
            raise RuleError(
                f"{where} ({self.op}): unknown field {', '.join(sorted(map(str, unknown)))}"
            )
        for field in required:
            if not isinstance(spec[field], str) or (not spec[field] and field != "with"):
                raise RuleError(f"{where} ({self.op}): {field} must be a non-empty string")
        self.regex = spec.get("regex", False)
        if not isinstance(self.regex, bool):
            raise RuleError(f"{where} ({self.op}): regex must be true or false")
        self.match = spec["match"]
        self.replacement = spec.get("with")
        self.text = spec.get("text")
        self.name = f"{where} {self.op}"
        if self.regex:
            try:
                self.pattern = re.compile(self.match)
            except re.error as exc:
                raise RuleError(f"{where} ({self.op}): invalid regex {self.match!r}: {exc}") from None
            if self.op == "replace":
                try:
                    check_template(self.pattern, self.replacement)
                except (re.error, IndexError) as exc:
                    raise RuleError(
                        f"{where} ({self.op}): invalid replacement {self.replacement!r}: {exc}"
                    ) from None

    def matches(self, content):
        if self.regex:
            return self.pattern.search(content) is not None
        return self.match in content


def check_template(pattern, template):
    """Expand template against a stand-in match with the same groups as pattern,
    so bad group references or escapes fail even if the input never matches."""
    names = {index: name for name, index in pattern.groupindex.items()}
    groups = "".join(
        f"(?P<{names[i]}>)" if i in names else "()" for i in range(1, pattern.groups + 1)
    )
    re.compile(groups).match("").expand(template)


def load_rules(text):
    try:
        specs = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise RuleError(f"not valid YAML: {exc}") from None
    if not isinstance(specs, list) or not specs:
        raise RuleError("must be a non-empty YAML list of rules")
    return [Rule(i, spec) for i, spec in enumerate(specs, 1)]


def split_lines(text):
    """Split into [content, ending] pairs; ending is "\\n", "\\r\\n" or "" (last line)."""
    lines = []
    for part in text.split("\n"):
        lines.append([part, "\n"])
    last = lines.pop()
    for line in lines:
        if line[0].endswith("\r"):
            line[0], line[1] = line[0][:-1], "\r\n"
    if last[0]:
        lines.append([last[0], ""])
    return lines


def indent(content):
    return len(content) - len(content.lstrip(" \t"))


def delete_blocks(rule, lines):
    """Remove each matching line and its more-indented descendants."""
    kept, removed, i = [], 0, 0
    while i < len(lines):
        content = lines[i][0]
        if not rule.matches(content):
            kept.append(lines[i])
            i += 1
            continue
        level, end = indent(content), i
        for j in range(i + 1, len(lines)):
            child = lines[j][0]
            if not child.strip():
                continue  # blank: decided by what follows
            if indent(child) <= level:
                break  # equal/lesser indentation, incl. an unindented "!", ends the block
            end = j
        removed += end - i + 1
        i = end + 1  # trailing blank lines after the last descendant are kept
    return kept, removed


def apply_rule(rule, lines):
    """Return (new lines, count of affected lines)."""
    if rule.op == "replace":
        changed = 0
        for line in lines:
            if rule.regex:
                new = rule.pattern.sub(rule.replacement, line[0])
            else:
                new = line[0].replace(rule.match, rule.replacement)
            if new != line[0]:
                line[0] = new
                changed += 1
        return lines, changed
    if rule.op == "delete-line":
        kept = [line for line in lines if not rule.matches(line[0])]
        return kept, len(lines) - len(kept)
    if rule.op == "delete-block":
        return delete_blocks(rule, lines)
    # insert-if-missing
    if any(rule.matches(line[0]) for line in lines):
        return lines, 0
    if lines and not lines[-1][1]:
        lines[-1][1] = lines[0][1] if lines[0][1] else "\n"  # end the last line first
    fragment = split_lines(rule.text)  # verbatim; split only so later rules see lines
    lines.extend(fragment)
    return lines, len(fragment)


def apply_rules(rules, text, report=print):
    lines = split_lines(text)
    for rule in rules:
        lines, count = apply_rule(rule, lines)
        # a replacement may contain line breaks: re-split so the next rule sees
        # physical lines (every byte and ending kept as-is)
        lines = split_lines("".join(content + ending for content, ending in lines))
        report(f"{rule.name}: {count} line(s) affected")
    return "".join(content + ending for content, ending in lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply text fixup rules to a copy of a config file. "
        "The input is never modified; the output is a new file."
    )
    parser.add_argument("input", help="config file to read (UTF-8)")
    parser.add_argument("--rules", required=True, help="YAML rule file")
    parser.add_argument("--output", required=True, help="new output file; never overwritten")
    args = parser.parse_args(argv)
    try:
        with open(args.rules, encoding="utf-8") as f:
            rules = load_rules(f.read())
        with open(args.input, "rb") as f:
            text = f.read().decode("utf-8")
        result = apply_rules(rules, text)
        write_new_file(args.output, result)
    except RuleError as exc:
        print(f"error: {args.rules}: {exc}", file=sys.stderr)
        return 1
    except (FetchError, OSError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
