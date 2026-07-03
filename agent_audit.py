#!/usr/bin/env python3
"""Retrospective audit of Claude Code subagent dispatches for a session."""
import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

VERIFICATION_TOOLS = {"WebFetch", "WebSearch", "Bash"}
_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


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
        try:
            first = datetime.strptime(min(timestamps), _TIMESTAMP_FORMAT)
            last = datetime.strptime(max(timestamps), _TIMESTAMP_FORMAT)
            duration_seconds = (last - first).total_seconds()
        except ValueError:
            duration_seconds = None

    return {
        "available": True,
        "tool_counts": tool_counts,
        "total_tool_calls": sum(tool_counts.values()),
        "self_verified": self_verified,
        "duration_seconds": duration_seconds,
    }


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
