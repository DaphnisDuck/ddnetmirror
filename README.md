# ddnetmirror

Build a Containerlab lab of a selected part of an Arista network, from what CloudVision (CVP or CVaaS) already knows about it.

ddnetmirror:
1. selects devices by CloudVision tag;
2. saves their running configs **unchanged**;
3. finds the links between them from LLDP;
4. optionally applies your own text fixup rules to *copies* of the configs;
5. writes a standard Containerlab topology of cEOS nodes.

After collection, everything is local files: you can inspect them, edit them, keep them in a private git repository, and deploy offline.

It is a configuration-preserving copier, a text-fixup tool and a topology generator. **It is not an EOS interpreter.** It never parses, validates or "repairs" EOS configuration. Anything a lab needs changed is expressed in rules that you write and control.

## Status

Early software, version 0.0.1. The full workflow passed a human-run, offline, synthetic four-node Containerlab test (cEOS 4.35.7.2M, Containerlab 0.83.0; #22). It has **not** yet been qualified against live CloudVision (CVP or CVaaS; #7), so expect rough edges and try it on a non-production selection first.

## Handle generated files with care

Saved running configs are real device configs. They can contain hashed credentials, keys and other secrets. ddnetmirror creates them with mode 0600. Keep them out of public repositories, issues and pull requests, and use synthetic data when reporting a bug.

## Prerequisites

- **Linux with Python ≥ 3.10.**
- **A CloudVision service-account token**, from the `CLOUDVISION_TOKEN` environment variable or an optional [token file](#token-file). It is never accepted as an argument and never logged.
- **To run the lab:** [Containerlab](https://containerlab.dev) and a cEOS image that you obtain and import yourself (for example `ceos:4.35.7.2M`). ddnetmirror does not download images.

### Token file

Instead of exporting `CLOUDVISION_TOKEN` in every shell, you can put the token alone in `$XDG_CONFIG_HOME/ddnetmirror/token` (default `~/.config/ddnetmirror/token`). A final newline and surrounding whitespace are ignored. The token itself must be one line of printable ASCII; a token with inner spaces or control characters is refused (from the variable too).

```sh
install -d -m 700 ~/.config/ddnetmirror
f=~/.config/ddnetmirror/token; touch "$f" && chmod 600 "$f" && cat > "$f"   # paste the token, then Ctrl-D
```

- **Precedence:** `CLOUDVISION_TOKEN` wins when it is set and not empty; the file is then not read. With neither, the command stops with an error naming both.
- **Read-only:** ddnetmirror only reads the file. It never creates it or saves a token anywhere.
- **Refused files.** ddnetmirror refuses the token file, without showing its content, if it is:
  - a symlink or not a regular file;
  - not owned by you;
  - readable or writable by group or others;
  - in a directory that group or others can write to.

  Mode 0600 for the file and 0700 for the directory are recommended.
- **No encryption.** File permissions are not encrypted storage: anyone with your account, or root, can read the token. Prefer a scoped, read-only, short-lived service-account token.

## Install

```sh
git clone https://github.com/DaphnisDuck/ddnetmirror.git && cd ddnetmirror
python3 -m venv .venv && .venv/bin/pip install .     # or -e ".[test]" for development
.venv/bin/ddn --help
```

This installs one command, `ddn`, with two subcommands:

| Command | Purpose |
|---|---|
| `ddn fetch` | Query CloudVision: running configs, EOS versions, LLDP, and optionally a Containerlab topology. |
| `ddn fixup` | Apply fixup rules to a copy of one config file. |

`ddn COMMAND --help` lists each command's options. Man pages and tldr-style examples are in [`man/`](man/). The older names `ddnetmirror-fetch-config` and `ddnetmirror-fixup` still work as aliases.

## From CloudVision to Containerlab

```sh
export CLOUDVISION_TOKEN=...          # service-account token
.venv/bin/ddn fetch --server www.arista.io --tag site=lab \
    --output-dir lab1 --lldp \
    --lab-name lab1 --image ceos:4.35.7.2M \
    --fixup-rules rules.yml
```

`--server` takes `host[:port]` (the default port is 443). For on-prem CVP with a private CA, add `--ca-file ca.pem`.

What this command does:
- Checks `rules.yml` first. A bad rule file stops the run before anything is fetched or written.
- Finds every device carrying the tag `site=lab` and saves each running config.
- Reports the LLDP links between the selected devices.
- Writes transformed copies of the configs and a topology that uses them:

```
lab1/
├── collected/   leaf1.cfg  leaf2.cfg  spine1.cfg  spine2.cfg   # exactly as CloudVision returned them
├── startup/     leaf1.cfg  leaf2.cfg  spine1.cfg  spine2.cfg   # copies with rules.yml applied
└── lab1.clab.yml
```

All files are created with mode 0600. Typical output:

```
wrote running config for leaf1 (SN-LEAF1, EOS 4.34.2F) to lab1/collected/leaf1.cfg
wrote startup config for leaf1 with fixups to lab1/startup/leaf1.cfg
...
collected 4 of 4 devices with tag site=lab into lab1/collected; 0 failed
wrote 4 of 4 startup configs with fixups into lab1/startup; 0 failed
LLDP links between selected devices: 5
leaf1 Ethernet1 <-> spine1 Ethernet1 [reciprocal]
leaf1 Ethernet49/1 <-> leaf2 Ethernet49/1 [reciprocal]
leaf2 Ethernet2 <-> spine2 Ethernet2 [one-sided]
...
wrote Containerlab topology lab1/lab1.clab.yml (4 nodes, 5 links)
```

`lab1/lab1.clab.yml`:

```yaml
# Generated by ddnetmirror 0.0.1 at 2026-11-19T06:10:00Z (generation time, UTC).
# 4 of 4 devices with a fixed-up startup config; 5 links.
name: "lab1"
topology:
  defaults:  # every node inherits these; override per node, e.g. "leaf1": {image: "..."}
    kind: ceos
    image: "ceos:4.35.7.2M"
    startup-config: "startup/__clabNodeName__.cfg"
  nodes:
    "leaf1": {}
    "leaf2": {}
    "spine1": {}
    "spine2": {}
  links:
    - endpoints: ["leaf1:eth1", "spine1:eth1"]
    - endpoints: ["leaf1:eth2", "spine2:eth1"]
    - endpoints: ["leaf1:eth49_1", "leaf2:eth49_1"]
    - endpoints: ["leaf2:eth1", "spine1:eth2"]
    # one-sided: LLDP reported this link from one end only
    - endpoints: ["leaf2:eth2", "spine2:eth2"]
```

How the topology is built:
- **Node names** are the inventory hostnames, used as-is.
- **Startup configs:** Containerlab replaces `__clabNodeName__` with the node name and resolves the path relative to the topology file. Each node therefore boots from `startup/<hostname>.cfg`.
- **Interface names** are converted mechanically: `Ethernet1` → `eth1`, `Ethernet49/1` → `eth49_1`.
- **The timestamp** is the time the file was written, not when CloudVision observed the devices.

Deploy and destroy the lab yourself:

```sh
sudo containerlab deploy  -t lab1/lab1.clab.yml
sudo docker exec -it clab-lab1-leaf1 Cli          # log in to a node
sudo containerlab destroy -t lab1/lab1.clab.yml --cleanup
```

ddnetmirror generates lab files. It does not deploy, start, stop or otherwise manage running labs. That is Containerlab's job, run by you with the commands above.

Smaller steps of the same command, each leaving out more options:
- **Topology without fixups:** omit `--fixup-rules`. The configs go directly in `lab1/<hostname>.cfg`, and the topology uses `startup-config: "__clabNodeName__.cfg"`.
- **LLDP report only:** omit `--fixup-rules`, `--lab-name` and `--image`. The configs are collected and the links are printed; no topology is written.
- **Collection only:** additionally omit `--lldp`. `--output-dir` only collects the configs.

### Pick images by EOS version: `--image auto`

Instead of one image for every node, `--image auto --fallback-image REPOSITORY:TAG` picks a local image per device from the EOS version that CloudVision inventory reports:

```sh
.venv/bin/ddn fetch --server www.arista.io --tag site=lab --output-dir lab1 --lldp \
    --lab-name lab1 --image auto --fallback-image ceos:4.33.11.2M
```

```
image for leaf1 (EOS 4.34.2F): ceos:4.33.11.2M (fallback: no local ceos image in the 4.34 train)
image for leaf2 (EOS unknown): ceos:4.33.11.2M (fallback: EOS version unknown)
image for spine1 (EOS 4.35.7.2M): ceos:4.35.7.2M (exact match)
image for spine2 (EOS 4.35.7.2M): ceos:4.35.7.2M (exact match)
```

How it works:
- **Local images only.** It runs `docker image ls REPOSITORY` once, before anything is fetched, and looks only at the tags of the fallback's repository (here `ceos`). It never pulls, builds or queries a registry. The fallback image must exist locally. If Docker is missing, not accessible (for example, you are not in the `docker` group), or the fallback is absent, the command stops with an error before contacting CloudVision.
- **Matching, per device:**
  1. a tag equal to the EOS version (exact match);
  2. otherwise the closest tag in the same `MAJOR.MINOR` train: the smallest difference in the third number, then in the fourth. Ties go to the tag with the same letter suffix (`F`/`M`), then to the newer version;
  3. otherwise (no image in that train, or an unknown or unusual version such as `4.32.1F-INT`) the fallback. It never picks an image from another train.
- Tags that aren't versions (e.g. `latest`) are ignored as candidates and reported.
- **In the topology,** the fallback is `defaults.image`. Only nodes that get a different image have `image:` set on the node. The header notes that images were picked automatically.
- **This is version matching, not a compatibility check.** A matching image does not mean the config runs cleanly on it, and the `F`/`M` suffix is only used to break ties.

Plain `--image IMAGE` is unchanged and never runs Docker.

### Regenerate a lab in place: `--force`

By default nothing is ever overwritten, so the usual way to refresh a lab is a new output directory. That also keeps the previous generation. To regenerate into the same directory instead, add `--force` (only with `--tag … --output-dir`):

```sh
.venv/bin/ddn fetch --server www.arista.io --tag site=lab --output-dir lab1 --lldp \
    --lab-name lab1 --image ceos:4.35.7.2M --fixup-rules rules.yml --force
```

- It replaces only the files this command generates: `collected/<hostname>.cfg` (or `<hostname>.cfg` without fixups), `startup/<hostname>.cfg` and `lab1.clab.yml`. **This includes files you edited by hand at those paths.**
- It makes **no backups**. To keep the previous lab, use a new output directory instead.
- Each file is written to a temporary file in the same directory and then renamed over the old one, so a failed write leaves the previous complete file in place.
- Unrelated files are never touched. Files of devices that are no longer selected (stale) are left as they are, but they are not in the new topology. The topology lists only devices that succeeded in this run.
- A device that fails in this run is reported as failed and left out of the topology, which is marked `INCOMPLETE` as usual. A file whose write fails keeps its previous version. Files of that device that were already replaced earlier in the run are not restored; there is no rollback. For example, if a device's fixup or startup write fails, its `collected/<hostname>.cfg` is already the new config, while `startup/<hostname>.cfg` stays the old one.
- A symbolic link, directory or other non-regular file at one of these paths is never followed or replaced. It is reported, and you remove it or use a new directory.
- `--force` only writes files. It never touches a running lab: destroy and redeploy it yourself with Containerlab.

## Fetch a single device

```sh
.venv/bin/ddn fetch --server www.arista.io --hostname leaf1                   # writes leaf1.cfg
.venv/bin/ddn fetch --server www.arista.io --tag role=core1 --output core1.cfg  # tag must match exactly one device
```

The device is looked up by inventory hostname, or by a device tag that matches exactly one device. Its running config is written unchanged, and its EOS version is reported (`unknown` if inventory has none).

## Fix up a config by hand

```sh
.venv/bin/ddn fixup lab1/collected/leaf1.cfg --rules rules.yml --output leaf1-lab.cfg
```

This prints one `rule N <op>: <count> line(s) affected` line per rule. It writes a new file and never touches the input. The result is identical to what `--fixup-rules` writes to `startup/`.

`--verbose` adds what each count means, for example `rule 3 delete-block: 3 line(s) affected (1 block(s) removed)`, `rule 9 collapse-separators: 4 line(s) affected (2 separator run(s) collapsed)` or, for a rule that found its match, `insert-if-missing: 0 line(s) affected (match present, nothing inserted)`. With `ddn fetch … --fixup-rules rules.yml --verbose` the same lines are printed for every device, prefixed `fixup <hostname>:`. Neither ever prints config text, matched lines or replacement text.

Check a rule file without touching any config:

```sh
.venv/bin/ddn fixup --check rules.yml     # prints "rules.yml: 9 rule(s) OK", or the first error
```

`--check` runs the same validation as a real run (see below). It reads only the rule file, writes nothing and exits non-zero if the file is invalid.

## Fixup rules

A rule file is a YAML list, and rules run in order, each on the previous rule's output. Every rule has an `op`, and all but `collapse-separators` have a `match`. `match` is a literal substring, or a Python regex when `regex: true`. Matching is per line, without the line ending.

| op | Fields | Effect |
|---|---|---|
| `replace` | `match`, `with` | Replaces matches in every line (regex mode uses `re.sub`, so `\1` works). |
| `delete-line` | `match` | Removes matching lines only; their indented children stay. |
| `delete-block` | `match` | Removes each matching line plus the following more-indented lines. The block ends at the next non-blank line with equal or lesser indentation, such as an unindented `!`, which is kept. |
| `insert-if-missing` | `match`, `text` | If no line matches, appends `text` verbatim at the end of the file. |
| `collapse-separators` | (none) | Merges each run of directly adjacent, unindented lines that are exactly `!` into one. Blank lines end a run; indented `!` lines and `!` comments (e.g. `! Command: show running-config`) are never touched. |

Example (synthetic):

```yaml
# mark every interface description as lab
- {op: replace, match: '^(   description )', with: '\1[lab] ', regex: true}

# drop production-only management lines
- {op: delete-line, match: '^(ntp server vrf MGMT|logging vrf MGMT host|ip route vrf MGMT) ', regex: true}

# drop CloudVision streaming, gNMI and the hardware management port
- {op: delete-block, match: daemon TerminAttr}
- {op: delete-block, match: '^management api gnmi$', regex: true}
- {op: delete-block, match: '^interface Management1$', regex: true}

# add a cEOS management stanza, keeping "end" as the last line
- {op: delete-line, match: '^end$', regex: true}
- op: insert-if-missing
  match: '^interface Management0$'
  regex: true
  text: |
    interface Management0
       description [lab] containerlab management
    !
- {op: insert-if-missing, match: '^end$', regex: true, text: "end\n"}

# tidy the "!" runs the deleted blocks left behind
- op: collapse-separators
```

Rules are plain text editing:
- Untouched text and line endings are kept byte for byte.
- ddnetmirror does no automatic `!` separator normalisation. Your rules still change separators where they say so: inserted text can add `!` lines, deleting blocks can remove `!` lines or leave two in a row, and `collapse-separators` (only if you list it) merges such runs.
- `insert-if-missing` first ends an unterminated last line (using the file's line ending), then appends the fragment verbatim.
- A rule file is fully validated before use: unknown ops or fields, bad regexes and bad `\N` references are all rejected.
- Every rule applies to every device. Rename hostnames with a `replace` rule if you want to; ddnetmirror never does it on its own.

## When things go wrong

- **Never overwrites** (unless you pass `--force`, see [above](#regenerate-a-lab-in-place---force)). An existing config, startup file or topology file is refused and reported. Nothing is renamed.
- **Nothing half-written.** A failed retrieval or write leaves no partial file.
- **Partial success.** With `--output-dir`, one device failing (retrieval, unsafe or duplicate hostname, fixup) does not stop the others:
  - each failure is reported as `failed: <hostname> (<serial>): <reason>`, or with only the device ID when the device can't be looked up, e.g. `failed: SN-LEAF1: no device with device_id 'SN-LEAF1' (tag site=lab) in inventory`;
  - a device whose fixup fails keeps its original config but gets no startup file and no node;
  - the topology contains only the devices that have configs, and links between them;
  - the topology is marked `# INCOMPLETE: …` in its header.
- **Refused topologies.** No topology is written when:
  - LLDP data is unavailable for every device;
  - no config was saved;
  - a hostname is not a valid Containerlab node name;
  - a hostname is reserved (`host`, `mgmt-net`, `macvlan`).
- **Exit status.** 0 only if every selected device was collected (and fixed up and placed in the topology, when requested). Otherwise 1, and 2 for command-line usage errors.

## Known limitations

- **Live CVP/CVaaS compatibility is unverified** (#7). The CloudVision calls follow the `cloudvision` Python library's source and examples.
  - The code is tested against stubbed responses (`pytest`).
  - It was also tested in a human-run synthetic test drive (#22): Containerlab 0.83.0 with `ceos:4.35.7.2M`, four nodes, BGP and ECMP up, and the slash-form link `Ethernet49/1` ↔ `eth49_1` passing traffic.
- **Image choice is by version string only.** `--image IMAGE` applies to every node. `--image auto` matches local tags to inventory versions (see above) but knows nothing about platforms or compatibility. You can always override a node in the YAML by hand.
- **LLDP matching is conservative.** A neighbour becomes a link only if:
  - its LLDP system name exactly matches the hostname of one selected device;
  - its port ID is an LLDP interface that device itself reports.

  Links seen from only one end are accepted and marked `one-sided`. Interfaces that appear in more than one link are skipped. Every skip is reported as `skipped LLDP: …`.
- **Not converted:** links on interfaces that don't map to a cEOS `ethN[_N…]` name (anything other than `EthernetN[/N[/N]]`) are left out and reported.
- **Configs are copied, not adapted.** Whether a production config boots cleanly on cEOS depends on your fixup rules.

## Development

```sh
.venv/bin/pip install -e ".[test]" && .venv/bin/pytest
```

## License and trademarks

Licensed under the [Apache License 2.0](LICENSE).

ddnetmirror is an independent project, not affiliated with or endorsed by Arista Networks. Arista, CloudVision, EOS and cEOS are trademarks of Arista Networks. ddnetmirror does not download or redistribute cEOS images.
