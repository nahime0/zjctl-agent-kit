#!/usr/bin/env python3
"""Safe, machine-readable helpers for inspecting and messaging Zellij panes."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

DEFAULT_LINES = 80
MAX_LINES = 1_000
MAX_CAPTURE_BYTES = 65_536
COMMAND_TIMEOUT_SECONDS = 30
POST_SUBMIT_CAPTURE_DELAY_SECONDS = 0.25
SUBMITTED_MEANS = (
    "A Zellij Enter key event was sent; receipt or acceptance is not confirmed."
)
PANE_ID_RE = re.compile(r"^terminal:[0-9]+$")
COMPATIBILITY_PATH = Path(__file__).resolve().parent.parent / "compatibility.json"


class SkillError(RuntimeError):
    """An expected error that should be shown to the calling agent."""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _json(data: Any, *, stream: Any = sys.stdout) -> None:
    json.dump(data, stream, ensure_ascii=False, indent=2, sort_keys=True)
    stream.write("\n")


def _command_env(session: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    if session is not None:
        env["ZELLIJ_SESSION_NAME"] = session
    return env


def _default_local_bin(name: str) -> str | None:
    path = Path.home() / ".local" / "bin" / name
    return str(path) if path.is_file() and os.access(path, os.X_OK) else None


def _binary(name: str, override_env: str) -> str:
    override = os.environ.get(override_env)
    if override:
        return override
    if name == "zjctl":
        local = _default_local_bin(name)
        if local:
            return local
    found = shutil.which(name)
    if found:
        return found
    raise SkillError(
        "missing_dependency",
        f"Required executable not found: {name}",
        executable=name,
        bootstrap=(
            "Run bootstrap.py plan, ask for explicit installation approval, "
            "then run bootstrap.py install"
            if name == "zjctl"
            else None
        ),
    )


def _run(
    args: Sequence[str],
    *,
    session: str | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    try:
        return subprocess.run(
            list(args),
            env=_command_env(session),
            capture_output=True,
            check=False,
            text=text,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SkillError(
            "command_failed",
            f"Could not run {Path(args[0]).name}: {exc}",
            command=list(args),
        ) from exc


def _run_checked(
    args: Sequence[str],
    *,
    session: str | None = None,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
    text: bool = True,
) -> subprocess.CompletedProcess[Any]:
    result = _run(args, session=session, timeout=timeout, text=text)
    if result.returncode != 0:
        stderr = (
            result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        )
        raise SkillError(
            "command_failed",
            f"{Path(args[0]).name} exited with status {result.returncode}",
            command=list(args),
            returncode=result.returncode,
            stderr=stderr.strip(),
        )
    return result


def _validate_session_name(session: str) -> str:
    if not session or any(ord(char) < 32 or ord(char) == 127 for char in session):
        raise SkillError(
            "invalid_session", "Session name is empty or contains control characters"
        )
    return session


def _version_tuple(output: str) -> tuple[int, int, int] | None:
    match = re.search(r"(?:^|\s)([0-9]+)\.([0-9]+)\.([0-9]+)(?:\s|$)", output)
    return tuple(int(part) for part in match.groups()) if match else None


def _minimum_zellij_version() -> str:
    try:
        manifest = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
        minimum = manifest["zellij_min_version"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise SkillError(
            "invalid_manifest", f"Cannot read Zellij compatibility metadata: {exc}"
        ) from exc
    if not isinstance(minimum, str) or _version_tuple(minimum) is None:
        raise SkillError(
            "invalid_manifest", "zellij_min_version is not a semantic version"
        )
    return minimum


def _expected_zjctl_version() -> str:
    try:
        manifest = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
        expected = manifest["zjctl_version"]
    except (OSError, KeyError, json.JSONDecodeError) as exc:
        raise SkillError(
            "invalid_manifest", f"Cannot read zjctl compatibility metadata: {exc}"
        ) from exc
    if not isinstance(expected, str) or _version_tuple(expected) is None:
        raise SkillError("invalid_manifest", "zjctl_version is not a semantic version")
    return expected


def list_sessions() -> list[str]:
    zellij = _binary("zellij", "ZELLIJ_BIN")
    result = _run_checked([zellij, "list-sessions", "--short", "--no-formatting"])
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def require_session(session: str) -> None:
    session = _validate_session_name(session)
    sessions = list_sessions()
    if session not in sessions:
        raise SkillError(
            "session_not_found",
            f"No exact Zellij session named {session!r}",
            session=session,
            available_sessions=sessions,
        )


def list_panes(session: str) -> list[dict[str, Any]]:
    require_session(session)
    zjctl = _binary("zjctl", "ZJCTL_BIN")
    result = _run_checked([zjctl, "panes", "ls", "--json"], session=session)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SkillError(
            "invalid_zjctl_output",
            "zjctl returned invalid JSON while listing panes",
            stderr=result.stderr.strip(),
        ) from exc
    if not isinstance(payload, list) or not all(
        isinstance(item, dict) for item in payload
    ):
        raise SkillError(
            "invalid_zjctl_output", "zjctl pane inventory was not a JSON array"
        )
    return payload


def summarize_tabs(session: str) -> dict[str, Any]:
    panes = list_panes(session)
    tabs: dict[tuple[int, str], dict[str, Any]] = {}
    for pane in panes:
        tab_index = int(pane.get("tab_index", 0))
        tab_name = str(pane.get("tab_name", ""))
        key = (tab_index, tab_name)
        tab = tabs.setdefault(
            key,
            {
                "tab_index": tab_index,
                "tab_name": tab_name,
                "focused": False,
                "pane_count": 0,
                "terminal_pane_count": 0,
            },
        )
        tab["pane_count"] += 1
        tab["terminal_pane_count"] += int(pane.get("pane_type") == "terminal")
        tab["focused"] = tab["focused"] or bool(pane.get("focused"))
    return {
        "session": session,
        "tabs": sorted(
            tabs.values(), key=lambda tab: (tab["tab_index"], tab["tab_name"])
        ),
    }


def _match_fields(pane: dict[str, Any], query: str) -> tuple[int, list[str]]:
    needle = query.casefold()
    score = 0
    matches: list[str] = []
    weights = {"tab_name": 40, "title": 30, "command": 20}
    for field, weight in weights.items():
        value = str(pane.get(field) or "")
        folded = value.casefold()
        if folded == needle:
            score += weight + 100
            matches.append(field)
        elif needle in folded:
            score += weight
            matches.append(field)
    return score, matches


def find_panes(session: str, query: str) -> dict[str, Any]:
    if not query.strip():
        raise SkillError("invalid_query", "Search query cannot be empty")
    candidates: list[dict[str, Any]] = []
    for pane in list_panes(session):
        if pane.get("pane_type") != "terminal":
            continue
        score, matched_fields = _match_fields(pane, query)
        if score:
            candidate = dict(pane)
            candidate["score"] = score
            candidate["matched_fields"] = matched_fields
            candidates.append(candidate)
    candidates.sort(
        key=lambda item: (-item["score"], item.get("tab_index", 0), item.get("id", ""))
    )
    return {
        "session": session,
        "query": query,
        "candidate_count": len(candidates),
        "unique": len(candidates) == 1,
        "candidates": candidates,
    }


def _require_terminal_pane(
    session: str,
    pane_id: str,
    *,
    expect_title: str | None = None,
    expect_tab: str | None = None,
) -> dict[str, Any]:
    if not PANE_ID_RE.fullmatch(pane_id):
        raise SkillError(
            "unsafe_pane_selector",
            "Pane must be an explicit terminal ID such as terminal:42",
            pane=pane_id,
        )
    pane = next(
        (item for item in list_panes(session) if item.get("id") == pane_id), None
    )
    if pane is None:
        raise SkillError(
            "pane_not_found", f"Pane {pane_id!r} no longer exists", session=session
        )
    if pane.get("pane_type") != "terminal":
        raise SkillError(
            "unsafe_pane_selector", "Refusing to target a non-terminal pane", pane=pane
        )
    if expect_title is not None and pane.get("title") != expect_title:
        raise SkillError(
            "pane_identity_changed",
            "Pane title changed since it was selected",
            expected_title=expect_title,
            actual_title=pane.get("title"),
            pane=pane,
        )
    if expect_tab is not None and pane.get("tab_name") != expect_tab:
        raise SkillError(
            "pane_identity_changed",
            "Pane moved to or was replaced in a different tab",
            expected_tab=expect_tab,
            actual_tab=pane.get("tab_name"),
            pane=pane,
        )
    return pane


def _zellij_pane_id(pane_id: str) -> str:
    if not PANE_ID_RE.fullmatch(pane_id):
        raise SkillError(
            "unsafe_pane_selector",
            "Pane must be an explicit terminal ID such as terminal:42",
            pane=pane_id,
        )
    return pane_id.replace(":", "_", 1)


@contextlib.contextmanager
def _session_lock(session: str) -> Iterator[None]:
    digest = hashlib.sha256(session.encode("utf-8")).hexdigest()[:24]
    lock_dir = Path(tempfile.gettempdir()) / f"zjctl-skill-{os.getuid()}"
    lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = lock_dir / f"{digest}.lock"
    with lock_path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _bounded_output(raw: bytes, lines: int) -> tuple[str, bool]:
    if not 1 <= lines <= MAX_LINES:
        raise SkillError("invalid_lines", f"lines must be between 1 and {MAX_LINES}")
    truncated = len(raw) > MAX_CAPTURE_BYTES
    if truncated:
        raw = raw[-MAX_CAPTURE_BYTES:]
    text = raw.decode("utf-8", errors="replace")
    chunks = text.splitlines()
    if len(chunks) > lines:
        chunks = chunks[-lines:]
        truncated = True
    return "\n".join(chunks), truncated


def _capture_locked(
    session: str,
    pane_id: str,
    lines: int,
    full: bool,
    *,
    expect_title: str | None = None,
    expect_tab: str | None = None,
) -> dict[str, Any]:
    pane = _require_terminal_pane(
        session,
        pane_id,
        expect_title=expect_title,
        expect_tab=expect_tab,
    )
    zjctl = _binary("zjctl", "ZJCTL_BIN")
    args = [zjctl, "pane", "capture", "--pane", f"id:{pane_id}"]
    if full:
        args.append("--full")
    result = _run_checked(args, session=session, text=False)
    output, truncated = _bounded_output(result.stdout, lines)
    return {
        "session": session,
        "pane": pane,
        "scope": "full_scrollback" if full else "visible_viewport",
        "line_limit": lines,
        "byte_limit": MAX_CAPTURE_BYTES,
        "truncated": truncated,
        "output": output,
    }


def capture_pane(
    session: str,
    pane_id: str,
    lines: int,
    full: bool,
    *,
    expect_title: str | None = None,
    expect_tab: str | None = None,
) -> dict[str, Any]:
    with _session_lock(session):
        return _capture_locked(
            session,
            pane_id,
            lines,
            full,
            expect_title=expect_title,
            expect_tab=expect_tab,
        )


def observe_activity(
    session: str,
    pane_id: str,
    lines: int,
    full: bool,
    interval: float,
    *,
    expect_title: str | None = None,
    expect_tab: str | None = None,
) -> dict[str, Any]:
    if not 0.25 <= interval <= 60:
        raise SkillError(
            "invalid_interval", "interval must be between 0.25 and 60 seconds"
        )
    with _session_lock(session):
        before = _capture_locked(
            session,
            pane_id,
            lines,
            full,
            expect_title=expect_title,
            expect_tab=expect_tab,
        )
        time.sleep(interval)
        after = _capture_locked(
            session,
            pane_id,
            lines,
            full,
            expect_title=expect_title,
            expect_tab=expect_tab,
        )
    before_hash = hashlib.sha256(before["output"].encode("utf-8")).hexdigest()
    after_hash = hashlib.sha256(after["output"].encode("utf-8")).hexdigest()
    return {
        "session": session,
        "pane": after["pane"],
        "interval_seconds": interval,
        "changed": before_hash != after_hash,
        "interpretation": (
            "Rendered output changed during the observation window. This alone does not prove progress."
            if before_hash != after_hash
            else "Rendered output did not change during the observation window. This alone does not prove completion or a stall."
        ),
        "latest": after,
    }


def send_message(
    session: str,
    pane_id: str,
    text: str,
    *,
    submit: bool,
    expect_title: str | None,
    expect_tab: str | None,
) -> dict[str, Any]:
    if not text or "\x00" in text:
        raise SkillError(
            "invalid_message", "Message must be non-empty and cannot contain NUL"
        )
    if submit:
        text = text.rstrip("\r\n")
        if not text:
            raise SkillError(
                "invalid_message", "Submitted message cannot contain only newlines"
            )
    with _session_lock(session):
        pane = _require_terminal_pane(
            session,
            pane_id,
            expect_title=expect_title,
            expect_tab=expect_tab,
        )
        zjctl = _binary("zjctl", "ZJCTL_BIN")
        args = [
            zjctl,
            "pane",
            "send",
            "--pane",
            f"id:{pane_id}",
            "--enter=false",
            "--",
            text,
        ]
        _run_checked(args, session=session)

        after_submit = None
        if submit:
            pane = _send_enter_event_locked(
                session,
                pane_id,
                expect_title=expect_title,
                expect_tab=expect_tab,
            )
            after_submit = _capture_after_submit_locked(session, pane_id, pane)

    result = {
        "session": session,
        "pane": pane,
        "submitted": submit,
        "bytes_sent": len(text.encode("utf-8")),
    }
    if submit:
        result["submitted_means"] = SUBMITTED_MEANS
        result["after_submit"] = after_submit
    return result


def _send_enter_event_locked(
    session: str,
    pane_id: str,
    *,
    expect_title: str | None,
    expect_tab: str | None,
) -> dict[str, Any]:
    pane = _require_terminal_pane(
        session,
        pane_id,
        expect_title=expect_title,
        expect_tab=expect_tab,
    )
    zellij = _binary("zellij", "ZELLIJ_BIN")
    _run_checked(
        [
            zellij,
            "--session",
            session,
            "action",
            "send-keys",
            "--pane-id",
            _zellij_pane_id(pane_id),
            "Enter",
        ],
        session=session,
    )
    return pane


def _capture_after_submit_locked(
    session: str, pane_id: str, pane: dict[str, Any]
) -> dict[str, Any]:
    time.sleep(POST_SUBMIT_CAPTURE_DELAY_SECONDS)
    tab_name = pane.get("tab_name")
    try:
        capture = _capture_locked(
            session,
            pane_id,
            DEFAULT_LINES,
            False,
            expect_title=None,
            expect_tab=tab_name if isinstance(tab_name, str) else None,
        )
    except SkillError as exc:
        return {
            "attempted": True,
            "succeeded": False,
            "error": {
                "code": exc.code,
                "message": str(exc),
                "details": exc.details,
            },
        }
    return {"attempted": True, "succeeded": True, "capture": capture}


def submit_only(
    session: str,
    pane_id: str,
    *,
    expect_title: str | None,
    expect_tab: str | None,
) -> dict[str, Any]:
    with _session_lock(session):
        pane = _send_enter_event_locked(
            session,
            pane_id,
            expect_title=expect_title,
            expect_tab=expect_tab,
        )
        after_submit = _capture_after_submit_locked(session, pane_id, pane)
    return {
        "session": session,
        "pane": pane,
        "submitted": True,
        "submitted_means": SUBMITTED_MEANS,
        "bytes_sent": 0,
        "after_submit": after_submit,
    }


def doctor(session: str | None) -> dict[str, Any]:
    report: dict[str, Any] = {"ready": False, "session": session}
    try:
        zellij = _binary("zellij", "ZELLIJ_BIN")
        version = _run_checked([zellij, "--version"]).stdout.strip()
        minimum = _minimum_zellij_version()
        parsed = _version_tuple(version)
        compatible = parsed is not None and parsed >= _version_tuple(minimum)
        report["zellij"] = {
            "available": True,
            "path": zellij,
            "version": version,
            "minimum_version": minimum,
            "compatible": compatible,
        }
        if not compatible:
            report["zellij"]["error"] = f"Zellij {minimum} or newer is required"
            return report
    except SkillError as exc:
        report["zellij"] = {"available": False, "error": str(exc)}
        return report
    try:
        zjctl = _binary("zjctl", "ZJCTL_BIN")
        version = _run_checked([zjctl, "--version"]).stdout.strip()
        expected = _expected_zjctl_version()
        compatible = _version_tuple(version) == _version_tuple(expected)
        report["zjctl"] = {
            "available": True,
            "path": zjctl,
            "version": version,
            "expected_version": expected,
            "compatible": compatible,
        }
        if not compatible:
            report["zjctl"]["error"] = f"This skill pins zjctl {expected}"
            report["zjctl"]["next_step"] = (
                "Run bootstrap.py plan and ask before replacing zjctl"
            )
            return report
    except SkillError as exc:
        report["zjctl"] = {
            "available": False,
            "error": str(exc),
            "next_step": "Run bootstrap.py plan and ask before installing",
        }
        return report
    if session is None:
        report["ready"] = True
        return report
    require_session(session)
    result = _run([zjctl, "doctor", "--json"], session=session)
    try:
        diagnostics = json.loads(result.stdout) if result.stdout.strip() else None
    except json.JSONDecodeError:
        diagnostics = {"stdout": result.stdout.strip()}
    report["plugin"] = {
        "available": result.returncode == 0,
        "diagnostics": diagnostics,
        "stderr": result.stderr.strip(),
    }
    report["ready"] = result.returncode == 0
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("sessions", help="List exact Zellij session names")

    doctor_parser = subcommands.add_parser(
        "doctor", help="Check dependencies and optional session setup"
    )
    doctor_parser.add_argument("--session")

    tabs_parser = subcommands.add_parser("tabs", help="List tabs in one exact session")
    tabs_parser.add_argument("--session", required=True)

    find_parser = subcommands.add_parser(
        "find", help="Find terminal panes by tab, title, or command"
    )
    find_parser.add_argument("--session", required=True)
    find_parser.add_argument("--query", required=True)

    capture_parser = subcommands.add_parser(
        "capture", help="Capture bounded output from one terminal pane"
    )
    capture_parser.add_argument("--session", required=True)
    capture_parser.add_argument("--pane", required=True)
    capture_parser.add_argument("--lines", type=int, default=DEFAULT_LINES)
    capture_parser.add_argument(
        "--full", action="store_true", help="Include scrollback before applying limits"
    )
    capture_parser.add_argument("--expect-title")
    capture_parser.add_argument("--expect-tab")

    activity_parser = subcommands.add_parser(
        "activity", help="Compare two bounded output snapshots"
    )
    activity_parser.add_argument("--session", required=True)
    activity_parser.add_argument("--pane", required=True)
    activity_parser.add_argument("--lines", type=int, default=DEFAULT_LINES)
    activity_parser.add_argument("--full", action="store_true")
    activity_parser.add_argument("--interval", type=float, default=4.0)
    activity_parser.add_argument("--expect-title")
    activity_parser.add_argument("--expect-tab")

    send_parser = subcommands.add_parser(
        "send", help="Write to one revalidated terminal pane"
    )
    send_parser.add_argument("--session", required=True)
    send_parser.add_argument("--pane", required=True)
    send_parser.add_argument("--text", required=True)
    send_parser.add_argument(
        "--submit", action="store_true", help="Press Enter after writing"
    )
    send_parser.add_argument("--expect-title")
    send_parser.add_argument("--expect-tab")

    submit_only_parser = subcommands.add_parser(
        "submit-only", help="Send Enter to one revalidated terminal pane"
    )
    submit_only_parser.add_argument("--session", required=True)
    submit_only_parser.add_argument("--pane", required=True)
    submit_only_parser.add_argument("--expect-title")
    submit_only_parser.add_argument("--expect-tab")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "sessions":
            payload = {"sessions": list_sessions()}
        elif args.command == "doctor":
            payload = doctor(args.session)
        elif args.command == "tabs":
            payload = summarize_tabs(args.session)
        elif args.command == "find":
            payload = find_panes(args.session, args.query)
        elif args.command == "capture":
            payload = capture_pane(
                args.session,
                args.pane,
                args.lines,
                args.full,
                expect_title=args.expect_title,
                expect_tab=args.expect_tab,
            )
        elif args.command == "activity":
            payload = observe_activity(
                args.session,
                args.pane,
                args.lines,
                args.full,
                args.interval,
                expect_title=args.expect_title,
                expect_tab=args.expect_tab,
            )
        elif args.command == "send":
            payload = send_message(
                args.session,
                args.pane,
                args.text,
                submit=args.submit,
                expect_title=args.expect_title,
                expect_tab=args.expect_tab,
            )
        elif args.command == "submit-only":
            payload = submit_only(
                args.session,
                args.pane,
                expect_title=args.expect_title,
                expect_tab=args.expect_tab,
            )
        else:  # pragma: no cover - argparse enforces this
            raise AssertionError(args.command)
        _json(payload)
        return 0
    except SkillError as exc:
        _json(
            {"error": {"code": exc.code, "message": str(exc), "details": exc.details}},
            stream=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
