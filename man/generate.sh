#!/bin/sh
# Regenerate man/*.1 from the real argparse parsers (see man/README.md).
# Needs argparse-manpage and ddnetmirror installed in the venv that provides
# argparse-manpage on PATH.
set -eu
cd "$(dirname "$0")"
bin=$(dirname "$(command -v argparse-manpage)")
version=$("$bin/python" -c 'import importlib.metadata as m; print(m.version("ddnetmirror"))')
# page date: last commit touching the CLI modules, so regeneration is reproducible
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(git log -1 --format=%ct -- \
    ../ddnetmirror/cli.py ../ddnetmirror/fetch_config.py ../ddnetmirror/fixup.py)}"

page() {  # page-name parser-function description
    argparse-manpage --pyfile parsers.py --function "$2" --description "$3" \
        --project-name ddnetmirror --version "$version" --manual-title "ddnetmirror manual" \
        --url https://github.com/DaphnisDuck/ddnetmirror --output "$1.1"
    # argparse-manpage writes the prog ("ddn fetch") unquoted into .TH; use the page name
    title=$(echo "$1" | tr a-z A-Z)
    sed -i "1s/^\.TH .* \"1\" /.TH $title \"1\" /" "$1.1"
}

page ddn ddn "build Containerlab labs from Arista CloudVision data"
page ddn-fetch ddn_fetch "fetch running configs, LLDP links and a Containerlab topology from CloudVision"
page ddn-fixup ddn_fixup "apply text fixup rules to a copy of a config file"
