# zjctl Agent Skill

A standalone agent skill for inspecting local [Zellij](https://zellij.dev/)
sessions and safely messaging terminal-based agents from Codex or Claude Code.

This repository is not a fork of `zjctl` and does not vendor its Rust source.
When the dependency is missing, the skill can propose a deterministic install
of a pinned release from the original
[`mrshu/zjctl`](https://github.com/mrshu/zjctl) project.

## What it can do

- list Zellij sessions and the tabs in one exact session;
- find terminal panes by tab, pane title, or foreground command;
- capture a bounded tail of the latest visible output;
- observe whether rendered output changes during a short interval;
- send an explicitly authorized message to a revalidated terminal pane.

Example requests:

- “List the tabs in my `project` session.”
- “Check whether the database migration work in `project` is still moving and
  show me the latest output.”
- “Send this agent the following message: implement X, Y, and Z.”

The wrapper never broadcasts input. It requires an exact session and terminal
pane ID, checks the expected tab and title again before writing, and presses
Enter only when the user explicitly authorizes submission.

## Requirements

- Python 3.10 or newer;
- Zellij 0.44 or newer;
- `zjctl` and its `zrpc.wasm` plugin.

Installing this skill does not silently install executables, load a plugin, or
edit `config.kdl`. On first use, the agent performs a read-only preflight. If
`zjctl` is missing, it shows the pinned version and destination paths and asks
for approval before invoking the included bootstrap.

The compatibility manifest currently pins `zjctl` v0.1.4 and verifies every
downloaded release asset with SHA-256. Installation, loading the plugin into a
named live session, and changing persistent Zellij configuration are three
separate approval steps.

Review the install plan manually with:

```bash
python3 skills/zellij-sessions/scripts/bootstrap.py plan
```

After approving the displayed paths and version:

```bash
python3 skills/zellij-sessions/scripts/bootstrap.py install
```

See [the skill instructions](skills/zellij-sessions/SKILL.md) for the complete
inspection, messaging, and bootstrap workflow.

## Install in Codex

After this repository is published, replace `OWNER` in the commands below:

```bash
codex plugin marketplace add OWNER/zjctl-agent-skill --ref main
codex plugin add zjctl-agent-skill@zjctl-agent-skill
```

To install only the skill, ask Codex's `$skill-installer` to install:

```text
https://github.com/OWNER/zjctl-agent-skill/tree/main/skills/zellij-sessions
```

When Codex runs directly in this checkout, it discovers the same canonical
skill through `.agents/skills/zellij-sessions`.

## Install in Claude Code

After publishing the repository:

```bash
claude plugin marketplace add OWNER/zjctl-agent-skill
claude plugin install zjctl-agent-skill@zjctl-agent-skill
```

For local development from this checkout:

```bash
claude --plugin-dir .
```

## Development

Run the portable unit tests and syntax checks:

```bash
python3 -m unittest discover -s tests/skill -p 'test_*.py' -v
python3 -m compileall -q skills tests
```

The test suite uses temporary files and fake subprocesses. It does not install
`zjctl`, modify the user's Zellij configuration, or send input to a live pane.

## Upstream dependency

`zjctl` is maintained independently by its upstream authors. This repository
only contains the agent-facing workflow, safety wrapper, deterministic
bootstrap, packaging metadata, and tests. See the
[`mrshu/zjctl` repository](https://github.com/mrshu/zjctl) for the CLI and
Zellij plugin source and license.

## License

The skill repository is available under the [MIT License](LICENSE).
