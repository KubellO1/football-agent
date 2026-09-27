from pathlib import Path


def test_dockerignore_excludes_credentials_and_database_artifacts() -> None:
    root = Path(__file__).parents[2]
    patterns = {
        line.strip()
        for line in (root / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert "*.dump" in patterns
    assert "*.sql" in patterns
    assert "*.tar" in patterns
    assert ".git" in patterns
    assert ".venv" in patterns
