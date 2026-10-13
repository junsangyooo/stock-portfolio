"""core.secrets.get — env first, then the .env file — and the shared bucket."""
from src.core import secrets
from src.core.ratelimit import TokenBucket


def test_env_wins_then_file_then_default(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text('A_KEY = "from-file"\nB_KEY=plain\n', encoding="utf-8")
    monkeypatch.setattr(secrets, "_FILES", (tmp_path / "missing.env", env))
    monkeypatch.setenv("A_KEY", "from-env")
    monkeypatch.delenv("B_KEY", raising=False)
    assert secrets.get("A_KEY") == "from-env"
    assert secrets.get("B_KEY") == "plain"
    assert secrets.get("C_KEY") is None
    assert secrets.get("C_KEY", "d") == "d"
    monkeypatch.delenv("A_KEY")
    assert secrets.get("A_KEY") == "from-file"     # quotes stripped


def test_bucket_burst_defaults_to_rate():
    b = TokenBucket(3)
    assert b.burst == 3
    for _ in range(3):
        b.take()                                     # no sleep at burst
    assert b.level < 1.0
