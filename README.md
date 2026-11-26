# ddnetmirror

Build a Containerlab lab of a selected part of an Arista network, from what CloudVision (CVP or CVaaS) already knows about it.

ddnetmirror:
1. selects devices by CloudVision tag, or by a small [tag expression](#tag-expressions);
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
- **A CloudVision service-account token**, from an optional [token file](#token-file) or the `CLOUDVISION_TOKEN` environment variable. The token value is never accepted as an argument (only a token file's path is) and never logged.
- **To run the lab:** [Containerlab](https://containerlab.dev) and a cEOS image that you obtain and import yourself (for example `ceos:4.35.7.2M`). ddnetmirror does not download images, and never runs Containerlab: you deploy the generated lab yourself, with the privileges your Containerlab installation needs.

### Token file

Instead of exporting `CLOUDVISION_TOKEN` in every shell, you can put the token alone in `$XDG_CONFIG_HOME/ddnetmirror/token` (default `~/.config/ddnetmirror/token`). A final newline and surrounding whitespace are ignored. The token itself must be one line of printable ASCII; a token with inner spaces or control characters is refused (from the variable too).

```sh
install -d -m 700 ~/.config/ddnetmirror
f=~/.config/ddnetmirror/token; touch "$f" && chmod 600 "$f" && cat > "$f"   # paste the token, then Ctrl-D
```

- **Precedence:**
  1. `--token-file PATH` ([below](#one-token-file-per-environment)), when given;
  2. otherwise `CLOUDVISION_TOKEN`, when it is set and not empty;
  3. otherwise the default token file.

  Only the first source that applies is read. With none of them, the command stops with an error naming the variable and the default file.
- **Read-only:** ddnetmirror only reads the file. It never creates it or saves a token anywhere.
- **Refused files.** ddnetmirror refuses the token file, without showing its content, if it is:
  - a symlink or not a regular file;
  - not owned by you;
  - readable or writable by group or others;
  - in a directory that group or others can write to.

  Mode 0600 for the file and 0700 for the directory are recommended.
- **No encryption.** File permissions are not encrypted storage: anyone with your account, or root, can read the token. Prefer a scoped, read-only, short-lived service-account token.

### One token file per environment

With tokens for several CloudVision environments, keep one file per environment and pick it with `--token-file PATH` on `ddn fetch` or `ddn build`:

```sh
install -d -m 700 ~/.config/ddnetmirror/tokens
for env in production staging cvaas; do
    f=~/.config/ddnetmirror/tokens/$env; touch "$f" && chmod 600 "$f"
done
cat > ~/.config/ddnetmirror/tokens/staging     # paste that environment's token, then Ctrl-D

ddn fetch --server cvp-staging.example.net --ca-file ca.pem --hostname leaf1 \
    --token-file ~/.config/ddnetmirror/tokens/staging
ddn fetch --server www.arista.io --tag site=lab --output-dir lab1 --lldp \
    --token-file ~/.config/ddnetmirror/tokens/cvaas
cd ~/labs/fabric && ddn build --token-file ~/.config/ddnetmirror/tokens/production
```

- **The option wins**, even when `CLOUDVISION_TOKEN` is set.
- **No fallback.** If the named file is missing or refused, the command stops with an error; it never falls back to the variable or the default file.
- **Same checks** as the default file (the list above); symlinks are refused here too, so name the real file. `~` is expanded, and a relative path is relative to the current directory.
- **Not in `ddnetmirror.yml`.** `ddn build` takes `--token-file` on its command line only.

## Install

```sh
git clone https://github.com/DaphnisDuck/ddnetmirror.git && cd ddnetmirror
python3 -m venv .venv && .venv/bin/pip install .     # or -e ".[test]" for development
.venv/bin/ddn --help
```

This installs one command, `ddn`, with three subcommands:

| Command | Purpose |
|---|---|
| `ddn fetch` | Query CloudVision: running configs, EOS versions, LLDP, and optionally a Containerlab topology. |
| `ddn fixup` | Apply fixup rules to a copy of one config file. |
| `ddn build` | Generate the lab described by `ddnetmirror.yml` in the current directory ([below](#one-command-ddn-build)). |

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
├── configs/
│   ├── collected/   leaf1.cfg  leaf2.cfg  spine1.cfg  spine2.cfg   # exactly as CloudVision returned them
│   └── startup/     leaf1.cfg  leaf2.cfg  spine1.cfg  spine2.cfg   # copies with rules.yml applied
└── lab1.clab.yml
```

All files are created with mode 0600. Typical output:

```
fixup rules: rules.yml (--fixup-rules)
selected 4 devices with tag site=lab
wrote running config for leaf1 (SN-LEAF1, EOS 4.34.2F) to lab1/configs/collected/leaf1.cfg
wrote startup config for leaf1 with fixups to lab1/configs/startup/leaf1.cfg
...
collected 4 of 4 devices with tag site=lab into lab1/configs/collected; 0 failed
wrote 4 of 4 startup configs with fixups into lab1/configs/startup; 0 failed
LLDP links between selected devices: 5
leaf1 Ethernet1 <-> spine1 Ethernet1 [reciprocal]
leaf1 Ethernet49/1 <-> leaf2 Ethernet49/1 [reciprocal]
leaf2 Ethernet2 <-> spine2 Ethernet2 [one-sided]
...
wrote Containerlab topology lab1/lab1.clab.yml (4 nodes, 5 links)
```

### Only EOS devices are selected

CloudVision inventory can include third-party devices (for example Cisco switches) that have no EOS running config. ddnetmirror only ever selects **eligible EOS devices**: devices that carry CloudVision's **system-generated `eos` tag**, with any value (the value isn't read).

- **Everywhere.** `--hostname`, `--tag` (including the universe of `not`), `--list`, `--exclude-host`, collection, LLDP, the topology and `ddn build` all use only eligible devices. A non-EOS device's config is never fetched, it never becomes a node, and LLDP links to it are skipped as `not in selection`. A Boolean expression can't bring one back in: `not role=spine`, `not eos=x` and `not role` cover eligible EOS devices only, and `not eos` selects nothing (every eligible device has the `eos` tag).
- **Messages.** Tagged devices that aren't eligible are listed once on stderr (`left out (not EOS: no system-generated eos tag): SN-SW1`). A tag that matches only non-EOS devices fails with `no eligible EOS device with tag …`; a `--hostname` of a non-EOS device fails with `… is not an eligible EOS device …`.
- **Operator tags don't count.** An `eos` tag you create yourself confers nothing; you can still use it as an ordinary term in `--tag`.
- **One extra lookup.** Eligibility is one tag query per run. If it fails, the run stops with an error; it never falls back to selecting everything.
- **Limitation.** An EOS device that lacks the system `eos` tag is excluded. This boundary was checked by owner observation on on-prem CVP and on CVaaS (every observed EOS device had the tag, no observed third-party device did); it is not an Arista compatibility guarantee. An unknown EOS version doesn't matter: such a device is still selected.

### Tag expressions

`--tag` takes one `KEY=VALUE`, a bare `KEY`, or a small Boolean expression of them:

```sh
ddn fetch --server www.arista.io --tag '(site=lab or site=dr) and not role=spine' --output-dir lab1 --lldp
```

```text
role=leaf
role=leaf or role=spine
site=lab and role=leaf
site=lab and not role=spine
not role=spine
(site=lab or site=dr) and role=leaf
role
site=lab and not role
site=lab and (role=leaf or not role)
```

- **Operators.** `not`, `and` and `or`, in that order of precedence, with parentheses for grouping: `site=dr or site=lab and role=spine` means `site=dr or (site=lab and role=spine)`. Operators are words in any case (`and`, `AND`, `And`) separated by whitespace or parentheses.
- **Terms** match a CloudVision device tag exactly, including case. `not role=spine` selects every eligible EOS device in inventory without that tag, including devices with no `role` tag at all (see [Only EOS devices are selected](#only-eos-devices-are-selected)).
- **A bare `KEY`** matches devices that have that tag with any value: `role` selects every eligible EOS device with a `role` tag, and `not role` every eligible EOS device without one. The key is matched exactly, including case (`Role` is a different key); there are no wildcards. `eos` is an ordinary key here: `not eos` selects nothing.
- **Plain tags are unchanged.** A `--tag` with `=` and without a standalone `and`, `or` or `not` word in any case (separated by whitespace or parentheses) is one `KEY=VALUE`, as before. Its value may contain spaces or parentheses (`site=New York`). Operator words inside a term are just text (`team=and` is a plain tag).
- **Limitation: standalone operator words are reserved.** A plain tag whose value contains `and`, `or` or `not` in any case as a separate word is read as an expression, and the other words become bare keys: `description=research and development` means `description=research` and any device with a `development` tag, and `desc=a or b` means `desc=a` or any device with a `b` tag. Such a tag value can't be selected with `--tag` for now. If a value might contain these words, run `--list` first to check the selection.
- **Reserved words.** A standalone `and`, `or` or `not` is always an operator, never a bare key: `--tag not` or `--tag AND` is a syntax error. `KEY=VALUE` terms that merely contain them (`and=or`) are unaffected.
- **Inside an expression,** and for a bare `KEY`, a term can't contain whitespace or parentheses; such a tag can't be combined, and the expression is refused with a syntax error. There is no quoting.
- **Validation.** A malformed expression (empty, a dangling operator, unbalanced parentheses, a term with an empty key or value) is a usage error before anything is contacted or written.
- **Selection.** Each device is selected once, in device-ID order, and the count is printed (`selected 12 devices with tag …`). There is no size limit and no confirmation: a broad expression such as `not role=spine` is a valid choice. No match is the same error as for a plain tag. Without `--output-dir` (or `--list`), the expression must still match exactly one device.
- **Check first.** Add `--list` to see which devices an expression selects before collecting; see [Preview a selection](#preview-a-selection---list).

`lab1/lab1.clab.yml`:

```yaml
# Generated by ddnetmirror 0.0.1 at 2026-11-19T06:10:00Z (generation time, UTC).
# 4 of 4 devices with a fixed-up startup config; 5 links.
name: "lab1"
topology:
  defaults:  # every node inherits these; override per node, e.g. "leaf1": {image: "..."}
    kind: ceos
    image: "ceos:4.35.7.2M"
    startup-config: "configs/startup/__clabNodeName__.cfg"
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
- **Startup configs:** Containerlab replaces `__clabNodeName__` with the node name and resolves the path relative to the topology file. Each node therefore boots from `configs/startup/<hostname>.cfg`.
- **Interface names** are converted mechanically: `Ethernet1` → `eth1`, `Ethernet49/1` → `eth49_1`.
- **The timestamp** is the time the file was written, not when CloudVision observed the devices.

When the topology is complete (every selected device made it in), the last lines of output are a ready-to-copy deploy command with the topology's absolute path, shell-quoted:

```text
deploy it with Containerlab, with the privileges your install needs:
  containerlab deploy -t /home/me/lab1/lab1.clab.yml
```

ddnetmirror only prints it. It never runs it and never adds `sudo`: run it with the privileges your Containerlab installation needs (for example with `sudo`, unless your user may run Containerlab directly). No command is printed when the topology is incomplete or not written, with `--list`, or when no topology was requested. Deploy and destroy the lab yourself:

```sh
sudo containerlab deploy  -t lab1/lab1.clab.yml
sudo docker exec -it clab-lab1-leaf1 Cli          # log in to a node
sudo containerlab destroy -t lab1/lab1.clab.yml --cleanup
```

ddnetmirror generates lab files. It does not deploy, start, stop or otherwise manage running labs. That is Containerlab's job.

Smaller steps of the same command, each leaving out more options:
- **Topology without fixups:** omit `--fixup-rules` (and have no [user default rules](#user-default-fixup-rules)), or pass `--fixup-rules none`. No `configs/startup/` is written, and the topology boots the collected configs directly: `startup-config: "configs/collected/__clabNodeName__.cfg"`.
- **LLDP report only:** omit `--fixup-rules`, `--lab-name` and `--image`. The configs are collected and the links are printed; no topology is written.
- **Collection only:** additionally omit `--lldp`. `--output-dir` only collects the configs into `configs/collected/`.

### Preview a selection: `--list`

`--list` selects devices exactly as collection does, prints them and stops. It fetches no configs and writes no files, and `--output-dir` isn't needed:

```sh
ddn fetch --server www.arista.io --tag 'site=lab and not role=spine' --list
```

```text
selected 3 devices with tag site=lab and not role=spine
border1  SN-BORDER1  4.33.1F
leaf1    SN-LEAF1    4.34.2F
leaf2    SN-LEAF2    unknown
```

Rows (hostname, serial, EOS version, sorted by hostname) go to stdout and the summary line to stderr, so the rows can be piped. A device without an inventory hostname shows `-`, one without an EOS version `unknown`; control characters are escaped. `--hostname leaf1 --list` shows the single device a hostname selects. Errors (no match, an ambiguous hostname, a lookup or token failure) are the same as for collection, with exit status 1. `--list` can't be combined with the output, LLDP, lab or fixup options.

### Leave devices out: `--exclude-host`

To drop a few devices from a tag selection without changing CloudVision tags, repeat `--exclude-host HOSTNAME`:

```sh
ddn fetch --server cvp.example.net --tag 'site=lab and not role=border' \
    --exclude-host leaf03 --exclude-host leaf04 --list
```

- **Applied right after selection,** before anything is collected. `--list`, collection, LLDP and the topology all use the reduced selection: an excluded device's config is never fetched, it is no node, and links to it are skipped as `not in selection`. The selected count is the count after exclusion.
- **Matching** is by inventory hostname, exact but ignoring case, as for `--hostname`. There are no wildcards or patterns. Each excluded device is printed (`excluded: leaf03 (SN-LEAF03)`).
- **A hostname that matches no selected device** (a typo, or a device the tag doesn't select) is a `warning:`, not an error.
- **Errors:** a hostname matching several selected devices that differ only in case is refused as ambiguous; excluding every selected device is refused. Nothing is collected or written.
- **Only with `--tag`.** `--hostname` with `--exclude-host` is a usage error.

### Images: picked by EOS version unless you pass `--image`

With `--lab-name` and no `--image`, a local `ceos` image is picked per device from the EOS version that CloudVision inventory reports:

```sh
.venv/bin/ddn fetch --server www.arista.io --tag site=lab --output-dir lab1 --lldp --lab-name lab1
```

```
image for leaf1 (EOS 4.34.2F): ceos:4.35.7.2M (no local image in the 4.34 train; newest in the nearest train 4.35)
warning: leaf1 gets ceos:4.35.7.2M from another release train than its EOS 4.34.2F; version matching does not establish configuration compatibility
image for leaf2 (EOS unknown): ceos:4.35.7.2M (EOS version unknown; newest local image)
image for spine1 (EOS 4.35.7.2M): ceos:4.35.7.2M (exact match)
image for spine2 (EOS 4.33.1F): ceos:4.33.11.2M (newest in the 4.33 train)
```

How it works:
- **Local images only.** It runs `docker image ls ceos` once, before contacting CloudVision, and uses the local `ceos` images whose tag is a version (`MAJOR.MINOR.PATCH[.BUILD][F/M]`, e.g. `ceos:4.35.7.2M`). Other tags such as `latest` are ignored and reported. It never pulls, builds or queries a registry.
- **It stops with an error before contacting CloudVision** if Docker is missing or not accessible (for example, you are not in the `docker` group), or if there is no version-tagged `ceos` image.
- **Matching, per device:**
  1. a tag equal to the EOS version (exact match);
  2. otherwise the newest version in the same `MAJOR.MINOR` train;
  3. otherwise the newest version in the nearest train. When two trains are equally near, the newer one wins. This cross-train pick is printed as a `warning:`;
  4. if the version is unknown or not recognized (e.g. `4.32.1F-INT`), the newest local image.
- **Comparison details:**
  - Versions compare numerically (`4.34.10F` is newer than `4.34.9M`). On equal numbers, `M` counts as newer than `F`.
  - The EOS/EOS64 distinction is ignored: `4.34.2F-EOS64` and `EOS64-4.34.2F` are matched as `4.34.2F`.
- **In the topology,** `defaults.image` is the image most nodes got. On a tie, it is the image of the first such node by name. Only the other nodes have `image:` set on the node. The header notes that images were picked automatically.
- **This is version matching, not a compatibility check.** A matching image does not mean the config runs cleanly on it.

`--image IMAGE` (with `--lab-name`) uses that image for every node and never runs Docker. `--image auto` from earlier versions still works and behaves the same as leaving out `--image`, with a note that it is no longer needed. `--fallback-image` was removed and is refused with an error.

### Regenerate a lab in place: `--force`

By default nothing is ever overwritten, so the usual way to refresh a lab is a new output directory. That also keeps the previous generation. To regenerate into the same directory instead, add `--force` (only with `--tag … --output-dir`):

```sh
.venv/bin/ddn fetch --server www.arista.io --tag site=lab --output-dir lab1 --lldp \
    --lab-name lab1 --image ceos:4.35.7.2M --fixup-rules rules.yml --force
```

- It replaces only the files this command generates: `configs/collected/<hostname>.cfg`, `configs/startup/<hostname>.cfg` (with fixups) and `lab1.clab.yml`. Missing `configs/` subdirectories are created. **This includes files you edited by hand at those paths.**
- It makes **no backups**. To keep the previous lab, use a new output directory instead.
- Each file is written to a temporary file in the same directory and then renamed over the old one, so a failed write leaves the previous complete file in place.
- Unrelated files are never touched, including other files and subdirectories under `configs/`. Files of devices that are no longer selected (stale) are left as they are, but they are not in the new topology. The topology lists only devices that succeeded in this run.
- A device that fails in this run is reported as failed and left out of the topology, which is marked `INCOMPLETE` as usual. A file whose write fails keeps its previous version. Files of that device that were already replaced earlier in the run are not restored; there is no rollback. For example, if a device's fixup or startup write fails, its `configs/collected/<hostname>.cfg` is already the new config, while `configs/startup/<hostname>.cfg` stays the old one.
- A symbolic link, directory or other non-regular file at one of these paths is never followed or replaced. It is reported, and you remove it or use a new directory.
- `--force` only writes files. It never touches a running lab: destroy and redeploy it yourself with Containerlab.

## One command: `ddn build`

A lab project is a directory with a `ddnetmirror.yml`:

```yaml
# ~/labs/fabric/ddnetmirror.yml
server: www.arista.io     # CloudVision host[:port]
name: fabric              # lab name and topology file name: fabric.clab.yml
tag: site=lab             # device tag KEY=VALUE, a bare KEY, or a tag expression ('role=leaf or not role')
# optional:
# cacert: ca.pem          # CA bundle for on-prem CVP (relative to this directory)
# fixup-rules: rules.yml  # relative to this directory, or "none"; default: the user default rules
# image: ceos:4.35.7.2M   # one image for every node; default: picked per device by EOS version
# exclude-hosts:          # hostnames to leave out of the tag selection (as ddn fetch --exclude-host)
#   - leaf03
#   - leaf04
```

```sh
cd ~/labs/fabric && ddn build
```

`ddn build` does this:
1. **Validates the project file.** Unknown keys are refused, and so is a token or token file: the token comes from `--token-file PATH` on the `ddn build` command line, `CLOUDVISION_TOKEN` or the default [token file](#token-file), as for `ddn fetch`. Reading the file runs nothing.
2. **Generates the project's files** in this directory, exactly like `ddn fetch --tag TAG --output-dir . --lldp --lab-name NAME` (plus one `--exclude-host` per `exclude-hosts` entry): `configs/collected/`, `configs/startup/` (with fixup rules) and `NAME.clab.yml`. Other files are untouched.
3. **Prints the deploy command** when the topology is complete, as `ddn fetch` does ([above](#from-cloudvision-to-containerlab)). It deploys nothing; its exit status is that of the generation.

Existing generated files are refused, as with `ddn fetch`. `ddn build --force` regenerates them in place with exactly the rules of [`ddn fetch --force`](#regenerate-a-lab-in-place---force): only the generated files are replaced, without backups (including files you edited by hand at those paths); unrelated files are kept; symlinks and non-regular files are refused. It never looks at or touches a running lab or Containerlab's own `clab-NAME/` directory. Rebuild only when that is safe for your lab, for example after destroying it, and redeploy it yourself.

Running `ddn build` concurrently against the same project directory is unsupported: there is no lock, and overlapping runs can mix generated files. Run one at a time.

**Migrating from `ddn up` / `ddn down`** (removed, no aliases): `ddn up` is now `ddn build` (add `--force` to regenerate an existing project, which `ddn up` always did) followed by the printed `containerlab deploy -t …` command. `ddn down` is now `containerlab destroy -t NAME.clab.yml --cleanup`, run yourself in the project directory with the privileges your Containerlab installation needs. `--cleanup` also deletes Containerlab's `clab-NAME/` directory, including configs saved with `write memory` inside the lab.

## Fetch a single device

```sh
.venv/bin/ddn fetch --server www.arista.io --hostname leaf1                   # writes leaf1.cfg
.venv/bin/ddn fetch --server www.arista.io --tag role=core1 --output core1.cfg  # tag must match exactly one device
```

The device is looked up by inventory hostname, or by a device tag that matches exactly one device. The hostname match ignores case (`leaf1` finds `LEAF1`); files and output use the hostname as CloudVision reports it, and hostnames that differ only in case are refused as ambiguous. Tag keys and values must match exactly, including case. Its running config is written unchanged, and its EOS version is reported (`unknown` if inventory has none).

## Fix up a config by hand

```sh
.venv/bin/ddn fixup lab1/configs/collected/leaf1.cfg --rules rules.yml --output leaf1-lab.cfg
```

This prints one `rule N <op>: <count> line(s) affected` line per rule. It writes a new file and never touches the input. The result is identical to what `--fixup-rules` writes to `configs/startup/`.

`--verbose` adds what each count means, for example `rule 3 delete-block: 3 line(s) affected (1 block(s) removed)`, `rule 9 collapse-separators: 4 line(s) affected (2 separator run(s) collapsed)` or, for a rule that found its match, `insert-if-missing: 0 line(s) affected (match present, nothing inserted)`. With `ddn fetch … --lab-name lab1 --verbose` and fixup rules in use, the same lines are printed for every device, prefixed `fixup <hostname>:`. Neither ever prints config text, matched lines or replacement text.

Check a rule file without touching any config:

```sh
.venv/bin/ddn fixup --check rules.yml     # prints "rules.yml: 9 rule(s) OK", or the first error
```

`--check` runs the same validation as a real run (see below). It reads only the rule file, writes nothing and exits non-zero if the file is invalid.

## Fixup rules

### User default fixup rules

To avoid copying a rule file into every lab, put your own rules in `$XDG_CONFIG_HOME/ddnetmirror/fixups.yml` (default `~/.config/ddnetmirror/fixups.yml`). `ddn fetch … --lab-name` picks its rules like this:

1. `--fixup-rules PATH` uses that file.
2. `--fixup-rules none` (exactly, lowercase) applies no fixups, even if the user default exists.
3. Without `--fixup-rules`, the user default file is used if it exists. Otherwise no fixups are applied, and the collected configs are used unchanged.

The command prints which one it used, e.g. `fixup rules: /home/me/.config/ddnetmirror/fixups.yml (user default)` or `fixup rules: none (disabled with --fixup-rules none)`.

- **Errors, not silent fallback.** If the user default exists but is a directory, unreadable or invalid, the command stops with an error before anything is fetched.
- **Only `ddn fetch … --lab-name`.** Standalone `ddn fixup` and `ddn fixup --check` never look at it; pass the file explicitly (`ddn fixup --check ~/.config/ddnetmirror/fixups.yml`).
- **Yours alone.** ddnetmirror never creates, installs, copies or updates this file.

### Rule file format

A rule file is a YAML list, and rules run in order, each on the previous rule's output. Every rule has an `op`, and all but `collapse-separators` have a `match`. `match` is a literal substring, or a Python regex when `regex: true`. Matching is per line, without the line ending.

| op | Fields | Effect |
|---|---|---|
| `replace` | `match`, `with` | Replaces matches in every line (regex mode uses `re.sub`, so `\1` works). |
| `delete-line` | `match` | Removes matching lines only; their indented children stay. |
| `delete-block` | `match` | Removes each matching line plus the following more-indented lines. The block ends at the next non-blank line with equal or lesser indentation, such as an unindented `!`, which is kept. |
| `insert-if-missing` | `match`, `text`, optional `before` | If no line matches, appends `text` verbatim at the end of the file, or inserts it just before the one line matching `before`. |
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
- `insert-if-missing` with `before`:
  - Example: `- {op: insert-if-missing, match: '^maintenance$', regex: true, before: '^end$', text: "maintenance\n"}` puts `maintenance` above the final `end`.
  - The fragment goes in verbatim immediately before the anchor line, which is left unchanged. If the fragment lacks a final newline, it gets the anchor's line ending.
  - `before` is checked only when nothing matches `match`; if the text is already there, nothing happens.
  - `regex` applies to both fields. With `regex: true`, `before` is a Python regex searched in each line, like `match`. Without it, `before` must equal a whole line (`before: end` matches `end` but not `! end of bgp`), whereas a literal `match` is a substring.
  - If `before` matches no line or more than one line, the rule fails with an error and no output is written for that file (with `ddn fetch`, that device's startup config; its collected original is kept).
- A rule file is fully validated before use: unknown ops or fields, bad regexes (including `before`) and bad `\N` references are all rejected.
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
- **EOS eligibility is the system `eos` tag.** EOS devices without CloudVision's system-generated `eos` tag are never selected (see [Only EOS devices are selected](#only-eos-devices-are-selected)). Verified by owner observation on on-prem CVP and CVaaS only.
- **Image choice is by version string only.** `--image IMAGE` applies to every node. Without it, local `ceos` tags are matched to inventory versions (see above), with no knowledge of platforms or compatibility. You can always override a node in the YAML by hand.
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
