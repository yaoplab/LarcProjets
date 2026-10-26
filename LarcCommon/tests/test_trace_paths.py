"""larccommon.trace (set_trace_file) et larccommon.paths (larc_root)."""
from pathlib import Path

from larccommon import paths, trace


def test_larc_root_par_defaut_est_le_monorepo(monkeypatch):
    monkeypatch.delenv("LARC_ROOT", raising=False)
    root = paths.larc_root()
    assert (root / "LarcCommon" / "larccommon" / "paths.py").is_file()


def test_larc_root_variable_d_environnement(monkeypatch, tmp_path):
    monkeypatch.setenv("LARC_ROOT", str(tmp_path))
    assert paths.larc_root() == tmp_path.resolve()


def test_set_trace_file_inactif_si_fichier_absent(tmp_path):
    old = trace.get_trace_file()
    try:
        target = tmp_path / "absent.log"
        trace.set_trace_file(target)
        trace.trace("ne doit rien écrire")
        assert not target.exists()
    finally:
        trace.set_trace_file(old)


def test_set_trace_file_actif_si_fichier_present(tmp_path):
    old = trace.get_trace_file()
    try:
        target = tmp_path / "trace.log"
        target.write_text("", encoding="utf-8")
        trace.set_trace_file(target)
        trace.trace("ligne 1")
        trace.disable()
        assert Path(trace.get_trace_file()) == target
        assert target.read_text(encoding="utf-8") == "ligne 1\n"
    finally:
        trace.set_trace_file(old)


def test_superviseur_garde_son_fichier_de_trace():
    """Le wrapper LarcSuperviseur pointe toujours vers LarcSuperviseur/trace.log."""
    import importlib

    import pytest
    sup_trace = pytest.importorskip("LarcSuperviseur.common.trace")
    old = trace.get_trace_file()
    try:
        importlib.reload(sup_trace)
        expected = paths.larc_root() / "LarcSuperviseur" / "trace.log"
        assert Path(trace.get_trace_file()).resolve() == expected.resolve()
        assert sup_trace.trace is trace.trace
    finally:
        trace.set_trace_file(old)


def test_set_log_file_par_app(monkeypatch, tmp_path):
    from larccommon import logger
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(logger, "_EXPLICIT", False)
    monkeypatch.setattr(logger, "_LOG_PATH", logger._LOG_PATH)
    monkeypatch.setattr(logger, "LOG_FILENAME", logger.LOG_FILENAME)
    logger.set_log_file("LarcTest")
    assert Path(logger.get_log_path()) == tmp_path / "Larc" / "logs" / "LarcTest.log"


def test_set_log_file_respecte_set_log_filename(monkeypatch, tmp_path):
    from larccommon import logger
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(logger, "_EXPLICIT", True)
    monkeypatch.setattr(logger, "_LOG_PATH", "explicite.log")
    logger.set_log_file("LarcTest")
    assert logger.get_log_path() == "explicite.log"


def test_app_config_chemin_relatif_resolu_depuis_la_racine(monkeypatch, tmp_path):
    from larccommon.app_config import AppConfig
    monkeypatch.setenv("LARC_ROOT", str(tmp_path))
    cfg = AppConfig()
    cfg._loaded = True
    cfg._cache["photos_dir"] = "LarcSuperviseur/photos"
    assert Path(cfg.get("photos_dir")) == tmp_path / "LarcSuperviseur" / "photos"
    absolute = str(tmp_path / "ailleurs")
    cfg._cache["photos_dir"] = absolute
    assert cfg.get("photos_dir") == absolute
