from pathlib import Path

from dooers.cli.env_prod import upsert_agent_seed_secret


def test_upsert_creates_env_prod(tmp_path: Path) -> None:
    path = upsert_agent_seed_secret(tmp_path, "secret-one-time-key-abc")
    assert path.name == "env.prod"
    assert path.read_text(encoding="utf-8").strip().endswith("AGENT_SEED_SECRET=secret-one-time-key-abc")


def test_upsert_replaces_existing_line(tmp_path: Path) -> None:
    env = tmp_path / "env.prod"
    env.write_text("FOO=1\nAGENT_SEED_SECRET=old\nBAR=2\n", encoding="utf-8")
    upsert_agent_seed_secret(tmp_path, "new-key-xxxxxxxxxxxx")
    text = env.read_text(encoding="utf-8")
    assert "AGENT_SEED_SECRET=new-key-xxxxxxxxxxxx" in text
    assert "AGENT_SEED_SECRET=old" not in text
    assert "FOO=1" in text
