# ddnetmirror
Query Arista CVP/CVaaS and generate Containerlab digital twins

## Fetch one device's running config

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
export CLOUDVISION_TOKEN=...   # CloudVision service-account token (env var only)
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --hostname leaf1
# or select the one device carrying a CloudVision device tag:
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --tag role=spine1
# or collect every device carrying a tag into a directory (one <hostname>.cfg each):
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --tag site=lab --output-dir configs
# ...and also print LLDP links between the collected devices:
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --tag site=lab --output-dir configs --lldp
# ...and also write a Containerlab topology configs/lab1.clab.yml for the saved configs:
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --tag site=lab --output-dir configs --lldp \
    --lab-name lab1 --image ceos:4.35.7.2M
# ...with startup configs transformed by fixup rules (originals kept in configs/collected):
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --tag site=lab --output-dir configs --lldp \
    --lab-name lab1 --image ceos:4.35.7.2M --fixup-rules rules.yml
# on-prem CVP with a private CA:
.venv/bin/ddnetmirror-fetch-config --server cvp.example.net --hostname leaf1 --ca-file ca.pem
```

Looks the device up by hostname in the CloudVision inventory, or by a `tag.v2` device tag assignment (exactly one match required), retrieves its running config with `cloudvision.compliance.ComplianceClient.get_config`, and writes it unchanged to `<hostname>.cfg` (or `--output`). The output is encoded as UTF-8. An existing file is never overwritten, and nothing is written if the lookup or retrieval fails. With `--output-dir`, devices are collected one after another. Successful files are kept when other devices fail, each failure is reported, and the exit status is non-zero unless every device was collected. A device whose hostname is unsafe as a file name, is shared with another tagged device, or already has a file in the directory is skipped and reported, never renamed or overwritten.

`--lldp` prints one line per link between selected devices, for example `leaf1 Ethernet1 <-> spine1 Ethernet49/1 [reciprocal]`, or `[one-sided]` when only one side reported it. Interface names are printed exactly as LLDP reports them. A neighbor becomes a link only if its LLDP system name exactly matches the hostname of one selected device and its port ID is an LLDP interface that device reports. Anything else is listed as `skipped LLDP: …` and does not change the exit status.

`--lab-name NAME --image IMAGE` (together, with `--lldp`) writes `DIR/NAME.clab.yml`. It has one cEOS node per saved config, named by the inventory hostname exactly as-is. Nodes are compact (`"leaf1": {}`) and inherit `kind: ceos`, the image and `startup-config: "__clabNodeName__.cfg"` from `topology.defaults`. Containerlab substitutes the node name and resolves the path relative to the topology file. Any node can override them, for example `"leaf1": {image: "ceos:4.33.11.2M"}`. The header comment gives the ddnetmirror version, the UTC generation time (when the file was written, not when CloudVision observed the devices) and node and link counts. One-sided LLDP links are marked with a comment. Links come only from accepted LLDP links between saved devices, with interfaces converted mechanically (`Ethernet49/1` → `eth49_1`). A link whose interface can't be converted is left out and reported. Configs are never modified, including their `hostname` lines. If some devices failed, the topology is marked `INCOMPLETE` in its header and the exit status is non-zero. An existing topology file is never overwritten. Deploy it with `sudo containerlab deploy -t DIR/NAME.clab.yml`.

`--fixup-rules RULES.yml` (with `--lab-name`) applies one rule file (see [Text fixups](#text-fixups)) to every collected config. The rule file is checked before anything is fetched or written. Originals are saved unchanged in `DIR/collected/<hostname>.cfg`, and the transformed copies in `DIR/startup/<hostname>.cfg`, which the topology references (`startup-config: "startup/__clabNodeName__.cfg"`). If a device's fixup fails, its original is kept, it gets no startup file and no topology node (nor links), the failure is reported, the topology is marked `INCOMPLETE`, and the exit status is non-zero. Without `--fixup-rules` the layout is unchanged.

**Live CVP/CVaaS compatibility is unverified.** This has been tested only against stubbed responses (`.venv/bin/pytest`).

## Text fixups

`ddnetmirror-fixup INPUT --rules RULES.yml --output OUTPUT` applies operator-defined text rules to a copy of a config. The input is never modified. The output is a new file (mode 0600) that is never overwritten, and nothing is written if a rule file or the input is bad. This is plain line-based text editing. ddnetmirror does not interpret EOS.

The rule file is a YAML list. Rules run in order, and each sees the previous rule's output. Every rule has an `op`, a `match` (a literal substring, or a Python regex when `regex: true`) and, depending on the op:

| op | effect |
|---|---|
| `replace` | in every line, replace `match` with `with` (regex: `re.sub`, so `\1` works) |
| `delete-line` | remove matching lines only (their indented children stay) |
| `delete-block` | remove each matching line and the following more-indented lines. The block ends at the next non-blank line with equal or lesser indentation, so an unindented `!` ends it and is kept. Indented `!` lines inside the block are removed, as are blank lines between its lines. |
| `insert-if-missing` | if no line matches, append `text` exactly as given (including any `!` lines) at the end of the file |

```yaml
- {op: replace, match: '^hostname (\S+)$', with: 'hostname lab-\1', regex: true}
- {op: delete-line, match: '^end$', regex: true}
- {op: delete-block, match: daemon TerminAttr}
- op: insert-if-missing
  match: '^interface Management0$'
  regex: true
  text: |
    interface Management0
       description lab management
    !
- {op: insert-if-missing, match: '^end$', regex: true, text: "end\n"}
```

Matching is per line, without the line ending. Line endings and all untouched text are kept byte for byte, and a missing final newline stays missing unless text is appended. `!` separators are never added, removed or collapsed except by the rules above. Inserted text is used verbatim, including its LF line endings. Because a running config ends with `end`, the example deletes `end`, appends the fragment, then appends `end` again. In a cEOS 4.35.7.2M smoke test, a fragment appended after `end` was also applied, but keeping `end` last keeps the file conventional.
