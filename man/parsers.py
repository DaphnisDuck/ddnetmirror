"""The real ddn parsers, for argparse-manpage (see man/README.md)."""

from ddnetmirror import build, cli, fetch_config, fixup


def ddn():
    return cli.build_parser()


def ddn_fetch():
    return fetch_config.build_parser("ddn fetch")


def ddn_fixup():
    return fixup.build_parser("ddn fixup")


def ddn_build():
    return build.build_parser("ddn build")
