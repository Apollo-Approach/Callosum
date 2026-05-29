import os
import json
import tempfile
from callosum.config import CallosumConfig


def test_default_config():
    cfg = CallosumConfig(config_dir=tempfile.mkdtemp())
    assert "palace" in cfg.palace_path
    assert cfg.collection_name == "callosum_drawers"


def test_config_from_file():
    tmpdir = tempfile.mkdtemp()
    with open(os.path.join(tmpdir, "config.json"), "w") as f:
        json.dump({"palace_path": "/custom/palace"}, f)
    cfg = CallosumConfig(config_dir=tmpdir)
    assert cfg.palace_path == "/custom/palace"


def test_env_override():
    os.environ["CALLOSUM_PALACE_PATH"] = "/env/palace"
    cfg = CallosumConfig(config_dir=tempfile.mkdtemp())
    assert cfg.palace_path == "/env/palace"
    del os.environ["CALLOSUM_PALACE_PATH"]


def test_init():
    tmpdir = tempfile.mkdtemp()
    cfg = CallosumConfig(config_dir=tmpdir)
    cfg.init()
    assert os.path.exists(os.path.join(tmpdir, "config.json"))
