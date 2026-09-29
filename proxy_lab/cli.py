"""Command-line entry point for proxy-lab."""

from __future__ import annotations

import argparse
import json
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

EPILOG = """\
examples:
  uvx proxy-lab start android
  uvx proxy-lab start ios
  uvx proxy-lab start android --json
  uvx proxy-lab doctor android --json
"""


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


def _add_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        help="machine-readable JSON output",
    )


def _add_detach_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--detach",
        action="store_true",
        help="run in the background and return once the proxy is ready",
    )
    parser.add_argument(
        "--duration",
        type=_positive_int,
        metavar="SECONDS",
        help="stop automatically after this many seconds",
    )
    parser.add_argument(
        "--log-format",
        choices=("text", "jsonl"),
        default=None,
        help="traffic output format (default: text, or jsonl with --json)",
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
        description="mitmproxy for the Android emulator and the iOS Simulator.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)

    start = commands.add_parser(
        "start",
        help="run the platform proxy — stop with Ctrl-C",
        description="Start mitmproxy for a platform. Ctrl-C (or stop) tears it down.",
    )
    platforms = start.add_subparsers(dest="platform", required=True)

    def add_start_common(platform_parser: argparse.ArgumentParser) -> None:
        platform_parser.add_argument(
            "config",
            nargs="?",
            metavar="domains.yml",
            help="domain list to log (default: the bundled domains.yaml)",
        )
        platform_parser.add_argument(
            "--config",
            dest="config_option",
            metavar="domains.yml",
            help="domain list (alternative to the positional path)",
        )
        platform_parser.add_argument(
            "-s",
            "--script",
            dest="scripts",
            action="append",
            default=[],
            type=_existing_file,
            help="mitmproxy addon script; may be repeated",
        )
        _add_detach_options(platform_parser)
        _add_json_option(platform_parser)
        _add_state_option(platform_parser)

    android = platforms.add_parser("android", help="proxy the Android emulator")
    add_start_common(android)
    _add_device_options(android, "android")

    ios = platforms.add_parser("ios", help="proxy the iOS Simulator")
    add_start_common(ios)
    _add_device_options(ios, "ios")

    for name, help_text in (
        ("stop", "stop a recorded proxy-lab session"),
        ("reset", "stop sessions and clear Android proxy settings"),
        ("status", "show recorded proxy-lab sessions"),
        ("doctor", "report versions, devices, tools, and configuration"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("platform", choices=("android", "ios"), nargs="?")
        _add_control_options(command)
        _add_json_option(command)

    logs = commands.add_parser(
        "logs", help="print the captured traffic of a detached session"
    )
    logs.add_argument("platform", choices=("android", "ios"), nargs="?")
    logs.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="keep printing new traffic as it arrives",
    )
    logs.add_argument(
        "--lines",
        type=_positive_int,
        metavar="N",
        help="print only the last N lines",
    )
    _add_state_option(logs)
    _add_json_option(logs)

    init = commands.add_parser(
        "init", help="write the files an app needs to trust the proxy CA"
    )
    init.add_argument("platform", choices=("android", "ios"))
    init.add_argument(
        "--path",
        metavar="DIR",
        help="project directory to write into (default: current directory)",
    )
    _add_json_option(init)

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


def _apply_start_defaults(args: argparse.Namespace) -> None:
    """`--json` is agent mode: detach, jsonl traffic, JSON ready document."""

    if getattr(args, "command", None) != "start":
        return
    if getattr(args, "json_output", False):
        args.detach = True
        if getattr(args, "log_format", None) is None:
            args.log_format = "jsonl"
    elif getattr(args, "log_format", None) is None:
        args.log_format = "text"


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
    if getattr(args, "detach", False):
        env["DETACH"] = "1"
    if getattr(args, "duration", None) is not None:
        env["DURATION"] = str(args.duration)
    log_format = getattr(args, "log_format", None)
    if log_format is not None:
        env["PROXY_LAB_LOG_FORMAT"] = log_format
    if getattr(args, "json_output", False):
        env["JSON_OUTPUT"] = "1"
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
    if getattr(args, "follow", False):
        command.append("--follow")
    if getattr(args, "lines", None) is not None:
        command.extend(("--lines", str(args.lines)))
    if getattr(args, "json_output", False):
        command.append("--json")
    env = os.environ.copy()
    env["PROXY_LAB_PYTHON"] = sys.executable
    env["PROXY_LAB_VERSION"] = __version__
    if getattr(args, "state_dir", None) is not None:
        env["PROXY_LAB_STATE_DIR"] = str(Path(args.state_dir).expanduser().resolve())
    return subprocess.call(command, env=env)


def _start_command(args: argparse.Namespace, script: Path) -> list[str]:
    """Build the launcher argv. Env is the clone-script contract; argv is what
    `parse_launcher_args` actually reads, so pass both."""

    command = ["bash", str(script)]
    if getattr(args, "detach", False):
        command.append("--detach")
    if getattr(args, "duration", None) is not None:
        command.extend(("--duration", str(args.duration)))
    log_format = getattr(args, "log_format", None)
    if log_format and log_format != "text":
        command.extend(("--log-format", log_format))
    if getattr(args, "port", None) is not None:
        command.extend(("--port", str(args.port)))
    if getattr(args, "avd", None) is not None:
        command.extend(("--avd", args.avd))
    if getattr(args, "serial", None) is not None:
        command.extend(("--serial", args.serial))
    if getattr(args, "boot_timeout", None) is not None:
        command.extend(("--boot-timeout", str(args.boot_timeout)))
    if getattr(args, "udid", None) is not None:
        command.extend(("--udid", args.udid))
    if getattr(args, "json_output", False):
        command.append("--json")
    for path in getattr(args, "scripts", []) or []:
        command.extend(("--script", str(path)))
    return command


def _run_start(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    config = _config_path(args, parser)
    env = _environment_for_start(args, config)
    script = LAUNCHER_ROOT / args.platform / "start-proxy.sh"
    if not script.is_file():
        parser.error(f"launcher missing: {script}")
    command = _start_command(args, script)
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


NETWORK_SECURITY_CONFIG = """<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by `proxy-lab init android`.
     <debug-overrides> applies only to debuggable builds, so this does not
     weaken a release build. Keep it in the debug source set. -->
<network-security-config>
   <debug-overrides>
      <trust-anchors>
         <certificates src="user" />
      </trust-anchors>
   </debug-overrides>
</network-security-config>
"""

MANIFEST_SNIPPET = """android:networkSecurityConfig="@xml/network_security_config\""""


def _android_res_path(root: Path) -> Path | None:
    """Return the debug resource path for *root*, preferring debug over main."""

    for source_set in ("debug", "main"):
        candidate = root / "app" / "src" / source_set / "res"
        if candidate.is_dir():
            return candidate
    return None


def _find_android_project(start: Path) -> Path:
    """Walk up from *start* looking for a typical Android module layout."""

    here = start.resolve()
    for candidate in (here, *here.parents):
        if (candidate / "app" / "src").is_dir():
            return candidate
        if (candidate / "settings.gradle").is_file() or (
            candidate / "settings.gradle.kts"
        ).is_file():
            return candidate
    return here


def _manifest_paths(root: Path) -> list[Path]:
    return [
        path
        for path in (
            root / "app" / "src" / "debug" / "AndroidManifest.xml",
            root / "app" / "src" / "main" / "AndroidManifest.xml",
        )
        if path.is_file()
    ]


def _run_init(args: argparse.Namespace) -> int:
    root = Path(args.path).expanduser() if args.path else Path.cwd()
    written: list[str] = []
    skipped: list[str] = []

    if args.platform == "android":
        root = _find_android_project(root)
        res = _android_res_path(root)
        if res is None:
            message = (
                f"no app/src/debug/res or app/src/main/res under {root}; "
                "create it, or pass --path to the Android project, then re-run "
                "proxy-lab init android"
            )
            if args.json_output:
                print(
                    json.dumps(
                        {"platform": "android", "written": [], "error": message},
                    )
                )
            else:
                print(f"  ✗ init        {message}", file=sys.stderr)
            return 3
        target = res / "xml" / "network_security_config.xml"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text(encoding="utf-8") == NETWORK_SECURITY_CONFIG:
            skipped.append(str(target))
        else:
            target.write_text(NETWORK_SECURITY_CONFIG, encoding="utf-8")
            written.append(str(target))
    else:
        message = (
            "iOS needs no project file: run `proxy-lab start ios` and approve the "
            "mitmproxy extension prompt once"
        )
        if args.json_output:
            print(json.dumps({"platform": "ios", "written": [], "note": message}))
        else:
            print(f"  i init        {message}")
        return 0

    manifests = _manifest_paths(root)
    manifest_ready = any(
        "networkSecurityConfig" in path.read_text(encoding="utf-8")
        for path in manifests
    )
    manifest_hint = (
        "already set in AndroidManifest.xml"
        if manifest_ready
        else f"add {MANIFEST_SNIPPET} to the <application> tag in "
        + (str(manifests[0]) if manifests else "AndroidManifest.xml")
    )

    if args.json_output:
        print(
            json.dumps(
                {
                    "platform": "android",
                    "project": str(root),
                    "written": written,
                    "unchanged": skipped,
                    "manifest_attribute": MANIFEST_SNIPPET,
                    "manifest_ready": manifest_ready,
                }
            )
        )
        return 0

    for path in written:
        print(f"  ✓ init        wrote {path}")
    for path in skipped:
        print(f"  ✓ init        already up to date: {path}")
    print(f"  → init        {manifest_hint}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _apply_start_defaults(args)
    _validate_platform_options(parser, args)

    if args.command == "start":
        try:
            return _run_start(args, parser)
        except ValueError as exc:
            parser.error(str(exc))

    if args.command == "init":
        return _run_init(args)

    if args.command == "logs":
        return _run_control(args, "logs")

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
