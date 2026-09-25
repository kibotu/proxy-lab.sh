# CLI entry point. The platform scripts do the work; this only dispatches them
# and passes the optional domains file through the environment.
import argparse
import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="proxy-lab",
        description="Start a mitmproxy for the Android emulator or the iOS simulator.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser(
        "start", help="run the platform's proxy — stop with Ctrl-C"
    )
    start.add_argument(
        "platform", choices=("android", "ios"), help="which device to proxy"
    )
    start.add_argument(
        "config",
        nargs="?",
        metavar="domains.yml",
        help="domain list to log (default: the bundled domains.yaml)",
    )
    args = parser.parse_args()

    if args.config is not None:
        config = Path(args.config).expanduser()
        if not config.is_file():
            parser.error(f"config not found: {config}")
        os.environ["PROXY_LAB_CONFIG"] = str(config.resolve())

    script = PACKAGE_DIR / args.platform / "start-proxy.sh"
    # exec: this process becomes the script, so Ctrl-C and the exit code
    # propagate exactly as if the script had been run directly.
    os.execvp("bash", ["bash", str(script)])
