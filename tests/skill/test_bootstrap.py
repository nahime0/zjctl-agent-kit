from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills" / "zellij-sessions" / "scripts" / "bootstrap.py"
SPEC = importlib.util.spec_from_file_location("zjctl_bootstrap", MODULE_PATH)
assert SPEC and SPEC.loader
bootstrap = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap)


class PlatformAndManifestTests(unittest.TestCase):
    def test_supported_platform_aliases(self) -> None:
        self.assertEqual(bootstrap.platform_key("Darwin", "arm64"), "darwin-arm64")
        self.assertEqual(bootstrap.platform_key("Linux", "amd64"), "linux-x86_64")

    def test_unsupported_platform_fails_closed(self) -> None:
        with self.assertRaises(bootstrap.BootstrapError) as raised:
            bootstrap.platform_key("Windows", "x86_64")
        self.assertEqual(raised.exception.code, "unsupported_platform")

    def test_release_manifest_has_only_versioned_urls_and_sha256(self) -> None:
        manifest = bootstrap.load_compatibility()
        self.assertIn(f"/v{manifest['zjctl_version']}", manifest["release_base_url"])
        self.assertNotIn("latest", manifest["release_base_url"])
        digests = [item["sha256"] for item in manifest["cli_artifacts"].values()]
        digests.append(manifest["plugin"]["sha256"])
        self.assertTrue(all(len(digest) == 64 for digest in digests))


class ArchiveAndDownloadTests(unittest.TestCase):
    def test_extracts_only_regular_cli_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "zjctl.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo("dist/zjctl")
                data = b"binary"
                info.size = len(data)
                bundle.addfile(info, io.BytesIO(data))
            self.assertEqual(bootstrap.extract_cli_bytes(archive), b"binary")

    def test_rejects_symlink_cli_member(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "zjctl.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo("zjctl")
                info.type = tarfile.SYMTYPE
                info.linkname = "/tmp/elsewhere"
                bundle.addfile(info)
            with self.assertRaises(bootstrap.BootstrapError) as raised:
                bootstrap.extract_cli_bytes(archive)
        self.assertEqual(raised.exception.code, "invalid_archive")

    def test_download_rejects_checksum_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "artifact"
            with (
                mock.patch.object(
                    bootstrap, "urlopen", return_value=io.BytesIO(b"wrong")
                ),
                self.assertRaises(bootstrap.BootstrapError) as raised,
            ):
                bootstrap._download(
                    "https://example.invalid/artifact", "0" * 64, destination
                )
        self.assertEqual(raised.exception.code, "checksum_mismatch")


class ConfigTests(unittest.TestCase):
    def test_adds_to_existing_load_plugins_block_and_is_idempotent(self) -> None:
        original = 'theme "default"\n\nload_plugins {\n    "file:/existing.wasm"\n}\n'
        updated, changed = bootstrap.ensure_plugin_entry(original, "file:/zrpc.wasm")
        second, changed_again = bootstrap.ensure_plugin_entry(
            updated, "file:/zrpc.wasm"
        )
        self.assertTrue(changed)
        self.assertFalse(changed_again)
        self.assertEqual(second.count("load_plugins"), 1)
        self.assertIn('    "file:/zrpc.wasm"', second)

    def test_creates_block_when_missing(self) -> None:
        updated, changed = bootstrap.ensure_plugin_entry(
            'theme "default"\n', "file:/zrpc.wasm"
        )
        self.assertTrue(changed)
        self.assertIn('load_plugins {\n    "file:/zrpc.wasm"\n}', updated)

    def test_configure_preserves_symlink_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin_dir = root / "plugins"
            plugin_dir.mkdir()
            plugin = plugin_dir / "zrpc.wasm"
            plugin.write_bytes(b"wasm")
            target = root / "real-config.kdl"
            target.write_text('theme "default"\n', encoding="utf-8")
            link = root / "config.kdl"
            link.symlink_to(target)
            manifest = {"plugin": {"sha256": hashlib.sha256(b"wasm").hexdigest()}}
            success = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
            with (
                mock.patch.dict(os.environ, {"ZELLIJ_CONFIG_FILE": str(link)}),
                mock.patch.object(bootstrap, "_zellij_binary", return_value="zellij"),
                mock.patch.object(bootstrap, "_run", return_value=success),
            ):
                result = bootstrap.configure(
                    manifest,
                    plugin_dir=str(plugin_dir),
                    apply=True,
                    session=None,
                )
            self.assertTrue(link.is_symlink())
            self.assertIn("zrpc.wasm", target.read_text(encoding="utf-8"))
            self.assertIsNotNone(result["backup"])
            self.assertTrue(Path(result["backup"]).is_file())

    def test_configure_rolls_back_invalid_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plugin_dir = root / "plugins"
            plugin_dir.mkdir()
            plugin = plugin_dir / "zrpc.wasm"
            plugin.write_bytes(b"wasm")
            config = root / "config.kdl"
            original = 'theme "default"\n'
            config.write_text(original, encoding="utf-8")
            manifest = {"plugin": {"sha256": hashlib.sha256(b"wasm").hexdigest()}}
            failure = subprocess.CompletedProcess([], 1, stdout="", stderr="invalid")
            with (
                mock.patch.dict(os.environ, {"ZELLIJ_CONFIG_FILE": str(config)}),
                mock.patch.object(bootstrap, "_zellij_binary", return_value="zellij"),
                mock.patch.object(bootstrap, "_run", return_value=failure),
                self.assertRaises(bootstrap.BootstrapError) as raised,
            ):
                bootstrap.configure(
                    manifest,
                    plugin_dir=str(plugin_dir),
                    apply=True,
                    session=None,
                )
            self.assertEqual(raised.exception.code, "config_validation_failed")
            self.assertEqual(config.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
