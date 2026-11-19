"""ddn: one command for the ddnetmirror tools (ddn fetch, ddn fixup)."""

import argparse
import sys

from ddnetmirror import fetch_config, fixup

COMMANDS = {
    "fetch": (fetch_config.main, "fetch running configs from CloudVision (ddnetmirror-fetch-config)"),
    "fixup": (fixup.main, "apply text fixup rules to a copy of a config (ddnetmirror-fixup)"),
}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ddn",
        description="ddnetmirror. Run 'ddn COMMAND --help' for a command's options.",
        epilog="commands:\n" + "\n".join(f"  {n:8}{h}" for n, (_, h) in COMMANDS.items()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", choices=COMMANDS, metavar="COMMAND", help="fetch or fixup")
    return parser


def main(argv=None):
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    # Parse only the command word (or -h / a usage error); everything after it,
    # including a leading "--", goes to the subcommand verbatim.
    args = parser.parse_args(argv[:1])
    command, _ = COMMANDS[args.command]
    return command(argv[1:], prog=f"ddn {args.command}")


if __name__ == "__main__":
    sys.exit(main())
