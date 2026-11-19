# Contributing

Thanks for your interest. ddnetmirror is a small hobby project, so please keep changes small.

- **Open an issue first** and describe the problem or idea before writing code.
- **Stay within scope.** ddnetmirror copies running configs, applies operator-written text fixups and generates Containerlab topologies. It is not an EOS interpreter: no config parsing, classification or automatic repair. See [AGENTS.md](AGENTS.md) for the project principles.
- **Small, focused pull requests**, one change each, referencing the issue, with proportional tests.
- **Never post real configs, tokens or device data** in issues, PRs or tests. Use synthetic data (documentation addresses like `192.0.2.0/24`, made-up hostnames and serials).

Run the tests:

```sh
python3 -m venv .venv && .venv/bin/pip install -e ".[test]"
.venv/bin/pytest
```

If you change command-line options, regenerate the man pages with `man/generate.sh` (see [man/README.md](man/README.md)).
