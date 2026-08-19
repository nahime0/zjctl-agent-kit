# Agent instructions

- Keep this repository standalone: do not vendor or copy the upstream `zjctl`
  Rust workspace.
- Pin dependency releases and SHA-256 checksums in
  `skills/zellij-sessions/compatibility.json`; never use a `latest` download.
- Treat installing `zjctl`, loading `zrpc.wasm`, editing Zellij configuration,
  and sending input to a pane as separate user-authorized actions.
- Keep tests isolated from live Zellij sessions and the user's real
  configuration.
- Run the Python unit tests, syntax checks, skill validator, Codex plugin
  validator, and Claude plugin validator before release.
- Use Conventional Commits and update `CHANGELOG.md` for notable changes.
