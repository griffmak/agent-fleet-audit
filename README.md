# agent-fleet-audit

Retrospective audit of Claude Code subagent dispatches. Reads the on-disk
session transcripts under `~/.claude/projects/` — no new instrumentation,
no server, no dependencies beyond the Python standard library.

## Usage

Run from the same working directory the Claude Code session used:

    python3 agent_audit.py                  # audits the most recent session
    python3 agent_audit.py --session <id>   # audits a specific session
    python3 agent_audit.py --save           # also writes reports/<id>.md

Output is a markdown table: which agents were dispatched, how long each
ran, how many tool calls it made, and whether it self-verified (used
WebFetch/WebSearch/Bash) versus just asserting. The `Notes` column is
blank by design — fill it in by hand when you've independently cross-checked
something the report can't detect on its own (see the design doc's
"explicitly out of scope" section for why).
