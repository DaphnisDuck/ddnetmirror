# ddn

> Build Containerlab labs from Arista CloudVision data, keeping running configs unchanged.
> The CloudVision token is read from `--token-file`, else from `CLOUDVISION_TOKEN`, else from `~/.config/ddnetmirror/token`.
> More information: <https://github.com/DaphnisDuck/ddnetmirror>.

- Fetch one device's running config by inventory hostname (writes `leaf1.cfg`):

`ddn fetch --server {{www.arista.io}} --hostname {{leaf1}}`

- Collect every device with a tag into a directory and report LLDP links between them:

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp`

- Also write a Containerlab topology, using startup configs transformed by fixup rules:

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp --lab-name {{lab1}} --image {{ceos:4.35.7.2M}} --fixup-rules {{rules.yml}}`

- Write a topology, picking a local `ceos` image per device by EOS version (the default without `--image`):

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp --lab-name {{lab1}}`

- Regenerate that lab in the same directory, replacing its generated files (no backups):

`ddn fetch --server {{www.arista.io}} --tag {{site=lab}} --output-dir {{lab1}} --lldp --lab-name {{lab1}} --image {{ceos:4.35.7.2M}} --fixup-rules {{rules.yml}} --force`

- Use an on-prem CVP server with a private CA:

`ddn fetch --server {{cvp.example.net}} --ca-file {{ca.pem}} --hostname {{leaf1}}`

- Read the token from one environment's token file instead of the default (an unusable file is an error):

`ddn fetch --server {{www.arista.io}} --hostname {{leaf1}} --token-file {{~/.config/ddnetmirror/tokens/cvaas}}`

- Apply fixup rules to a copy of one config file:

`ddn fixup {{lab1/configs/collected/leaf1.cfg}} --rules {{rules.yml}} --output {{leaf1-lab.cfg}}`

- Check a fixup rule file without reading or writing any config:

`ddn fixup --check {{rules.yml}}`

- Build and deploy the lab described by `ddnetmirror.yml` in the current directory (refuses if the lab exists):

`ddn up`

- Build and deploy it with the token of one environment:

`ddn up --token-file {{~/.config/ddnetmirror/tokens/staging}}`

- Show the options of a subcommand:

`ddn {{fetch|fixup|up}} --help`
