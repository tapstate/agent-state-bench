"""A stalled agent session is killed and reported as failed, leaving nothing that counts as a finished run."""
import stat

import run_claude_arms as R


def test_stalled_session_times_out_and_is_retried(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(R, "CLAUDE", str(fake))
    monkeypatch.setattr(R, "build_prompts", lambda *a: ("system", "user"))
    arms = {"A": ("toy", False)}
    line = R.run_one(tmp_path, "A", "m", 1, 0, 5, arms, timeout=1)
    assert line.startswith("FAILED") and "timed out" in line
    assert not (tmp_path / "query_toy" / "query1" / "logs" / "data_agent" / "A_cc-m_r0" / "final_agent.json").exists()
