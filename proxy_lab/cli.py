"""Command-line entry point for proxy-lab."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import ConfigError, load_domains

PACKAGE_DIR = Path(__file__).resolve().parent
if (PACKAGE_DIR.parent / "android" / "start-proxy.sh").is_file():
    LAUNCHER_ROOT = PACKAGE_DIR.parent
else:
    LAUNCHER_ROOT = PACKAGE_DIR
CONTROL_SCRIPT = PACKAGE_DIR / "control.sh"


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _existing_file(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_file():
        raise argparse.ArgumentTypeError(f"file not found: {path}")
    return path.resolve()


def _add_state_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--state-dir",
        help="directory for owner-scoped proxy-lab session state",
    )


def _add_device_options(parser: argparse.ArgumentParser, platform: str | None = None) -> None:
    if platform in (None, "android"):
        parser.add_argument("--port", type=_positive_int, help="Android mitmdump port")
        parser.add_argument("--avd", help="Android AVD to boot when none is running")
        parser.add_argument("--serial", help="Android emulator serial to target")
        parser.add_argument(
            "--boot-timeout",
            type=_positive_int,
            help="seconds to wait for Android boot (default: 240)",
        )
    if platform in (None, "ios"):
        parser.add_argument(
            "--udid",
            help="iOS Simulator UDID to select for discovery/CA trust",
        )


def _add_control_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--port", type=_positive_int, help="Android session port")
    parser.add_argument("--serial", help="Android emulator serial")
    parser.add_argument(
        "--udid",
        help="iOS Simulator UDID to select for discovery/CA trust",
    )
    _add_state_option(parser)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy-lab",
        description="Start and manage mitmproxy for Android emulators and iOS Simulators.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="run the platform proxy — stop with Ctrl-C")
    start.add_argument("platform", choices=("android", "ios"))
    start.add_argument("config", nargs="?", metavar="domains.yml")
    start.add_argument(
        "--config",
        dest="config_option",
        metavar="domains.yml",
        help="domain list to log (alternative to the positional path)",
    )
    start.add_argument(
        "-s",
        "--script",
        dest="scripts",
        action="append",
        default=[],
        type=_existing_file,
        help="mitmproxy addon script; may be repeated",
    )
    _add_device_options(start)
    _add_state_option(start)

    for name, help_text in (
        ("stop", "stop a recorded proxy-lab session"),
        ("reset", "stop sessions and clear Android proxy settings"),
        ("status", "show recorded proxy-lab sessions"),
        ("doctor", "report versions, devices, tools, and configuration"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("platform", choices=("android", "ios"), nargs="?")
        _add_control_options(command)

    trust = commands.add_parser("trust", help="install the mitmproxy CA in an iOS Simulator")
    trust.add_argument("platform", choices=("ios",))
    _add_device_options(trust, "ios")
    _add_state_option(trust)
    return parser


def _config_path(args: argparse.Namespace, parser: argparse.ArgumentParser) -> Path | None:
    if args.config is not None and args.config_option is not None:
        parser.error("provide the config path either positionally or with --config, not both")
    value = args.config_option or args.config
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_file():
        parser.error(f"config not found: {path}")
    return path.resolve()


def _environment_for_start(args: argparse.Namespace, config: Path | None) -> dict[str, str]:
    env = os.environ.copy()
    if config is not None:
        try:
            load_domains(config)
        except ConfigError as exc:
            raise ValueError(str(exc)) from exc
        env["PROXY_LAB_CONFIG"] = str(config)
    if args.scripts:
        env["PROXY_LAB_SCRIPTS"] = "\n".join(str(path) for path in args.scripts)
    if getattr(args, "port", None) is not None:
        env["PORT"] = str(args.port)
    if getattr(args, "avd", None) is not None:
        env["AVD"] = args.avd
    if getattr(args, "serial", None) is not None:
        env["SERIAL"] = args.serial
    if getattr(args, "boot_timeout", None) is not None:
        env["BOOT_TIMEOUT"] = str(args.boot_timeout)
    if getattr(args, "udid", None) is not None:
        env["UDID"] = args.udid
    if getattr(args, "state_dir", None) is not None:
        env["PROXY_LAB_STATE_DIR"] = str(Path(args.state_dir).expanduser().resolve())
    env["PROXY_LAB_PYTHON"] = sys.executable
    env["PROXY_LAB_VERSION"] = __version__
    return env


def _run_control(args: argparse.Namespace, operation: str) -> int:
    command = ["bash", str(CONTROL_SCRIPT), operation]
    if args.platform is not None:
        command.append(args.platform)
    for option, value in (
        ("--serial", getattr(args, "serial", None)),
        ("--udid", getattr(args, "udid", None)),
        ("--port", getattr(args, "port", None)),
    ):
        if value is not None:
            command.extend((option, str(value)))
    env = os.environ.copy()
    env["PROXY_LAB_PYTHON"] = sys.executable
    env["PROXY_LAB_VERSION"] = __version__
    if getattr(args, "state_dir", None) is not None:
        env["PROXY_LAB_STATE_DIR"] = str(Path(args.state_dir).expanduser().resolve())
    return subprocess.call(command, env=env)


def _run_start(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _config_path(args, parser)
    env = _environment_for_start(args, config)
    script = LAUNCHER_ROOT / args.platform / "start-proxy.sh"
    if not script.is_file():
        parser.error(f"launcher missing: {script}")
    command = ["bash", str(script)]
    # exec: the shell becomes the launcher, preserving signals and exit status.
    os.execvpe(command[0], command, env)
    return 0  # pragma: no cover - os.execvpe does not return


def _validate_platform_options(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    platform = getattr(args, "platform", None)
    if platform == "android" and getattr(args, "udid", None) is not None:
        parser.error("--udid is only valid for iOS")
    if platform == "ios":
        android_options = (
            getattr(args, "port", None),
            getattr(args, "avd", None),
            getattr(args, "serial", None),
            getattr(args, "boot_timeout", None),
        )
        if any(value is not None for value in android_options):
            parser.error("--port, --avd, --serial, and --boot-timeout are Android-only")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_platform_options(parser, args)

    if args.command == "start":
        try:
            return _run_start(args, parser)
        except ValueError as exc:
            parser.error(str(exc))

    if args.command == "trust":
        env = os.environ.copy()
        env["PROXY_LAB_PYTHON"] = sys.executable
        env["PROXY_LAB_VERSION"] = __version__
        if args.udid is not None:
            env["UDID"] = args.udid
        if args.state_dir is not None:
            env["PROXY_LAB_STATE_DIR"] = str(Path(args.state_dir).expanduser().resolve())
        script = LAUNCHER_ROOT / "ios" / "start-proxy.sh"
        command = ["bash", str(script), "--trust-only"]
        if args.udid is not None:
            command.extend(("--udid", args.udid))
        return subprocess.call(command, env=env)

    return _run_control(args, args.command)


if __name__ == "__main__":
    raise SystemExit(main())
