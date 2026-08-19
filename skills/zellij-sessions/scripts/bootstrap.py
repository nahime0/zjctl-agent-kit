#!/usr/bin/env python3
"""Plan and perform a pinned, checksum-verified zjctl installation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

COMPATIBILITY_PATH = Path(__file__).resolve().parent.parent / "compatibility.json"
DOWNLOAD_TIMEOUT_SECONDS = 60
COMMAND_TIMEOUT_SECONDS = 30


class BootstrapError(RuntimeError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _json(data: Any, *, stream: Any = sys.stdout) -> None:
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")


def load_compatibility(path: Path = COMPATIBILITY_PATH) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BootstrapError(
            "invalid_manifest", f"Cannot read compatibility manifest: {exc}"
        ) from exc
    required = {"zjctl_version", "release_base_url", "cli_artifacts", "plugin"}
    missing = sorted(required - data.keys())
    if missing:
        raise BootstrapError(
            "invalid_manifest", "Compatibility manifest is incomplete", missing=missing
        )
    return data


def platform_key(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).casefold()
    machine = (machine or platform.machine()).casefold()
    os_name = {"darwin": "darwin", "linux": "linux"}.get(system)
    arch = {
        "arm64": "arm64",
        "aarch64": "arm64",
        "x86_64": "x86_64",
        "amd64": "x86_64",
    }.get(machine)
    if os_name is None or arch is None:
        raise BootstrapError(
            "unsupported_platform",
            f"Unsupported platform: {system}/{machine}",
            supported=["darwin-arm64", "darwin-x86_64", "linux-x86_64"],
        )
    return f"{os_name}-{arch}"


def _paths(bin_dir: str | None, plugin_dir: str | None) -> tuple[Path, Path]:
    cli_dir = Path(bin_dir).expanduser() if bin_dir else Path.home() / ".local" / "bin"
    wasm_dir = (
        Path(plugin_dir).expanduser()
        if plugin_dir
        else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "zellij"
        / "plugins"
    )
    return cli_dir / "zjctl", wasm_dir / "zrpc.wasm"


def _run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(args),
            capture_output=True,
            text=True,
            check=False,
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BootstrapError(
            "command_failed", f"Could not run {Path(args[0]).name}: {exc}"
        ) from exc


def _version_of(path: str | Path) -> str | None:
    try:
        result = _run([str(path), "--version"])
    except BootstrapError:
        return None
    if result.returncode != 0:
        return None
    match = re.search(r"(?:^|\s)([0-9]+\.[0-9]+\.[0-9]+)(?:\s|$)", result.stdout)
    return match.group(1) if match else None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def installation_plan(
    manifest: dict[str, Any],
    *,
    bin_dir: str | None = None,
    plugin_dir: str | None = None,
    system: str | None = None,
    machine: str | None = None,
) -> dict[str, Any]:
    key = platform_key(system, machine)
    artifact = manifest["cli_artifacts"].get(key)
    if artifact is None:
        raise BootstrapError(
            "unsupported_platform",
            f"No pinned zjctl artifact for {key}",
            supported=sorted(manifest["cli_artifacts"]),
        )
    cli_path, plugin_path = _paths(bin_dir, plugin_dir)
    desired = manifest["zjctl_version"]
    path_cli = shutil.which("zjctl")
    target_version = _version_of(cli_path) if cli_path.exists() else None
    path_version = (
        _version_of(path_cli)
        if path_cli and Path(path_cli) != cli_path
        else target_version
    )
    plugin_digest = sha256_file(plugin_path) if plugin_path.is_file() else None
    base = manifest["release_base_url"].rstrip("/")
    return {
        "platform": key,
        "pinned_zjctl_version": desired,
        "zellij_min_version": manifest.get("zellij_min_version"),
        "cli": {
            "target": str(cli_path),
            "target_version": target_version,
            "path_executable": path_cli,
            "path_version": path_version,
            "url": f"{base}/{artifact['filename']}",
            "sha256": artifact["sha256"],
            "action": "skip"
            if target_version == desired
            or (not cli_path.exists() and path_version == desired)
            else "install",
        },
        "plugin": {
            "target": str(plugin_path),
            "installed_sha256": plugin_digest,
            "url": f"{base}/{manifest['plugin']['filename']}",
            "sha256": manifest["plugin"]["sha256"],
            "action": "skip"
            if plugin_digest == manifest["plugin"]["sha256"]
            else "install",
        },
        "mutates_config": False,
        "next_steps": [
            "Ask the user to approve this exact pinned installation plan.",
            "After approval, run bootstrap.py install with the same path options.",
            "Configure auto-load or load a live session only with separate explicit approval.",
        ],
    }


def _download(url: str, expected_sha256: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": "zjctl-zellij-sessions-skill/1"})
    digest = hashlib.sha256()
    try:
        with (
            urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response,
            destination.open("wb") as output,
        ):
            while chunk := response.read(1024 * 1024):
                digest.update(chunk)
                output.write(chunk)
    except OSError as exc:
        raise BootstrapError(
            "download_failed", f"Could not download {url}: {exc}"
        ) from exc
    actual = digest.hexdigest()
    if actual != expected_sha256:
        raise BootstrapError(
            "checksum_mismatch",
            "Downloaded artifact failed SHA-256 verification",
            url=url,
            expected=expected_sha256,
            actual=actual,
        )


def extract_cli_bytes(archive: Path) -> bytes:
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            candidates = [
                member
                for member in bundle.getmembers()
                if member.isfile() and Path(member.name).name == "zjctl"
            ]
            if len(candidates) != 1:
                raise BootstrapError(
                    "invalid_archive",
                    "CLI archive must contain exactly one regular file named zjctl",
                    candidates=[member.name for member in candidates],
                )
            extracted = bundle.extractfile(candidates[0])
            if extracted is None:
                raise BootstrapError(
                    "invalid_archive", "Could not read zjctl from archive"
                )
            return extracted.read()
    except (tarfile.TarError, OSError) as exc:
        raise BootstrapError(
            "invalid_archive", f"Could not read CLI archive: {exc}"
        ) from exc


def _atomic_install(data: bytes, target: Path, mode: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", dir=target.parent
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, target)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def install(
    manifest: dict[str, Any],
    *,
    bin_dir: str | None,
    plugin_dir: str | None,
    replace: bool,
) -> dict[str, Any]:
    plan = installation_plan(manifest, bin_dir=bin_dir, plugin_dir=plugin_dir)
    cli = plan["cli"]
    plugin = plan["plugin"]
    desired = manifest["zjctl_version"]
    cli_target = Path(cli["target"])
    plugin_target = Path(plugin["target"])

    if cli_target.exists() and cli["target_version"] != desired and not replace:
        raise BootstrapError(
            "conflicting_installation",
            "Refusing to replace the existing CLI without --replace",
            target=str(cli_target),
            installed_version=cli["target_version"],
            desired_version=desired,
        )
    if (
        not cli_target.exists()
        and cli["path_executable"]
        and cli["path_version"] not in (None, desired)
        and not replace
    ):
        raise BootstrapError(
            "conflicting_installation",
            "PATH contains a different zjctl version; review it before installing the pinned version",
            path=cli["path_executable"],
            installed_version=cli["path_version"],
            desired_version=desired,
        )
    if (
        plugin_target.exists()
        and plugin["installed_sha256"] != plugin["sha256"]
        and not replace
    ):
        raise BootstrapError(
            "conflicting_installation",
            "Refusing to replace the existing zrpc.wasm without --replace",
            target=str(plugin_target),
            installed_sha256=plugin["installed_sha256"],
            desired_sha256=plugin["sha256"],
        )

    installed: list[str] = []
    skipped: list[str] = []
    with tempfile.TemporaryDirectory(prefix="zjctl-bootstrap-") as temporary:
        temporary_dir = Path(temporary)
        if cli["action"] == "install":
            archive = temporary_dir / "zjctl.tar.gz"
            _download(cli["url"], cli["sha256"], archive)
            cli_bytes = extract_cli_bytes(archive)
            candidate = temporary_dir / "zjctl"
            candidate.write_bytes(cli_bytes)
            candidate.chmod(0o755)
            installed_version = _version_of(candidate)
            if installed_version != desired:
                raise BootstrapError(
                    "version_mismatch",
                    "Downloaded CLI did not report the pinned version",
                    expected=desired,
                    actual=installed_version,
                )
            _atomic_install(cli_bytes, cli_target, 0o755)
            installed.append(str(cli_target))
        else:
            skipped.append(
                str(cli_target if cli_target.exists() else cli["path_executable"])
            )

        if plugin["action"] == "install":
            wasm = temporary_dir / "zrpc.wasm"
            _download(plugin["url"], plugin["sha256"], wasm)
            _atomic_install(wasm.read_bytes(), plugin_target, 0o644)
            installed.append(str(plugin_target))
        else:
            skipped.append(str(plugin_target))

    return {
        "pinned_zjctl_version": desired,
        "installed": installed,
        "skipped": skipped,
        "config_changed": False,
        "next_step": "Run bootstrap.py configure only after separate approval to edit Zellij config or load a session.",
    }


def config_file_path() -> Path:
    if value := os.environ.get("ZELLIJ_CONFIG_FILE"):
        return Path(value).expanduser()
    if value := os.environ.get("ZELLIJ_CONFIG_DIR"):
        return Path(value).expanduser() / "config.kdl"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "zellij" / "config.kdl"


def _find_load_plugins_block(contents: str) -> tuple[int, int, str] | None:
    match = re.search(r"(?m)^(?P<indent>[ \t]*)load_plugins[ \t]*\{", contents)
    if match is None:
        return None
    opening = contents.find("{", match.start(), match.end())
    depth = 0
    in_string = False
    escaped = False
    in_line_comment = False
    index = opening
    while index < len(contents):
        char = contents[index]
        next_char = contents[index + 1] if index + 1 < len(contents) else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            index += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            index += 2
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return opening, index, match.group("indent")
        index += 1
    raise BootstrapError("invalid_config", "Found an unterminated load_plugins block")


def ensure_plugin_entry(contents: str, plugin_url: str) -> tuple[str, bool]:
    quoted = json.dumps(plugin_url)
    if quoted in contents:
        return contents, False
    block = _find_load_plugins_block(contents)
    if block is None:
        separator = (
            "" if not contents else ("\n" if contents.endswith("\n") else "\n\n")
        )
        return f"{contents}{separator}load_plugins {{\n    {quoted}\n}}\n", True
    opening, closing, indent = block
    inner = contents[opening + 1 : closing]
    entry_indent = f"{indent}    "
    if not inner.strip():
        replacement = f"\n{entry_indent}{quoted}\n{indent}"
    else:
        newline = "" if inner.endswith("\n") else "\n"
        replacement = f"{inner}{newline}{entry_indent}{quoted}\n{indent}"
    updated = contents[: opening + 1] + replacement + contents[closing:]
    return updated, True


def _atomic_write(data: bytes, target: Path, mode: int | None = None) -> None:
    existing_mode = mode
    if existing_mode is None and target.exists():
        existing_mode = target.stat().st_mode & 0o777
    _atomic_install(data, target, existing_mode or 0o644)


def _zellij_binary() -> str:
    override = os.environ.get("ZELLIJ_BIN")
    found = override or shutil.which("zellij")
    if not found:
        raise BootstrapError(
            "missing_dependency", "zellij is required before configuring zrpc"
        )
    return found


def _exact_session_exists(zellij: str, session: str) -> bool:
    result = _run([zellij, "list-sessions", "--short", "--no-formatting"])
    if result.returncode != 0:
        raise BootstrapError(
            "command_failed",
            "Could not list Zellij sessions",
            stderr=result.stderr.strip(),
        )
    return session in {
        line.strip() for line in result.stdout.splitlines() if line.strip()
    }


def load_plugin(
    manifest: dict[str, Any],
    *,
    plugin_dir: str | None,
    session: str,
) -> dict[str, Any]:
    if not session or any(ord(char) < 32 or ord(char) == 127 for char in session):
        raise BootstrapError(
            "invalid_session", "Session name is empty or contains control characters"
        )
    _, plugin_path = _paths(None, plugin_dir)
    expected_digest = manifest["plugin"]["sha256"]
    if not plugin_path.is_file() or sha256_file(plugin_path) != expected_digest:
        raise BootstrapError(
            "plugin_not_installed",
            "Pinned zrpc.wasm is not installed at the expected path",
            target=str(plugin_path),
            expected_sha256=expected_digest,
        )
    zellij = _zellij_binary()
    if not _exact_session_exists(zellij, session):
        raise BootstrapError(
            "session_not_found", f"No exact Zellij session named {session!r}"
        )
    plugin_url = f"file:{plugin_path.resolve()}"
    launch = _run([zellij, "--session", session, "action", "launch-plugin", plugin_url])
    if launch.returncode != 0:
        raise BootstrapError(
            "plugin_load_failed",
            "zrpc could not be loaded in the requested session",
            session=session,
            stderr=launch.stderr.strip(),
        )
    return {
        "session": session,
        "plugin_url": plugin_url,
        "session_loaded": True,
        "config_changed": False,
    }


def configure(
    manifest: dict[str, Any],
    *,
    plugin_dir: str | None,
    apply: bool,
    session: str | None,
) -> dict[str, Any]:
    _, plugin_path = _paths(None, plugin_dir)
    expected_digest = manifest["plugin"]["sha256"]
    if not plugin_path.is_file() or sha256_file(plugin_path) != expected_digest:
        raise BootstrapError(
            "plugin_not_installed",
            "Pinned zrpc.wasm is not installed at the expected path",
            target=str(plugin_path),
            expected_sha256=expected_digest,
        )
    requested_config = config_file_path()
    target_config = requested_config.resolve(strict=False)
    contents = (
        target_config.read_text(encoding="utf-8") if target_config.exists() else ""
    )
    plugin_url = f"file:{plugin_path.resolve()}"
    updated, changed = ensure_plugin_entry(contents, plugin_url)
    result: dict[str, Any] = {
        "apply": apply,
        "requested_config_path": str(requested_config),
        "resolved_config_path": str(target_config),
        "plugin_url": plugin_url,
        "would_change_config": changed,
        "session": session,
    }
    if not apply:
        result["next_step"] = "Ask for approval, then repeat with --apply."
        return result

    zellij = _zellij_binary()
    backup: Path | None = None
    existed = target_config.exists()
    original = contents.encode("utf-8")
    if changed:
        target_config.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            backup = target_config.with_name(
                f"{target_config.name}.zjctl-backup-{timestamp}"
            )
            shutil.copy2(target_config, backup)
        _atomic_write(updated.encode("utf-8"), target_config)
        check = _run([zellij, "--config", str(target_config), "setup", "--check"])
        if check.returncode != 0:
            if existed:
                _atomic_write(original, target_config)
            else:
                target_config.unlink(missing_ok=True)
            raise BootstrapError(
                "config_validation_failed",
                "Zellij rejected the updated config; the original was restored",
                stderr=check.stderr.strip(),
                stdout=check.stdout.strip(),
                backup=str(backup) if backup else None,
            )
    if session is not None:
        if not session or any(ord(char) < 32 or ord(char) == 127 for char in session):
            raise BootstrapError(
                "invalid_session",
                "Session name is empty or contains control characters",
            )
        if not _exact_session_exists(zellij, session):
            raise BootstrapError(
                "session_not_found", f"No exact Zellij session named {session!r}"
            )
        launch = _run(
            [zellij, "--session", session, "action", "launch-plugin", plugin_url]
        )
        if launch.returncode != 0:
            raise BootstrapError(
                "plugin_load_failed",
                "Config was valid, but zrpc could not be loaded in the requested session",
                session=session,
                stderr=launch.stderr.strip(),
            )
    result.update(
        {
            "config_changed": changed,
            "backup": str(backup) if backup else None,
            "session_loaded": session is not None,
        }
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    plan = subcommands.add_parser(
        "plan", help="Show exact downloads and paths without changing anything"
    )
    plan.add_argument("--bin-dir")
    plan.add_argument("--plugin-dir")

    install_parser = subcommands.add_parser(
        "install", help="Install pinned, checksum-verified artifacts"
    )
    install_parser.add_argument("--bin-dir")
    install_parser.add_argument("--plugin-dir")
    install_parser.add_argument("--replace", action="store_true")

    load_parser = subcommands.add_parser(
        "load", help="Load the pinned plugin in one exact live session"
    )
    load_parser.add_argument("--plugin-dir")
    load_parser.add_argument("--session", required=True)

    configure_parser = subcommands.add_parser(
        "configure", help="Plan or apply zrpc auto-load configuration"
    )
    configure_parser.add_argument("--plugin-dir")
    configure_parser.add_argument(
        "--apply", action="store_true", help="Actually edit config.kdl"
    )
    configure_parser.add_argument(
        "--session", help="Also load zrpc into this exact live session"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        manifest = load_compatibility()
        if args.command == "plan":
            payload = installation_plan(
                manifest, bin_dir=args.bin_dir, plugin_dir=args.plugin_dir
            )
        elif args.command == "install":
            payload = install(
                manifest,
                bin_dir=args.bin_dir,
                plugin_dir=args.plugin_dir,
                replace=args.replace,
            )
        elif args.command == "load":
            payload = load_plugin(
                manifest, plugin_dir=args.plugin_dir, session=args.session
            )
        elif args.command == "configure":
            payload = configure(
                manifest,
                plugin_dir=args.plugin_dir,
                apply=args.apply,
                session=args.session,
            )
        else:  # pragma: no cover - argparse enforces this
            raise AssertionError(args.command)
        _json(payload)
        return 0
    except BootstrapError as exc:
        _json(
            {"error": {"code": exc.code, "message": str(exc), "details": exc.details}},
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
