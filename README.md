<p align="center">
  <img src="assets/logo-mark.png" alt="zjctl Agent Kit logo" width="130">
</p>

<h1 align="center">zjctl Agent Kit</h1>

<p align="center">
  <em>See every Zellij session. Guide the right agent.</em>
</p>

<p align="center">
  <a href="https://github.com/nahime0/zjctl-agent-kit/stargazers"><img src="https://img.shields.io/github/stars/nahime0/zjctl-agent-kit?style=flat-square&amp;logo=github&amp;logoColor=white&amp;label=stars&amp;color=7C3AED" alt="GitHub stars"></a>
  <a href="https://github.com/nahime0/zjctl-agent-kit/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/nahime0/zjctl-agent-kit/ci.yml?branch=main&amp;style=flat-square&amp;logo=githubactions&amp;logoColor=white&amp;label=CI&amp;color=7C3AED" alt="CI status"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/nahime0/zjctl-agent-kit?style=flat-square&amp;color=7C3AED" alt="License: MIT"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.10%2B-7C3AED?style=flat-square&amp;logo=python&amp;logoColor=white" alt="Python 3.10 or newer"></a>
</p>

<p align="center">
  <strong>Codex &middot; Claude Code &middot; Deterministic bootstrap &middot; Guarded pane writes</strong>
</p>

<p align="center">
  A portable agent skill for inspecting exact <a href="https://zellij.dev/">Zellij</a> sessions,<br>
  reading bounded pane output, observing activity, and sending user-approved instructions.
</p>

<p align="center">
  <a href="https://opensource.nahi.me"><strong>opensource.nahi.me</strong></a>
</p>

---

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
pane ID and checks the expected tab and title again before writing. For an
authorized submission, it types with Enter disabled, revalidates the target,
sends a separate Zellij `Enter` key event, and captures the pane afterward.
`submitted: true` means the key event was sent, not that the terminal
application accepted or acted on the instruction.

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

Add this repository as a marketplace, then install the plugin:

```bash
codex plugin marketplace add nahime0/zjctl-agent-kit --ref main
codex plugin add zjctl-agent-skill@zjctl-agent-skill
```

To install only the skill, ask Codex's `$skill-installer` to install:

```text
https://github.com/nahime0/zjctl-agent-kit/tree/main/skills/zellij-sessions
```

When Codex runs directly in this checkout, it discovers the same canonical
skill through `.agents/skills/zellij-sessions`.

## Install in Claude Code

Add the repository marketplace and install the plugin:

```bash
claude plugin marketplace add nahime0/zjctl-agent-kit
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
