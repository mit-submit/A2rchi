import os

from src.utils.env import read_or_create_persistent_secret


def test_returns_configured_env_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TEST_SECRET_FILE", raising=False)
    monkeypatch.setenv("MY_TEST_SECRET", "configured-value")
    assert read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path)) == "configured-value"
    # nothing is persisted when a value is already configured
    assert not os.path.exists(os.path.join(str(tmp_path), ".my_test_secret"))


def test_reads_previously_persisted_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TEST_SECRET", raising=False)
    monkeypatch.delenv("MY_TEST_SECRET_FILE", raising=False)
    (tmp_path / ".my_test_secret").write_text("persisted-key")
    assert read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path)) == "persisted-key"


def test_generates_then_stable_across_restart(tmp_path, monkeypatch):
    monkeypatch.delenv("MY_TEST_SECRET", raising=False)
    monkeypatch.delenv("MY_TEST_SECRET_FILE", raising=False)
    # first boot: no env, no file -> generate + persist a 64-hex-char key
    first = read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path))
    assert len(first) == 64
    assert os.path.exists(os.path.join(str(tmp_path), ".my_test_secret"))
    # second boot (same dir): SAME key, so signed sessions survive a restart
    second = read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path))
    assert second == first


def test_generated_key_file_is_owner_only_from_creation(tmp_path, monkeypatch):
    """The signing key must never be readable by other users, even briefly:
    the file is created with mode 0600 (os.open), not created wide then
    chmodded after the key is already on disk."""
    monkeypatch.delenv("MY_TEST_SECRET", raising=False)
    monkeypatch.delenv("MY_TEST_SECRET_FILE", raising=False)
    read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path))
    mode = os.stat(tmp_path / ".my_test_secret").st_mode & 0o777
    assert mode == 0o600


def test_key_written_into_preexisting_file_gets_tightened(tmp_path, monkeypatch):
    """An empty leftover key file with wide permissions must not receive the
    new secret at its old mode — os.open's mode only applies at creation, so
    the helper fchmods before writing."""
    monkeypatch.delenv("MY_TEST_SECRET", raising=False)
    monkeypatch.delenv("MY_TEST_SECRET_FILE", raising=False)
    keyfile = tmp_path / ".my_test_secret"
    keyfile.write_text("")
    os.chmod(keyfile, 0o644)
    value = read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path))
    assert keyfile.read_text() == value
    assert (os.stat(keyfile).st_mode & 0o777) == 0o600


def test_filename_override_keeps_per_app_keys_distinct(tmp_path, monkeypatch):
    """Two apps sharing one persist_dir (chat + uploader mount the same data
    volume) and, historically, the same secret NAME must still end up with
    DIFFERENT auto-generated signing keys — a shared Flask secret would make
    one app's signed cookies cryptographically valid input to the other."""
    monkeypatch.delenv("MY_TEST_SECRET", raising=False)
    monkeypatch.delenv("MY_TEST_SECRET_FILE", raising=False)
    default_key = read_or_create_persistent_secret("MY_TEST_SECRET", str(tmp_path))
    named_key = read_or_create_persistent_secret(
        "MY_TEST_SECRET", str(tmp_path), filename=".other_app_secret_key"
    )
    assert named_key != default_key
    assert os.path.exists(os.path.join(str(tmp_path), ".other_app_secret_key"))
    # both stay stable on re-read
    assert read_or_create_persistent_secret(
        "MY_TEST_SECRET", str(tmp_path), filename=".other_app_secret_key"
    ) == named_key
    # an explicitly configured secret still wins over any filename
    monkeypatch.setenv("MY_TEST_SECRET", "configured-value")
    assert read_or_create_persistent_secret(
        "MY_TEST_SECRET", str(tmp_path), filename=".other_app_secret_key"
    ) == "configured-value"


def test_uploader_app_uses_the_persistent_secret_helper():
    """The chat app persists its auto-generated session key so restarts don't
    log users out; the uploader had kept the old fresh-key-per-boot pattern
    (with the SAME secret name) one file over — the exact bug the helper
    exists to prevent. Source-text check because uploader_app imports the
    heavy data_manager stack, unavailable in the bare unit env."""
    import pathlib

    uploader_src = (
        pathlib.Path(__file__).parent.parent.parent
        / "src" / "interfaces" / "uploader_app" / "app.py"
    ).read_text()
    assert "read_or_create_persistent_secret(" in uploader_src
    assert "secrets.token_hex" not in uploader_src
    # the uploader must persist its OWN key file: the chat app already persists
    # under the default file for this same (historically shared) secret name,
    # and both containers mount the same data volume.
    assert 'filename=".uploader_app_secret_key"' in uploader_src
