from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills" / "zellij-sessions" / "scripts" / "zellij_sessions.py"
SPEC = importlib.util.spec_from_file_location("zellij_sessions", MODULE_PATH)
assert SPEC and SPEC.loader
zellij_sessions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(zellij_sessions)


def pane(
    pane_id: str,
    *,
    tab: str = "MySQLi",
    title: str = "Claude",
    command: str = "claude",
    tab_index: int = 1,
    focused: bool = False,
    pane_type: str = "terminal",
) -> dict[str, object]:
    return {
        "id": pane_id,
        "pane_type": pane_type,
        "title": title,
        "command": command,
        "tab_index": tab_index,
        "tab_name": tab,
        "focused": focused,
        "floating": False,
        "suppressed": False,
        "rows": 40,
        "cols": 120,
    }


class InventoryTests(unittest.TestCase):
    def test_version_parser_handles_zellij_output(self) -> None:
        self.assertEqual(zellij_sessions._version_tuple("zellij 0.44.3"), (0, 44, 3))
        self.assertIsNone(zellij_sessions._version_tuple("unknown"))

    def test_tabs_are_deduplicated_and_sorted(self) -> None:
        panes = [
            pane("terminal:8", tab="Tests", tab_index=2),
            pane("terminal:3", tab="MySQLi", tab_index=1, focused=True),
            pane("plugin:9", tab="MySQLi", tab_index=1, pane_type="plugin"),
        ]
        with mock.patch.object(zellij_sessions, "list_panes", return_value=panes):
            result = zellij_sessions.summarize_tabs("elephc")
        self.assertEqual(
            [tab["tab_name"] for tab in result["tabs"]], ["MySQLi", "Tests"]
        )
        self.assertEqual(result["tabs"][0]["pane_count"], 2)
        self.assertEqual(result["tabs"][0]["terminal_pane_count"], 1)
        self.assertTrue(result["tabs"][0]["focused"])

    def test_find_matches_case_insensitively_and_ignores_plugins(self) -> None:
        panes = [
            pane("terminal:3", tab="MySQLi implementation", command="codex"),
            pane("terminal:4", tab="Other", title="mysqli review", command="claude"),
            pane("plugin:5", tab="MySQLi", pane_type="plugin"),
        ]
        with mock.patch.object(zellij_sessions, "list_panes", return_value=panes):
            result = zellij_sessions.find_panes("elephc", "MYSQLI")
        self.assertEqual(
            [item["id"] for item in result["candidates"]], ["terminal:3", "terminal:4"]
        )
        self.assertFalse(result["unique"])


class CaptureAndSendTests(unittest.TestCase):
    def test_capture_is_bounded_to_latest_lines(self) -> None:
        raw = b"one\ntwo\nthree\nfour\n"
        output, truncated = zellij_sessions._bounded_output(raw, 2)
        self.assertEqual(output, "three\nfour")
        self.assertTrue(truncated)

    def test_capture_refuses_changed_tab_before_reading_output(self) -> None:
        target = pane("terminal:42", tab="Different", title="Agent")
        with (
            mock.patch.object(zellij_sessions, "list_panes", return_value=[target]),
            mock.patch.object(zellij_sessions, "_run_checked") as run,
            self.assertRaises(zellij_sessions.SkillError) as raised,
        ):
            zellij_sessions._capture_locked(
                "elephc",
                "terminal:42",
                80,
                False,
                expect_title="Agent",
                expect_tab="MySQLi",
            )
        self.assertEqual(raised.exception.code, "pane_identity_changed")
        run.assert_not_called()

    def test_rejects_ambiguous_selector_before_inventory(self) -> None:
        with (
            mock.patch.object(zellij_sessions, "list_panes") as inventory,
            self.assertRaises(zellij_sessions.SkillError) as raised,
        ):
            zellij_sessions._require_terminal_pane("elephc", "focused")
        self.assertEqual(raised.exception.code, "unsafe_pane_selector")
        inventory.assert_not_called()

    def test_send_revalidates_fingerprint_and_defaults_to_no_enter(self) -> None:
        target = pane("terminal:42", tab="MySQLi", title="Agent")
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            mock.patch.object(zellij_sessions, "list_panes", return_value=[target]),
            mock.patch.object(zellij_sessions, "_binary", return_value="/tmp/zjctl"),
            mock.patch.object(
                zellij_sessions, "_run_checked", return_value=completed
            ) as run,
        ):
            result = zellij_sessions.send_message(
                "elephc",
                "terminal:42",
                "Fai X, Y e Z",
                submit=False,
                expect_title="Agent",
                expect_tab="MySQLi",
            )
        args = run.call_args.args[0]
        self.assertEqual(
            args[:6],
            ["/tmp/zjctl", "pane", "send", "--pane", "id:terminal:42", "--enter=false"],
        )
        self.assertEqual(args[-2:], ["--", "Fai X, Y e Z"])
        self.assertFalse(result["submitted"])

    def test_send_submit_types_then_sends_enter_event_and_captures(self) -> None:
        target = pane("terminal:42", tab="MySQLi", title="Agent")
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        captured = {"output": "Working", "pane": target}
        with (
            mock.patch.object(
                zellij_sessions, "list_panes", return_value=[target]
            ) as inventory,
            mock.patch.object(
                zellij_sessions,
                "_binary",
                side_effect=lambda name, _override: f"/tmp/{name}",
            ),
            mock.patch.object(
                zellij_sessions, "_run_checked", return_value=completed
            ) as run,
            mock.patch.object(
                zellij_sessions, "_capture_locked", return_value=captured
            ) as capture,
            mock.patch.object(zellij_sessions.time, "sleep") as sleep,
        ):
            result = zellij_sessions.send_message(
                "elephc",
                "terminal:42",
                "Fai X, Y e Z",
                submit=True,
                expect_title="Agent",
                expect_tab="MySQLi",
            )

        self.assertEqual(inventory.call_count, 2)
        self.assertEqual(
            run.call_args_list[0].args[0],
            [
                "/tmp/zjctl",
                "pane",
                "send",
                "--pane",
                "id:terminal:42",
                "--enter=false",
                "--",
                "Fai X, Y e Z",
            ],
        )
        self.assertEqual(
            run.call_args_list[1].args[0],
            [
                "/tmp/zellij",
                "--session",
                "elephc",
                "action",
                "send-keys",
                "--pane-id",
                "terminal_42",
                "Enter",
            ],
        )
        sleep.assert_called_once_with(zellij_sessions.POST_SUBMIT_CAPTURE_DELAY_SECONDS)
        capture.assert_called_once_with(
            "elephc",
            "terminal:42",
            zellij_sessions.DEFAULT_LINES,
            False,
            expect_title=None,
            expect_tab="MySQLi",
        )
        self.assertTrue(result["submitted"])
        self.assertIn("not confirmed", result["submitted_means"])
        self.assertEqual(
            result["after_submit"],
            {"attempted": True, "succeeded": True, "capture": captured},
        )

    def test_send_submit_refuses_identity_change_after_typing(self) -> None:
        target = pane("terminal:42", tab="MySQLi", title="Agent")
        changed = pane("terminal:42", tab="MySQLi", title="Different")
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with (
            mock.patch.object(
                zellij_sessions,
                "list_panes",
                side_effect=[[target], [changed]],
            ),
            mock.patch.object(zellij_sessions, "_binary", return_value="/tmp/zjctl"),
            mock.patch.object(
                zellij_sessions, "_run_checked", return_value=completed
            ) as run,
            mock.patch.object(zellij_sessions, "_capture_locked") as capture,
            self.assertRaises(zellij_sessions.SkillError) as raised,
        ):
            zellij_sessions.send_message(
                "elephc",
                "terminal:42",
                "Fai X",
                submit=True,
                expect_title="Agent",
                expect_tab="MySQLi",
            )
        self.assertEqual(raised.exception.code, "pane_identity_changed")
        self.assertEqual(run.call_count, 1)
        capture.assert_not_called()

    def test_submit_only_sends_enter_and_mandatory_capture(self) -> None:
        target = pane("terminal:42", tab="MySQLi", title="Agent")
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        captured = {"output": "Working", "pane": target}
        with (
            mock.patch.object(zellij_sessions, "list_panes", return_value=[target]),
            mock.patch.object(zellij_sessions, "_binary", return_value="/tmp/zellij"),
            mock.patch.object(
                zellij_sessions, "_run_checked", return_value=completed
            ) as run,
            mock.patch.object(
                zellij_sessions, "_capture_locked", return_value=captured
            ) as capture,
            mock.patch.object(zellij_sessions.time, "sleep"),
        ):
            result = zellij_sessions.submit_only(
                "elephc",
                "terminal:42",
                expect_title="Agent",
                expect_tab="MySQLi",
            )

        self.assertEqual(
            run.call_args.args[0],
            [
                "/tmp/zellij",
                "--session",
                "elephc",
                "action",
                "send-keys",
                "--pane-id",
                "terminal_42",
                "Enter",
            ],
        )
        capture.assert_called_once()
        self.assertTrue(result["submitted"])
        self.assertEqual(result["bytes_sent"], 0)
        self.assertTrue(result["after_submit"]["succeeded"])

    def test_capture_failure_after_enter_does_not_hide_submitted_event(self) -> None:
        target = pane("terminal:42", tab="MySQLi", title="Agent")
        completed = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        capture_error = zellij_sessions.SkillError(
            "capture_failed", "Post-submit capture failed"
        )
        with (
            mock.patch.object(zellij_sessions, "list_panes", return_value=[target]),
            mock.patch.object(zellij_sessions, "_binary", return_value="/tmp/zellij"),
            mock.patch.object(
                zellij_sessions, "_run_checked", return_value=completed
            ) as run,
            mock.patch.object(
                zellij_sessions, "_capture_locked", side_effect=capture_error
            ),
            mock.patch.object(zellij_sessions.time, "sleep"),
        ):
            result = zellij_sessions.submit_only(
                "elephc",
                "terminal:42",
                expect_title="Agent",
                expect_tab="MySQLi",
            )

        self.assertEqual(run.call_count, 1)
        self.assertTrue(result["submitted"])
        self.assertFalse(result["after_submit"]["succeeded"])
        self.assertEqual(result["after_submit"]["error"]["code"], "capture_failed")

    def test_send_refuses_changed_title(self) -> None:
        target = pane("terminal:42", tab="MySQLi", title="Different")
        with (
            mock.patch.object(zellij_sessions, "list_panes", return_value=[target]),
            self.assertRaises(zellij_sessions.SkillError) as raised,
        ):
            zellij_sessions.send_message(
                "elephc",
                "terminal:42",
                "Fai X",
                submit=True,
                expect_title="Agent",
                expect_tab="MySQLi",
            )
        self.assertEqual(raised.exception.code, "pane_identity_changed")

    def test_activity_wording_does_not_claim_progress(self) -> None:
        first = {"output": "waiting", "pane": pane("terminal:42")}
        second = {"output": "working", "pane": pane("terminal:42")}
        with (
            mock.patch.object(
                zellij_sessions, "_capture_locked", side_effect=[first, second]
            ),
            mock.patch.object(zellij_sessions.time, "sleep"),
        ):
            result = zellij_sessions.observe_activity(
                "elephc", "terminal:42", 80, False, 1
            )
        self.assertTrue(result["changed"])
        self.assertIn("does not prove progress", result["interpretation"])


if __name__ == "__main__":
    unittest.main()
