"""ddn up: build and deploy the lab described by ./ddnetmirror.yml.
ddn down: destroy that lab with containerlab destroy.

Reads the project file, refuses if the lab already has containers, then runs
the same collection, fixup and topology path as `ddn fetch --tag ...
--output-dir . --lldp --lab-name NAME --force`, and only if that completed,
`containerlab deploy -t NAME.clab.yml`. Loading the project file runs nothing.

ddn down runs `containerlab destroy -t NAME.clab.yml --cleanup` for the same
project, after checking that the topology file is the project's own. It never
removes the project file, topology or configs.
"""

import argparse
import os
import shutil
import stat
import subprocess
import sys

import yaml

from ddnetmirror import fetch_config

PROJECT_FILE = "ddnetmirror.yml"
REQUIRED = ("server", "name", "tag")
OPTIONAL = ("cacert", "fixup-rules", "image", "exclude-hosts")
DOCKER_TIMEOUT = 30  # seconds


class ProjectError(Exception):
    """A problem with the project file or the environment, reported as one line."""


def load_project(directory="."):
    """Read and validate directory/ddnetmirror.yml; return a dict of strings.

    Relative cacert and fixup-rules paths are relative to directory. Only
    reads the file: no Docker, Containerlab or CloudVision access.
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
    if "cacert" in project:
        project["cacert"] = os.path.join(directory, project["cacert"])  # absolute stays
    return project


def fetch_args(project, directory=".", token_file=None):
    """ddn fetch arguments for the project: regenerate in place with --force.
    token_file comes from the ddn up command line, never from the project file.

    Every value is attached as --option=value, so a value starting with "-"
    stays a value instead of being parsed as an option."""
    args = [f"--server={project['server']}", f"--tag={project['tag']}",
            f"--output-dir={directory}", "--lldp", f"--lab-name={project['name']}", "--force"]
    for key, option in (("cacert", "--ca-file"), ("image", "--image"),
                        ("fixup-rules", "--fixup-rules")):
        if key in project:
            args.append(f"{option}={project[key]}")
    args += [f"--exclude-host={h}" for h in project.get("exclude-hosts", [])]
    if token_file is not None:
        args.append(f"--token-file={token_file}")
    return args


def containerlab_command(*args):
    """containerlab as the current user if it can work as such (root, or the
    setuid-root install for clab_admins), else through sudo."""
    exe = shutil.which("containerlab")
    if exe is None:
        raise ProjectError("containerlab was not found on PATH (https://containerlab.dev)")
    info = os.stat(exe)
    as_user = os.geteuid() == 0 or (info.st_mode & stat.S_ISUID and info.st_uid == 0)
    return ([] if as_user else ["sudo"]) + [exe, *args]


def lab_containers(name):
    """Names of existing containers (running or stopped) of Containerlab lab name."""
    command = ["docker", "ps", "-a", "--filter", f"label=containerlab={name}",
               "--format", "{{.Names}}"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectError(f"cannot check for an existing lab {name!r}: {exc}") from None
    if result.returncode != 0:
        detail = result.stderr.strip() or f"exit status {result.returncode}"
        raise ProjectError(f"cannot check for an existing lab {name!r}: 'docker ps' failed: {detail}")
    return sorted(result.stdout.split())


def build_parser(prog=None):
    parser = argparse.ArgumentParser(
        prog=prog,
        description=f"Build and deploy the lab described by ./{PROJECT_FILE}: refuse if the "
        "lab already has containers, collect configs and LLDP from CloudVision, apply fixup "
        "rules, regenerate the project's files (as ddn fetch --force), and only if that "
        "completed, run 'containerlab deploy' (through sudo unless containerlab runs as you). "
        f"{PROJECT_FILE} keys: server, name, tag (KEY=VALUE, a bare KEY or an expression, "
        "as for ddn fetch --tag); optional cacert, "
        "fixup-rules (a path or none), image, exclude-hosts (a list of hostnames, as for "
        "ddn fetch --exclude-host). The token is read as for ddn fetch: "
        "--token-file, else CLOUDVISION_TOKEN, else the default token file.",
    )
    fetch_config.add_token_file_argument(parser)
    return parser


def main(argv=None, prog=None):
    parser = build_parser(prog)
    args = parser.parse_args(argv)
    if args.token_file is not None and not args.token_file.strip():
        parser.error("--token-file must not be empty")
    directory = "."
    try:
        project = load_project(directory)
        name = project["name"]
        topology = fetch_config.topology_path(directory, name)
        # resolved once: the messages below reuse it and never look up containerlab again
        clab = containerlab_command()
        existing = lab_containers(name)
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    deploy, inspect = (" ".join(clab + [action, "-t", topology]) for action in ("deploy", "inspect"))
    if existing:
        print(f"error: lab {name!r} already has containers ({', '.join(existing)}); not "
              f"regenerating or deploying. Remove it first if you mean to: "
              "ddn down", file=sys.stderr)
        return 1

    try:
        code = fetch_config.main(fetch_args(project, directory, args.token_file), prog=prog,
                                 base_dir=directory)
    except SystemExit as exc:  # usage error from the generated arguments
        code = exc.code if isinstance(exc.code, int) else 2
    if code != 0:
        print("error: not deploying: collection or topology generation did not complete "
              "(see above)", file=sys.stderr)
        return code

    print(f"deploying: {deploy}", flush=True)
    try:
        code = subprocess.run(clab + ["deploy", "-t", topology]).returncode  # output unchanged
    except KeyboardInterrupt:
        code = 130
    except OSError as exc:  # sudo or containerlab could not be started
        print(f"error: could not run {clab[0]}: {exc.strerror or exc}; nothing was deployed "
              f"or torn down. Check with: {inspect}; remove a partial lab with: ddn down",
              file=sys.stderr)
        return 1
    if code != 0:
        print(f"error: containerlab deploy failed (exit {code}); nothing was torn down. "
              f"Inspect: {inspect}; remove: ddn down",
              file=sys.stderr)
    return code


def check_topology(path, name):
    """Refuse unless path is a regular file (not a symlink) whose lab name is name,
    so containerlab destroy can only target this project's lab."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise ProjectError(f"no {path}: nothing to destroy (ddn up writes it)") from None
    except OSError as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from None
    if not stat.S_ISREG(info.st_mode):
        raise ProjectError(f"{path} is not a regular file; not destroying")
    try:
        with open(path, encoding="utf-8") as f:
            spec = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ProjectError(f"cannot read {path}: {exc}") from None
    found = spec.get("name") if isinstance(spec, dict) else None
    if found != name:
        raise ProjectError(f"{path} is for lab {found!r}, not {name!r} from {PROJECT_FILE}; "
                           "not destroying")


def build_down_parser(prog=None):
    return argparse.ArgumentParser(
        prog=prog,
        description=f"Destroy the lab described by ./{PROJECT_FILE}: run 'containerlab destroy "
        "-t ./NAME.clab.yml --cleanup' (through sudo unless containerlab runs as you) and exit "
        "with its status. Refuses unless NAME.clab.yml is a regular file for lab NAME. "
        "--cleanup also deletes Containerlab's clab-NAME directory, including files saved "
        f"inside the running lab (e.g. by write memory); {PROJECT_FILE}, the topology and "
        "the collected and startup configs are kept. No CloudVision access.",
    )


def down_main(argv=None, prog=None):
    build_down_parser(prog).parse_args(argv)
    directory = "."
    try:
        name = load_project(directory)["name"]
        topology = fetch_config.topology_path(directory, name)
        check_topology(topology, name)
        command = containerlab_command("destroy", "-t", topology, "--cleanup")
    except ProjectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"destroying: {' '.join(command)}", flush=True)
    try:
        code = subprocess.run(command).returncode  # output unchanged
    except KeyboardInterrupt:
        return 130
    except OSError as exc:  # sudo or containerlab could not be started
        print(f"error: could not run {command[0]}: {exc.strerror or exc}", file=sys.stderr)
        return 1
    if code != 0:
        print(f"error: containerlab destroy failed (exit {code})", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
