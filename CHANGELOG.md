# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.1] - 2026-08-19

### Added

- Cross-client `zellij-sessions` skill for Codex and Claude Code.
- Safe JSON wrapper for session and tab inventory, pane discovery, bounded
  output capture, rendered-activity observation, and explicitly authorized
  messaging to a revalidated terminal pane.
- Pinned, checksum-verified bootstrap for the original `mrshu/zjctl` v0.1.4
  release, with separately authorized installation, live-session loading, and
  persistent Zellij configuration.
- Codex and Claude plugin manifests, local marketplace metadata, and Python
  tests.

### Changed

- Restyled the README header with an original mark, uniform project badges,
  and the project website link.

### Fixed

- Submit messages with a real, session- and pane-scoped Zellij `Enter` key
  event instead of a newline byte, after typing with Enter disabled and
  revalidating the target pane.
- Added guarded `submit-only` support and a mandatory bounded capture attempt
  after each submit event. A successful event dispatch is reported separately
  from application receipt or acceptance.
