from hunter import paths


def test_defaults_point_into_project_me(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert paths.db() == paths.ROOT / "me" / "job-hunter.db"
    assert paths.config().is_absolute()
    assert paths.config() == paths.ROOT / "me" / "config.yaml"
    assert paths.env() == paths.ROOT / "me" / ".env"
    assert paths.profile() == paths.ROOT / "me" / "profile.md"


def test_paths_read_me_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "ME", tmp_path / "me")

    assert paths.db() == tmp_path / "me" / "job-hunter.db"
    for func in (paths.logs, paths.debug, paths.exports, paths.applications):
        assert func().parent == tmp_path / "me"
