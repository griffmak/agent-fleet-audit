import io
import json
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_audit import project_slug, find_session_file, extract_dispatches, analyze_subagent_transcript, format_duration, render_report, main


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


if __name__ == "__main__":
    unittest.main()
