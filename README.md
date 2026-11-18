# ddnetmirror
Query Arista CVP/CVaaS and generate Containerlab digital twins

## Fetch one device's running config

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
export CLOUDVISION_TOKEN=...   # CloudVision service-account token (env var only)
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --hostname leaf1
# or select the one device carrying a CloudVision device tag:
.venv/bin/ddnetmirror-fetch-config --server www.arista.io --tag role=spine1
# on-prem CVP with a private CA:
.venv/bin/ddnetmirror-fetch-config --server cvp.example.net --hostname leaf1 --ca-file ca.pem
```

Looks the device up by hostname in the CloudVision inventory, or by a `tag.v2` device tag assignment (exactly one match required), retrieves its running config with `cloudvision.compliance.ComplianceClient.get_config`, and writes it unchanged to `<hostname>.cfg` (or `--output`). The output is encoded as UTF-8. An existing file is never overwritten, and nothing is written if the lookup or retrieval fails.

**Live CVP/CVaaS compatibility is unverified.** This has been tested only against stubbed responses (`.venv/bin/pytest`).
