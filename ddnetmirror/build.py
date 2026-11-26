"""ddn build: generate the lab described by ./ddnetmirror.yml.

Reads the project file and runs the same collection, fixup and topology path as
`ddn fetch --tag ... --output-dir . --lldp --lab-name NAME` (plus --force with
ddn build --force). Loading the project file runs nothing. Deploying the lab is
left to the operator and Containerlab.
"""

import argparse
import os
import sys

import yaml

from ddnetmirror import fetch_config

PROJECT_FILE = "ddnetmirror.yml"
REQUIRED = ("server", "name", "tag")
OPTIONAL = ("cacert", "fixup-rules", "image", "exclude-hosts")


class ProjectError(Exception):
    """A problem with the project file or the environment, reported as one line."""


def load_project(directory="."):
    """Read and validate directory/ddnetmirror.yml; return a dict of strings.

    A leading ~ in cacert and fixup-rules is expanded; relative paths are
    relative to directory. Only
    reads the file: no Docker or CloudVision access.
    """
    path = os.path.join(directory, PROJECT_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
    except FileNotFoundError:
        raise ProjectError(f"no {path}: run ddn in a lab project directory "
                           f"that contains {PROJECT_FILE}") from None
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from None
    if not isinstance(spec, dict):
        raise ProjectError(f"{path}: must be a mapping of {', '.join(REQUIRED + OPTIONAL)}")
    unknown = sorted(map(str, spec.keys() - set(REQUIRED + OPTIONAL)))
    if unknown:
        raise ProjectError(f"{path}: unknown key {', '.join(unknown)} "
                           f"(allowed: {', '.join(REQUIRED + OPTIONAL)}; no tokens here)")
    missing = [key for key in REQUIRED if key not in spec]
    if missing:
        raise ProjectError(f"{path}: missing {', '.join(missing)}")
    for key, value in spec.items():
        if key == "exclude-hosts":
            if not isinstance(value, list) or not all(
                    isinstance(h, str) and h.strip() for h in value):
                raise ProjectError(f"{path}: exclude-hosts must be a list of hostnames")
        elif not isinstance(value, str) or not value.strip():
            raise ProjectError(f"{path}: {key} must be a non-empty string")
    try:
        fetch_config.parse_lab_name(spec["name"])
        fetch_config.parse_tag(spec["tag"])
    except argparse.ArgumentTypeError as exc:
        raise ProjectError(f"{path}: {exc}") from None
    if spec.get("image") == "auto":
        raise ProjectError(f"{path}: image: auto is not needed; leave image out to pick "
                           "local ceos images by EOS version")
    project = dict(spec)
    for key in ("cacert", "fixup-rules"):
        if key in project and project[key].startswith("~"):
            project[key] = os.path.expanduser(project[key])
    if "cacert" in project:
        project["cacert"] = os.path.join(directory, project["cacert"])  # absolute stays
    return project


def fetch_args(project, directory=".", token_file=None, force=False):
    """ddn fetch arguments for the project, generating into directory.
    token_file and force come from the ddn build command line, never from the
    project file.

    Every value is attached as --option=value, so a value starting with "-"
    stays a value instead of being parsed as an option."""
    args = [f"--server={project['server']}", f"--tag={project['tag']}",
            f"--output-dir={directory}", "--lldp", f"--lab-name={project['name']}"]
    for key, option in (("cacert", "--ca-file"), ("image", "--image"),
                        ("fixup-rules", "--fixup-rules")):
        if key in project:
            args.append(f"{option}={project[key]}")
    args += [f"--exclude-host={h}" for h in project.get("exclude-hosts", [])]
    if token_file is not None:
        args.append(f"--token-file={token_file}")
    if force:
        args.append("--force")
    return args


def build_parser(prog=None):
    parser = argparse.ArgumentParser(
        prog=prog,
        description=f"Generate the lab described by ./{PROJECT_FILE}: collect configs and "
        "LLDP from CloudVision, apply fixup rules and write configs/ and NAME.clab.yml here, "
        "as ddn fetch --tag TAG --output-dir . --lldp --lab-name NAME. Existing generated "
        "files are refused unless --force. Deploys nothing: when generation is complete, it "
        "prints the 'containerlab deploy' command to run yourself. "
        f"{PROJECT_FILE} keys: server, name, tag (KEY=VALUE, a bare KEY or an expression, "
        "as for ddn fetch --tag); optional cacert, "
        "fixup-rules (a path or none), image, exclude-hosts (a list of hostnames, as for "
        "ddn fetch --exclude-host). The token is read as for ddn fetch: "
        "--token-file, else CLOUDVISION_TOKEN, else the default token file.",
    )
    fetch_config.add_token_file_argument(parser)
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace existing generated files (original and lab configs, topology) as "
        "ddn fetch --force does; no backups, other files untouched. Rebuild only when that is "
        "safe for your lab",
    )
    return parser


def main(argv=None, prog=None):
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    if args.token_file is not None and not args.token_file.strip():
        parser.error("--token-file must not be empty")
    directory = "."
    try:
        project = load_project(directory)
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    try:
        return fetch_config.main(fetch_args(project, directory, args.token_file, args.force),
                                 prog=prog, base_dir=directory)
    except SystemExit as exc:  # usage error from the generated arguments
        return exc.code if isinstance(exc.code, int) else 2


if __name__ == "__main__":
    sys.exit(main())
