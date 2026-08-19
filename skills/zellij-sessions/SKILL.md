---
name: zellij-sessions
description: Inspect local Zellij sessions and interact with terminal-based agents through zjctl. Use to list sessions or tabs, find work by topic, report the latest visible output or activity, and send an explicitly authorized message to a verified agent pane.
---

# Zellij sessions

Use the bundled scripts to inspect an exact local Zellij session and, when the user explicitly asks, write to one verified terminal pane. Keep observations and interpretations separate: changing terminal output is evidence of activity, not proof that useful work is progressing; unchanged output is not proof that a process is stuck or finished.

## Use the portable wrapper

Resolve this skill's directory from the loaded `SKILL.md`, then invoke. In a Claude plugin install, it is `${CLAUDE_PLUGIN_ROOT}/skills/zellij-sessions`; otherwise use the path associated with the loaded skill:

```text
python3 <skill-directory>/scripts/zellij_sessions.py <command> ...
```

Do not substitute raw `zellij` keystrokes or broad `zjctl` selectors for supported operations. The wrapper provides exact session scoping, bounded capture, per-session locking, terminal-pane validation, and JSON output.

Start with a read-only preflight for the named session:

```text
python3 <skill-directory>/scripts/zellij_sessions.py doctor --session elephc
```

If no session was named, list exact names first:

```text
python3 <skill-directory>/scripts/zellij_sessions.py sessions
```

## Inspect sessions, tabs, and work

List tabs in one exact session:

```text
python3 <skill-directory>/scripts/zellij_sessions.py tabs --session elephc
```

Find candidate terminal panes by tab name, pane title, or foreground command:

```text
python3 <skill-directory>/scripts/zellij_sessions.py find --session elephc --query MySQLi
```

Use only explicit IDs such as `terminal:42`. Never use `focused`, title selectors, regular expressions, plugin panes, or an all-panes operation for capture or sending. If search returns multiple plausible candidates, inspect their bounded output and ask the user only if the target remains ambiguous.

Capture the latest visible output, bounded to the last 80 lines and 64 KiB by default:

```text
python3 <skill-directory>/scripts/zellij_sessions.py capture --session elephc --pane terminal:42 --expect-tab MySQLi --expect-title Agent
```

Use `--full` only when the user explicitly asks for scrollback or visible output is insufficient. Even with `--full`, the wrapper applies the line and byte limits.

To check whether rendered output changes over a short window:

```text
python3 <skill-directory>/scripts/zellij_sessions.py activity --session elephc --pane terminal:42 --expect-tab MySQLi --expect-title Agent --interval 4
```

Report the exact session, tab, pane ID, the relevant tail of output, and whether it changed during the observation window. Then interpret the content cautiously. Say “the rendered output changed” or “did not change”; do not equate either result with progress, completion, failure, or process liveness without supporting output.

Terminal captures can contain secrets or private data. Return only the minimum relevant excerpt, never persist captures, and do not include unrelated tokens, credentials, or environment values in the response.

## Send a message only when authorized

A request such as “scrivi all'agente di fare X” authorizes sending that exact message to the already identified target. Reuse the prior target from the conversation only after revalidating its pane ID, title, and tab. If the target or intended text changed or is ambiguous, stop and ask.

Submit an explicitly authorized message with the identity values returned by the latest inventory:

```text
python3 <skill-directory>/scripts/zellij_sessions.py send \
  --session elephc \
  --pane terminal:42 \
  --expect-tab MySQLi \
  --expect-title 'Agent' \
  --text 'Fai X, Y e Z.' \
  --submit
```

Omit `--submit` when the user asks to draft or type without pressing Enter. The wrapper refuses non-terminal panes, non-explicit IDs, missing panes, and title/tab identity changes. It never broadcasts. After submission, capture the pane again when useful to confirm that the message appeared or the agent acknowledged it; do not claim acceptance based only on a successful write.

## Install the dependency deterministically

Normal inspection does not authorize software installation, replacing binaries, loading a plugin, or editing Zellij configuration.

If `doctor` reports that `zjctl` is missing, run the non-mutating plan:

```text
python3 <skill-directory>/scripts/bootstrap.py plan
```

Show the user the pinned version, exact destination paths, and whether existing files would be replaced. Ask for explicit approval. After approval, run:

```text
python3 <skill-directory>/scripts/bootstrap.py install
```

The bootstrap downloads versioned release assets listed in `compatibility.json`, verifies SHA-256, extracts only the expected CLI file, and installs atomically. It never uses `latest`, `curl | sh`, or an unapproved source build. It refuses unsupported platforms and conflicting files unless the user separately approves replacement with `--replace`.

Installation does not edit `config.kdl`. If the plugin must be loaded into one existing session, ask for approval scoped to that session, then run:

```text
python3 <skill-directory>/scripts/bootstrap.py load --session elephc
```

For persistent auto-load, first preview the exact resolved config path without mutation:

```text
python3 <skill-directory>/scripts/bootstrap.py configure
```

Ask for separate approval before applying:

```text
python3 <skill-directory>/scripts/bootstrap.py configure --apply
```

The configure command preserves a symlinked config target, adds to an existing `load_plugins` block when present, creates a timestamped backup, validates with `zellij setup --check`, and restores the prior file if validation fails. Existing Zellij sessions do not inherit a config change; load the plugin into a named live session separately when requested.

Re-run `doctor --session <name>` after installation or plugin activation before continuing.
