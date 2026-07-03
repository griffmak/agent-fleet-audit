# Agent Fleet Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dependency-free Python CLI that reads a Claude Code session's on-disk JSONL transcripts and prints a retrospective report of every subagent dispatched — what it was asked, how long it ran, how many tool calls it made, and whether it self-verified (used WebFetch/WebSearch/Bash) or just asserted.

**Architecture:** Single-file CLI (`agent_audit.py`) with no server and no external dependencies. It locates a session's transcript under `~/.claude/projects/<project-slug>/`, extracts `Agent` tool_use/tool_result pairs, cross-references each dispatched agent's own transcript under `<session>/subagents/agent-<id>.jsonl`, and renders a markdown table.

**Tech Stack:** Python 3.10+, standard library only (`json`, `re`, `argparse`, `pathlib`, `datetime`, `unittest`, `tempfile`).

## Global Constraints

- Standard library only — no pip dependencies, no `requirements.txt`.
- Single script, not packaged as a Claude Code skill (v1 is a plain CLI — see spec's "Explicitly out of scope").
- Tests use `unittest` (stdlib), not pytest, to keep the repo genuinely dependency-free.
- Personal-use tool: no config file, no multi-user support, no auth.
- Repo root: `~/dev/agent-fleet-audit` (already git-initialized with the design spec committed as `92e5c2e`).

---

## File Structure

- `agent_audit.py` — the entire tool: session location, dispatch extraction, subagent analysis, report rendering, CLI entry point. Small enough (~150 lines) that splitting into multiple modules would add navigation overhead without benefit.
- `test_agent_audit.py` — all tests, using `unittest` + `tempfile.TemporaryDirectory` to build fixture transcripts on disk per test. No checked-in fixture files.
- `README.md` — short usage doc, added in the final task once the tool is complete.

## Confirmed on-disk schema (verified 2026-07-03 against a live session)

**Parent transcript** (`~/.claude/projects/<slug>/<sessionId>.jsonl`), one JSON object per line:
```json
{"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_...", "name": "Agent", "input": {"description": "...", "prompt": "...", "subagent_type": "..."}}]}}
```
paired with a later line:
```json
{"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_...", "content": [{"type": "text", "text": "Async agent launched successfully...\nagentId: a126773b6530edf7b (internal ID...)..."}]}]}}
```
`subagent_type` is not always present in `input` (omitted when the default general-purpose agent is used).

**Subagent transcript** (`~/.claude/projects/<slug>/<sessionId>/subagents/agent-<agentId>.jsonl`), one JSON object per line:
```json
{"type": "assistant", "agentId": "a126773b6530edf7b", "timestamp": "2026-07-03T14:45:38.717Z", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {...}}]}}
```
Timestamps are ISO 8601 with a literal `Z` suffix, format `%Y-%m-%dT%H:%M:%S.%fZ`.

---

### Task 1: Session locator and dispatch extractor

**Files:**
- Create: `agent_audit.py`
- Test: `test_agent_audit.py`

**Interfaces:**
- Produces: `project_slug(cwd: Path) -> str`
- Produces: `find_session_file(session_id: str | None = None, projects_root: Path | None = None, cwd: Path | None = None) -> Path` — raises `FileNotFoundError` with a descriptive message if nothing found.
- Produces: `extract_dispatches(session_path: Path) -> list[dict]` — each dict has keys `tool_use_id: str`, `description: str`, `subagent_type: str`, `agent_id: str | None`.

- [ ] **Step 1: Write the failing tests**

Create `test_agent_audit.py`:

```python
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_audit import project_slug, find_session_file, extract_dispatches


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for record in records:
            fh.write(json.dumps(record) + "\n")


class TestProjectSlug(unittest.TestCase):
    def test_replaces_slashes_with_dashes(self):
        self.assertEqual(project_slug(Path("/Users/griffinmaklansky")), "-Users-griffinmaklansky")

    def test_nested_path(self):
        self.assertEqual(
            project_slug(Path("/Users/griffinmaklansky/dev/fantasy-game")),
            "-Users-griffinmaklansky-dev-fantasy-game",
        )


class TestFindSessionFile(unittest.TestCase):
    def test_finds_explicit_session_id(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "-fake-project"
            session_path = project_dir / "abc123.jsonl"
            write_jsonl(session_path, [{"type": "user"}])

            result = find_session_file(
                session_id="abc123", projects_root=root, cwd=Path("/fake/project")
            )
            self.assertEqual(result, session_path)

    def test_raises_when_explicit_session_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "-fake-project").mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                find_session_file(session_id="missing", projects_root=root, cwd=Path("/fake/project"))

    def test_defaults_to_most_recently_modified(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_dir = root / "-fake-project"
            older = project_dir / "older.jsonl"
            newer = project_dir / "newer.jsonl"
            write_jsonl(older, [{"type": "user"}])
            write_jsonl(newer, [{"type": "user"}])
            import os
            import time

            time.sleep(0.01)
            os.utime(newer, None)  # bump mtime past older

            result = find_session_file(projects_root=root, cwd=Path("/fake/project"))
            self.assertEqual(result, newer)

    def test_raises_when_project_dir_missing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                find_session_file(projects_root=root, cwd=Path("/fake/project"))


class TestExtractDispatches(unittest.TestCase):
    def test_pairs_tool_use_with_agent_id_from_tool_result(self):
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            write_jsonl(session_path, [
                {
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use", "id": "toolu_1", "name": "Agent",
                        "input": {"description": "Review spec-kit repo", "prompt": "..."},
                    }]},
                },
                {
                    "type": "user",
                    "message": {"content": [{
                        "type": "tool_result", "tool_use_id": "toolu_1",
                        "content": [{"type": "text", "text": "Async agent launched successfully.\nagentId: abc123 (internal ID...)"}],
                    }]},
                },
            ])

            result = extract_dispatches(session_path)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["description"], "Review spec-kit repo")
            self.assertEqual(result[0]["subagent_type"], "general-purpose")
            self.assertEqual(result[0]["agent_id"], "abc123")

    def test_ignores_non_agent_tool_use(self):
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            write_jsonl(session_path, [
                {
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use", "id": "toolu_1", "name": "Bash",
                        "input": {"command": "ls"},
                    }]},
                },
            ])
            self.assertEqual(extract_dispatches(session_path), [])

    def test_missing_agent_id_stays_none(self):
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            write_jsonl(session_path, [
                {
                    "type": "assistant",
                    "message": {"content": [{
                        "type": "tool_use", "id": "toolu_1", "name": "Agent",
                        "input": {"description": "No result yet"},
                    }]},
                },
            ])
            result = extract_dispatches(session_path)
            self.assertEqual(result[0]["agent_id"], None)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: `ModuleNotFoundError: No module named 'agent_audit'`

- [ ] **Step 3: Write minimal implementation**

Create `agent_audit.py`:

```python
#!/usr/bin/env python3
"""Retrospective audit of Claude Code subagent dispatches for a session."""
import re
from pathlib import Path


def project_slug(cwd: Path) -> str:
    return str(cwd).replace("/", "-")


def find_session_file(session_id=None, projects_root=None, cwd=None) -> Path:
    projects_root = projects_root or (Path.home() / ".claude" / "projects")
    cwd = cwd or Path.cwd()
    project_path = projects_root / project_slug(cwd)

    if session_id:
        session_file = project_path / f"{session_id}.jsonl"
        if not session_file.exists():
            raise FileNotFoundError(f"Session file not found: {session_file}")
        return session_file

    if not project_path.is_dir():
        raise FileNotFoundError(f"No Claude Code project directory found at {project_path}")

    candidates = sorted(
        project_path.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No session transcripts found in {project_path}")
    return candidates[0]


def extract_dispatches(session_path: Path) -> list:
    import json

    dispatches = {}
    order = []
    with session_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            content = record.get("message", {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Agent":
                    tool_use_id = block.get("id")
                    input_data = block.get("input", {})
                    dispatches[tool_use_id] = {
                        "tool_use_id": tool_use_id,
                        "description": input_data.get("description", ""),
                        "subagent_type": input_data.get("subagent_type", "general-purpose"),
                        "agent_id": None,
                    }
                    order.append(tool_use_id)
                elif block.get("type") == "tool_result":
                    tool_use_id = block.get("tool_use_id")
                    if tool_use_id not in dispatches:
                        continue
                    result_content = block.get("content")
                    text = ""
                    if isinstance(result_content, list):
                        for b in result_content:
                            if isinstance(b, dict) and b.get("type") == "text":
                                text += b.get("text", "")
                    elif isinstance(result_content, str):
                        text = result_content
                    match = re.search(r"agentId:\s*([a-f0-9]+)", text)
                    if match:
                        dispatches[tool_use_id]["agent_id"] = match.group(1)

    return [dispatches[tid] for tid in order]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: all tests in `TestProjectSlug`, `TestFindSessionFile`, `TestExtractDispatches` PASS

- [ ] **Step 5: Commit**

```bash
cd ~/dev/agent-fleet-audit
git add agent_audit.py test_agent_audit.py
git commit -m "feat(audit): add session locator and dispatch extractor"
```

---

### Task 2: Subagent transcript analyzer

**Files:**
- Modify: `agent_audit.py`
- Modify: `test_agent_audit.py`

**Interfaces:**
- Consumes: nothing from Task 1 directly (takes a `session_path: Path` and `agent_id: str`, same types Task 1 produces).
- Produces: `analyze_subagent_transcript(session_path: Path, agent_id: str) -> dict` — returns `{"available": False}` if the transcript file doesn't exist, otherwise `{"available": True, "tool_counts": dict, "total_tool_calls": int, "self_verified": bool, "duration_seconds": float | None}`.
- Produces: `VERIFICATION_TOOLS = {"WebFetch", "WebSearch", "Bash"}` module-level constant.

- [ ] **Step 1: Write the failing tests**

Append to `test_agent_audit.py` (add import at top: `from agent_audit import analyze_subagent_transcript` alongside the existing import line):

```python
class TestAnalyzeSubagentTranscript(unittest.TestCase):
    def _session_dir(self, tmp):
        session_path = Path(tmp) / "session.jsonl"
        write_jsonl(session_path, [{"type": "user"}])
        return session_path

    def test_missing_transcript_reports_unavailable(self):
        with TemporaryDirectory() as tmp:
            session_path = self._session_dir(tmp)
            result = analyze_subagent_transcript(session_path, "nonexistent")
            self.assertEqual(result, {"available": False})

    def test_flags_self_verified_when_webfetch_used(self):
        with TemporaryDirectory() as tmp:
            session_path = self._session_dir(tmp)
            sub_path = session_path.parent / session_path.stem / "subagents" / "agent-abc123.jsonl"
            write_jsonl(sub_path, [
                {"type": "assistant", "timestamp": "2026-07-03T14:45:33.586Z", "message": {"content": [{"type": "text", "text": "thinking"}]}},
                {"type": "assistant", "timestamp": "2026-07-03T14:45:38.717Z", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {}}]}},
                {"type": "assistant", "timestamp": "2026-07-03T14:46:03.314Z", "message": {"content": [{"type": "text", "text": "done"}]}},
            ])

            result = analyze_subagent_transcript(session_path, "abc123")
            self.assertTrue(result["available"])
            self.assertTrue(result["self_verified"])
            self.assertEqual(result["tool_counts"], {"WebFetch": 1})
            self.assertEqual(result["total_tool_calls"], 1)
            self.assertAlmostEqual(result["duration_seconds"], 29.728, places=2)

    def test_not_self_verified_without_verification_tools(self):
        with TemporaryDirectory() as tmp:
            session_path = self._session_dir(tmp)
            sub_path = session_path.parent / session_path.stem / "subagents" / "agent-def456.jsonl"
            write_jsonl(sub_path, [
                {"type": "assistant", "timestamp": "2026-07-03T14:45:00.000Z", "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}},
                {"type": "assistant", "timestamp": "2026-07-03T14:45:05.000Z", "message": {"content": [{"type": "text", "text": "done"}]}},
            ])

            result = analyze_subagent_transcript(session_path, "def456")
            self.assertTrue(result["available"])
            self.assertFalse(result["self_verified"])
            self.assertEqual(result["tool_counts"], {"Read": 1})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: `ImportError: cannot import name 'analyze_subagent_transcript'`

- [ ] **Step 3: Write minimal implementation**

Add to `agent_audit.py` (near the top, after imports, add `from datetime import datetime`; module-level constant goes after imports; function goes after `extract_dispatches`):

```python
from datetime import datetime

VERIFICATION_TOOLS = {"WebFetch", "WebSearch", "Bash"}
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def analyze_subagent_transcript(session_path: Path, agent_id: str) -> dict:
    import json

    transcript_path = session_path.parent / session_path.stem / "subagents" / f"agent-{agent_id}.jsonl"
    if not transcript_path.exists():
        return {"available": False}

    tool_counts = {}
    timestamps = []
    with transcript_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = record.get("timestamp")
            if ts:
                timestamps.append(ts)
            content = record.get("message", {}).get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "unknown")
                        tool_counts[name] = tool_counts.get(name, 0) + 1

    self_verified = any(name in VERIFICATION_TOOLS for name in tool_counts)
    duration_seconds = None
    if len(timestamps) >= 2:
        first = datetime.strptime(min(timestamps), _TIMESTAMP_FORMAT)
        last = datetime.strptime(max(timestamps), _TIMESTAMP_FORMAT)
        duration_seconds = (last - first).total_seconds()

    return {
        "available": True,
        "tool_counts": tool_counts,
        "total_tool_calls": sum(tool_counts.values()),
        "self_verified": self_verified,
        "duration_seconds": duration_seconds,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: all tests including `TestAnalyzeSubagentTranscript` PASS

- [ ] **Step 5: Commit**

```bash
cd ~/dev/agent-fleet-audit
git add agent_audit.py test_agent_audit.py
git commit -m "feat(audit): add subagent transcript analyzer"
```

---

### Task 3: Report renderer

**Files:**
- Modify: `agent_audit.py`
- Modify: `test_agent_audit.py`

**Interfaces:**
- Consumes: dispatch dicts from `extract_dispatches` (Task 1) — keys `description`, `subagent_type`, `agent_id`; and `analyze_subagent_transcript(session_path, agent_id) -> dict` (Task 2).
- Produces: `format_duration(seconds: float | None) -> str`
- Produces: `render_report(dispatches: list, session_path: Path) -> str` — returns a markdown table as a single string.

- [ ] **Step 1: Write the failing tests**

Append to `test_agent_audit.py` (add `format_duration, render_report` to the import line):

```python
class TestFormatDuration(unittest.TestCase):
    def test_none_is_not_available(self):
        self.assertEqual(format_duration(None), "n/a")

    def test_formats_seconds_to_one_decimal(self):
        self.assertEqual(format_duration(29.728), "29.7s")


class TestRenderReport(unittest.TestCase):
    def test_renders_row_per_dispatch_with_agent_id(self):
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            write_jsonl(session_path, [{"type": "user"}])
            sub_path = session_path.parent / session_path.stem / "subagents" / "agent-abc123.jsonl"
            write_jsonl(sub_path, [
                {"type": "assistant", "timestamp": "2026-07-03T14:45:00.000Z", "message": {"content": [{"type": "tool_use", "name": "WebFetch", "input": {}}]}},
                {"type": "assistant", "timestamp": "2026-07-03T14:45:10.000Z", "message": {"content": [{"type": "text", "text": "done"}]}},
            ])

            dispatches = [{
                "tool_use_id": "toolu_1", "description": "Review spec-kit repo",
                "subagent_type": "general-purpose", "agent_id": "abc123",
            }]
            report = render_report(dispatches, session_path)
            self.assertIn("Review spec-kit repo", report)
            self.assertIn("general-purpose", report)
            self.assertIn("10.0s", report)
            self.assertIn("yes", report)

    def test_missing_agent_id_reports_no_result(self):
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            write_jsonl(session_path, [{"type": "user"}])
            dispatches = [{
                "tool_use_id": "toolu_1", "description": "Still running",
                "subagent_type": "general-purpose", "agent_id": None,
            }]
            report = render_report(dispatches, session_path)
            self.assertIn("agentId not found", report)

    def test_missing_transcript_reports_unavailable(self):
        with TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "session.jsonl"
            write_jsonl(session_path, [{"type": "user"}])
            dispatches = [{
                "tool_use_id": "toolu_1", "description": "Gone",
                "subagent_type": "general-purpose", "agent_id": "missing",
            }]
            report = render_report(dispatches, session_path)
            self.assertIn("transcript unavailable", report)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: `ImportError: cannot import name 'format_duration'`

- [ ] **Step 3: Write minimal implementation**

Add to `agent_audit.py` (after `analyze_subagent_transcript`):

```python
def format_duration(seconds) -> str:
    if seconds is None:
        return "n/a"
    return f"{seconds:.1f}s"


def render_report(dispatches: list, session_path: Path) -> str:
    lines = [
        "| Agent | Type | Duration | Tool calls | Self-verified? | Notes |",
        "|---|---|---|---|---|---|",
    ]
    for dispatch in dispatches:
        description = dispatch["description"] or "(no description)"
        subagent_type = dispatch["subagent_type"]
        agent_id = dispatch["agent_id"]

        if not agent_id:
            lines.append(f"| {description} | {subagent_type} | n/a | n/a | n/a | agentId not found in tool_result |")
            continue

        analysis = analyze_subagent_transcript(session_path, agent_id)
        if not analysis["available"]:
            lines.append(f"| {description} | {subagent_type} | n/a | n/a | n/a | transcript unavailable |")
            continue

        duration = format_duration(analysis["duration_seconds"])
        tool_calls = analysis["total_tool_calls"]
        self_verified = "yes" if analysis["self_verified"] else "no"
        lines.append(f"| {description} | {subagent_type} | {duration} | {tool_calls} | {self_verified} | |")

    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: all tests including `TestFormatDuration`, `TestRenderReport` PASS

- [ ] **Step 5: Commit**

```bash
cd ~/dev/agent-fleet-audit
git add agent_audit.py test_agent_audit.py
git commit -m "feat(audit): add markdown report renderer"
```

---

### Task 4: CLI entry point and README

**Files:**
- Modify: `agent_audit.py`
- Modify: `test_agent_audit.py`
- Create: `README.md`

**Interfaces:**
- Consumes: `find_session_file`, `extract_dispatches`, `render_report` (all prior tasks).
- Produces: `main(argv: list | None = None) -> int` — the CLI entry point.

- [ ] **Step 1: Write the failing test**

Append to `test_agent_audit.py` (add `main` and `import sys` to imports at top):

```python
import io
import sys


class TestMain(unittest.TestCase):
    def test_prints_error_and_returns_1_when_session_not_found(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "-fake-project").mkdir(parents=True)

            import agent_audit
            original_home = agent_audit.Path.home
            agent_audit.Path.home = staticmethod(lambda: root.parent)
            try:
                captured = io.StringIO()
                old_stderr = sys.stderr
                sys.stderr = captured
                try:
                    exit_code = main(["--session", "missing"])
                finally:
                    sys.stderr = old_stderr
            finally:
                agent_audit.Path.home = original_home

            self.assertEqual(exit_code, 1)
            self.assertIn("Error", captured.getvalue())
```

**Note:** this test monkeypatches `Path.home` because `main()` calls `find_session_file` with only `session_id` (production defaults for `projects_root`/`cwd`) — this is the one path in the codebase that touches the real filesystem layout, so the test has to patch at that boundary rather than pass explicit overrides.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: `ImportError: cannot import name 'main'`

- [ ] **Step 3: Write minimal implementation**

Add to `agent_audit.py` (add `import argparse` and `import sys` to the top imports; add at the bottom of the file):

```python
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Audit subagent dispatches for a Claude Code session")
    parser.add_argument("--session", dest="session_id", default=None, help="Session ID to audit (defaults to most recently modified session in the current project)")
    parser.add_argument("--save", action="store_true", help="Also save the report to reports/<sessionId>.md")
    args = parser.parse_args(argv)

    try:
        session_path = find_session_file(session_id=args.session_id)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    dispatches = extract_dispatches(session_path)
    report = render_report(dispatches, session_path)
    print(report)

    if args.save:
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        out_path = reports_dir / f"{session_path.stem}.md"
        out_path.write_text(report + "\n")
        print(f"\nSaved to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd ~/dev/agent-fleet-audit && python3 -m unittest -v`
Expected: all tests PASS, including `TestMain`

- [ ] **Step 5: Write the README**

Create `README.md`:

```markdown
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
```

- [ ] **Step 6: Commit**

```bash
cd ~/dev/agent-fleet-audit
git add agent_audit.py test_agent_audit.py README.md
git commit -m "feat(audit): add CLI entry point and README"
```

---

## Self-Review Notes

- **Spec coverage:** session locator (Task 1), dispatch extractor (Task 1), subagent analyzer (Task 2), report renderer (Task 3), error handling for missing transcript/session (Tasks 1-3), CLI with `--session`/`--save` (Task 4), README (Task 4). Token/cost accounting, live capture, parent-side cross-verification detection, and skill packaging are confirmed out of scope in the spec and intentionally have no task here.
- **Placeholder scan:** no TBD/TODO; every step has complete, runnable code.
- **Type consistency:** `dispatch` dicts carry `tool_use_id`, `description`, `subagent_type`, `agent_id` consistently from Task 1 through Task 3's `render_report`. `analyze_subagent_transcript`'s return shape (`available`, `tool_counts`, `total_tool_calls`, `self_verified`, `duration_seconds`) is used identically in Task 3. `main()` in Task 4 calls the exact function names/signatures produced in Tasks 1 and 3.
