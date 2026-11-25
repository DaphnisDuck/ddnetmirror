"""The real ddn parsers, for argparse-manpage (see man/README.md)."""

from ddnetmirror import cli, fetch_config, fixup, up


def ddn():
    return cli.build_parser()


def ddn_fetch():
    return fetch_config.build_parser("ddn fetch")


def ddn_fixup():
    return fixup.build_parser("ddn fixup")


def ddn_up():
    return up.build_parser("ddn up")


def ddn_down():
    return up.build_down_parser("ddn down")
