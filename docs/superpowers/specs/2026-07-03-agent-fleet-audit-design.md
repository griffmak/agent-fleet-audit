# Agent Fleet Audit — Design

## Goal

A personal, retrospective self-audit tool for Griffin's Claude Code sessions. When a session dispatches multiple subagents (a common pattern in this setup), there's currently no visibility after the fact into what ran, how much each agent did, or whether its claims were self-verified versus asserted on faith. This tool answers that, on demand, for a given session.

Primary use case: personal self-audit only. Not built for client demos or real-time monitoring. Covers both cost/volume visibility (how many agents, how much work) and trust/verification visibility (did the agent check its own claims), since both are symptoms of the same "I lose track once I fan out" gap.

## Data sources (confirmed on disk, 2026-07-03)

- Parent session transcript: `~/.claude/projects/<project-slug>/<sessionId>.jsonl`
  Contains the parent's `Agent` tool_use blocks (description, subagent_type, prompt) paired with tool_result blocks (agentId).
- Subagent transcripts: `~/.claude/projects/<project-slug>/<sessionId>/subagents/agent-<agentId>.jsonl`
  Persistent (not the ephemeral `/private/tmp/.../tasks/*.output` symlink target — that symlink points here). Each line has `type`, `message`, `timestamp`, `agentId`, `sessionId`.

No new instrumentation is required. Both sources already persist without any change to hooks or settings.

## Architecture

A single dependency-free Python CLI script. No server, no database. Reads the JSONL sources above at invocation time and prints (or saves) a report.

Invocation: `python agent_audit.py` (defaults to the most recent session in the current project) or `python agent_audit.py --session <sessionId>` for a specific one. Optional `--save` writes the report to `reports/<sessionId>.md` instead of only printing.

## Components

1. **Session locator** — resolves a project slug + sessionId to the transcript path above. Defaults to the most recently modified `.jsonl` file in the current project's directory if `--session` isn't given.
2. **Dispatch extractor** — walks the parent transcript for `Agent` tool_use entries, capturing `description`, `subagent_type`, `prompt` (truncated for display), and the paired tool_result's `agentId`.
3. **Subagent analyzer** — for each agentId, loads its transcript (if present) and computes:
   - tool-call count, grouped by tool name
   - duration: last timestamp minus first timestamp
   - self-verification flag: `true` if the transcript contains a `WebFetch`, `WebSearch`, or `Bash` tool_use, else `false`
4. **Report renderer** — a markdown table: `Agent | Type | Duration | Tool calls | Self-verified? | Notes`. The `Notes` column is left blank for manual annotation (e.g. "cross-checked this one myself independently").

## Data flow

invoke → locate session file → extract dispatch list → for each dispatch, attempt to load its subagent transcript → aggregate stats → render markdown → print to stdout, and write to `reports/<sessionId>.md` if `--save` was passed.

## Error handling

- A subagent transcript that's missing or fails to parse: that row is rendered with `Notes = "transcript unavailable"` rather than aborting the run.
- A missing or unreadable main session transcript: hard error with a clear message to stderr, non-zero exit. No partial/silent output in this case, since without the parent transcript there's no dispatch list to report on at all.

## Testing

One `test_agent_audit.py` using a fixture directory with a fake session transcript referencing three fake subagent transcripts:
1. one containing a `WebFetch` call (expect self-verified = true)
2. one with no verification-tool calls (expect self-verified = false)
3. one whose file is absent (expect "transcript unavailable")

Asserts the rendered report contains the correct row for each case. No framework, no fixtures beyond the one test directory — matches the scope of the tool.

## Explicitly out of scope (v1)

- Real-time/live capture during a session (would require hooks — deferred; only build if this retrospective version proves useful and thin passive data becomes the blocker).
- Detecting whether the *parent* (not the subagent) independently cross-checked a subagent's claim afterward (the arXiv-paper scenario from 2026-07-03). This requires correlating topically-related tool calls after the fact, which is a fuzzy heuristic — the `Notes` column exists as the manual escape hatch for this instead of building unreliable auto-detection.
- Token/cost accounting — not confirmed to be reliably present in the local JSONL transcripts; if it turns out to be there, add later, not now.
- Packaging as a Claude Code skill/slash command — starts as a plain CLI script; wrap it in a skill later only if it gets used enough to be worth the extra layer.

## Location

New standalone repo: `~/dev/agent-fleet-audit` (sibling to `ai-workspace` and `fantasy-game`, matching Griffin's existing one-repo-per-project convention). Kept out of `ai-workspace` deliberately since that repo is the trading system's home (Render/FastAPI deploy config, Supabase) and this tool is unrelated.
