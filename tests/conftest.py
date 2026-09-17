import pytest

from jobbot import db, extensions


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Base SQLite isolée : db.DB_PATH pointe vers tmp_path."""
    path = tmp_path / "jobbot.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db._reset(path)
    db.init()
    yield path
    db._reset(path)


@pytest.fixture
def clean_extensions():
    """Restaure les registres après le test."""
    saved = {name: list(getattr(extensions, name)) for name in extensions.REGISTRIES}
    yield extensions
    for name, values in saved.items():
        getattr(extensions, name)[:] = values
