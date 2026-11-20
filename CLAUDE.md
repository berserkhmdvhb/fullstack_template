@AGENTS.md

<!-- Claude Code bridge. AGENTS.md is the source of truth for ALL agents (Codex, Cursor, Copilot,
     Gemini, Claude). This file adds only what is Claude-Code-specific — nothing else, so there is
     one source of truth, not two drifting copies. -->

## Claude Code specifics

- **Scoped skills are NOT auto-loaded.** This repo has no `.claude/` directory, so the skills under
  `agent/skills/` (`add-supply-chain-scanner`, `quality-gates`) are a **transfer bundle**, not
  session config. To use one:
  - Install it into your user skills dir once: `cp -r agent/skills/<skill> ~/.claude/skills/`, or
  - Invoke it by name in a session: `/skill <skill>` (e.g. `/skill quality-gates`,
    `/skill add-supply-chain-scanner`).
- Everything else you need — commands, mandatory rules, pitfalls, where depth lives — is in
  [`AGENTS.md`](./AGENTS.md). Start there.
