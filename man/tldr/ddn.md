# ddn

> Build Containerlab labs from Arista CloudVision data, keeping running configs unchanged.
> The CloudVision token is read from the `CLOUDVISION_TOKEN` environment variable.
> More information: <https://github.com/DaphnisDuck/ddnetmirror>.

- Fetch one device's running config by inventory hostname (writes `leaf1.cfg`):

`ddn fetch --server {{www.arista.io}} --hostname {{leaf1}}`

- Collect every device with a tag into a directory and report LLDP links between them:

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp`

- Also write a Containerlab topology, using startup configs transformed by fixup rules:

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp --lab-name {{lab1}} --image {{ceos:4.35.7.2M}} --fixup-rules {{rules.yml}}`

- Regenerate that lab in the same directory, replacing its generated files (no backups):

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp --lab-name {{lab1}} --image {{ceos:4.35.7.2M}} --fixup-rules {{rules.yml}} --force`

- Use an on-prem CVP server with a private CA:

`ddn fetch --server {{cvp.example.net}} --ca-file {{ca.pem}} --hostname {{leaf1}}`

- Apply fixup rules to a copy of one config file:

`ddn fixup {{lab1/collected/leaf1.cfg}} --rules {{rules.yml}} --output {{leaf1-lab.cfg}}`

- Check a fixup rule file without reading or writing any config:

`ddn fixup --check {{rules.yml}}`

- Show the options of a subcommand:

`ddn {{fetch|fixup}} --help`
