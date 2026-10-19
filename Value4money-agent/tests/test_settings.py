# The settings the web API will read. These are the defaults a fresh checkout runs on, and two of
# them would be dangerous to get wrong: the token that guards approval, and the CORS allowlist that
# decides which browser may reach it.

from config import Settings, settings


def _from_env(monkeypatch, **values):
    """Build a Settings object from the given environment, ignoring the .env file."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return Settings(_env_file=None)


def test_the_api_binds_to_localhost_and_not_to_every_interface():
    assert settings.api_host == "127.0.0.1"
    assert settings.api_port == 8000


def test_cors_names_the_dev_server_rather_than_every_origin():
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]

    assert "http://localhost:5173" in origins
    assert "*" not in origins


def test_uploads_live_in_their_own_folder():
    """Uploads become settings.bids_dir, so a run's PDFs live under one parent per run."""
    assert settings.uploads_dir.name == "uploads"


def test_uploads_do_not_move_with_the_working_directory():
    """outputs_dir is anchored to the package, and uploads_dir has to agree with it.

    A bare relative path resolved against the CWD, and the server can be launched from anywhere.
    The folder holding real bid documents would then be created outside the repository, where the
    .gitignore entry below cannot protect it and parse_bid_pdf refuses the paths as being outside
    bids_dir.
    """
    assert settings.uploads_dir.is_absolute()
    assert settings.uploads_dir.parent == settings.outputs_dir.parent


def test_runs_are_capped_so_a_stampede_cannot_exhaust_the_database_pool():
    assert 1 <= settings.run_max_concurrency <= 8


def test_the_checkpoint_pool_is_bigger_than_the_number_of_runs_it_serves():
    """Every run writes a checkpoint from its own thread, so the pool must outlast the run cap."""
    assert settings.db_pool_size >= settings.run_max_concurrency


def test_the_api_token_is_read_from_the_environment(monkeypatch):
    assert _from_env(monkeypatch, API_TOKEN="token-from-env").api_token == "token-from-env"


def test_the_api_token_never_appears_in_a_print_of_the_settings(monkeypatch):
    """It is a shared secret, and a traceback or a log line prints settings."""
    built = _from_env(monkeypatch, API_TOKEN="do-not-print-me")

    assert "do-not-print-me" not in repr(built)