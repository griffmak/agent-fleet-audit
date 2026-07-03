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
