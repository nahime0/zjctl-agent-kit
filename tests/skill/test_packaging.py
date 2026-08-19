from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class PackagingTests(unittest.TestCase):
    def test_codex_manifest_points_to_canonical_skills(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "zjctl-agent-skill")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertTrue((ROOT / manifest["skills"]).is_dir())

    def test_codex_marketplace_has_local_root_plugin_source_and_policy(self) -> None:
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(
                encoding="utf-8"
            )
        )
        plugin = marketplace["plugins"][0]
        self.assertEqual(marketplace["name"], "zjctl-agent-skill")
        self.assertEqual(plugin["name"], "zjctl-agent-skill")
        self.assertEqual(plugin["source"]["source"], "local")
        self.assertEqual(plugin["source"]["path"], "./")
        self.assertEqual(plugin["policy"]["installation"], "AVAILABLE")
        self.assertEqual(plugin["policy"]["authentication"], "ON_INSTALL")
        self.assertEqual(plugin["category"], "Productivity")

    def test_claude_manifest_and_marketplace_share_plugin_name(self) -> None:
        manifest = json.loads(
            (ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        marketplace = json.loads(
            (ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "zjctl-agent-skill")
        self.assertEqual(marketplace["plugins"][0]["name"], manifest["name"])
        self.assertEqual(marketplace["plugins"][0]["source"], "./")

    def test_repo_local_codex_link_resolves_to_canonical_skill(self) -> None:
        link = ROOT / ".agents" / "skills" / "zellij-sessions"
        canonical = ROOT / "skills" / "zellij-sessions"
        self.assertTrue(link.is_symlink())
        self.assertEqual(link.resolve(), canonical.resolve())

    def test_skill_frontmatter_has_portable_required_fields(self) -> None:
        contents = (ROOT / "skills" / "zellij-sessions" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertTrue(contents.startswith("---\n"))
        frontmatter = contents.split("---\n", 2)[1]
        self.assertIn("name: zellij-sessions\n", frontmatter)
        self.assertIn("description:", frontmatter)
        self.assertNotIn("allowed-tools:", frontmatter)


if __name__ == "__main__":
    unittest.main()
