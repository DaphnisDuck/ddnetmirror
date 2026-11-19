# Man pages and examples

- `ddn.1`, `ddn-fetch.1`, `ddn-fixup.1`: generated from the real argparse parsers. Read them with `man -l man/ddn-fetch.1`. `pip install` does not install them.
- `tldr/ddn.md`: common examples in [tldr](https://tldr.sh) page format.

Fixup rule syntax and the full workflow are documented in the top-level README.

## Regenerating (maintainers)

After changing any option or help text, regenerate and commit the pages. `argparse-manpage` is a maintainer-only tool, not a dependency of ddnetmirror.

```sh
python3 -m venv /tmp/ddn-man && /tmp/ddn-man/bin/pip install argparse-manpage .
PATH=/tmp/ddn-man/bin:$PATH man/generate.sh
git diff man/
```

The page date is the date of the last commit that touched the CLI modules (`SOURCE_DATE_EPOCH`), so regenerating unchanged parsers gives identical files.
