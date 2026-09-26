"""Only an idle session is killed: one that keeps logging runs on, one that goes quiet is retried."""
import stat

import run_claude_arms as R


def fake_claude(tmp_path, body):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\n" + body)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return str(fake)


def run(tmp_path, monkeypatch, body, idle):
    monkeypatch.setattr(R, "CLAUDE", fake_claude(tmp_path, body))
    monkeypatch.setattr(R, "build_prompts", lambda *a: ("system", "user"))
    return R.run_one(tmp_path, "A", "m", 1, 0, 5, {"A": ("toy", False)}, idle=idle)


def test_idle_session_is_killed_and_left_for_a_retry(tmp_path, monkeypatch):
    line = run(tmp_path, monkeypatch, "sleep 130\n", idle=1)
    assert line.startswith("FAILED") and "no activity" in line
    assert not list(tmp_path.glob("query_toy/query1/logs/data_agent/A_cc-m_r0/final_agent.json"))


def test_working_session_outlives_the_idle_limit(tmp_path, monkeypatch):
    body = 'for i in 1 2 3; do echo x >> tool_calls.jsonl; sleep 50; done\necho \'{"result": "", "num_turns": 3}\'\n'
    line = run(tmp_path, monkeypatch, body, idle=70)
    assert line.startswith("ok")
