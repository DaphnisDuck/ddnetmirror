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
# on-prem CVP with a private CA:
.venv/bin/ddnetmirror-fetch-config --server cvp.example.net --hostname leaf1 --ca-file ca.pem
```

Looks the device up by hostname in the CloudVision inventory, or by a `tag.v2` device tag assignment (exactly one match required), retrieves its running config with `cloudvision.compliance.ComplianceClient.get_config`, and writes it unchanged to `<hostname>.cfg` (or `--output`). The output is encoded as UTF-8. An existing file is never overwritten, and nothing is written if the lookup or retrieval fails. With `--output-dir`, devices are collected one after another. Successful files are kept when other devices fail, each failure is reported, and the exit status is non-zero unless every device was collected. A device whose hostname is unsafe as a file name, is shared with another tagged device, or already has a file in the directory is skipped and reported, never renamed or overwritten.

`--lldp` prints one line per link between selected devices, for example `leaf1 Ethernet1 <-> spine1 Ethernet49/1 [reciprocal]`, or `[one-sided]` when only one side reported it. Interface names are printed exactly as LLDP reports them. A neighbor becomes a link only if its LLDP system name exactly matches the hostname of one selected device and its port ID is an LLDP interface that device reports. Anything else is listed as `skipped LLDP: …` and does not change the exit status.

`--lab-name NAME --image IMAGE` (together, with `--lldp`) writes `DIR/NAME.clab.yml`. It has one `arista_ceos` node per saved config, named by the inventory hostname exactly as-is. Each node's `startup-config` is a path relative to the topology file (for example `leaf1.cfg`). Links come only from accepted LLDP links between saved devices, with interfaces converted mechanically (`Ethernet49/1` → `eth49_1`). A link whose interface can't be converted is left out and reported. Configs are never modified, including their `hostname` lines. If some devices failed, the topology is marked `INCOMPLETE` in its header and the exit status is non-zero. An existing topology file is never overwritten. Deploy it with `sudo containerlab deploy -t DIR/NAME.clab.yml`.

**Live CVP/CVaaS compatibility is unverified.** This has been tested only against stubbed responses (`.venv/bin/pytest`).
