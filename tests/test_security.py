"""Security regression tests: the reviewed repo must never be able to redirect
where code/data is sent, and revisions must not be readable as git options."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from perf_gate import config
from perf_gate.diff import _safe_rev


def _clear_env():
    for k in ("PERF_GATE_MODEL", "PERF_GATE_BACKEND", "PERF_GATE_BASE_URL",
              "PERF_GATE_LLM_DISABLED", "PERF_GATE_FAIL_ON", "PERF_GATE_ENV_FILE",
              "OLLAMA_HOST"):
        os.environ.pop(k, None)


def test_repo_yaml_cannot_set_llm_endpoint(tmp_path):
    """A malicious perf-gate.yml in the reviewed repo must not change backend/base_url."""
    _clear_env()
    (tmp_path / "perf-gate.yml").write_text(
        "llm:\n  backend: openai\n  base_url: http://attacker.example/v1\n  model: evil\n"
    )
    cfg = config.load(str(tmp_path))
    assert cfg.llm["backend"] == "ollama"          # forced back to safe default
    assert cfg.llm["base_url"] == ""               # repo cannot point us off-box


def test_repo_dotenv_is_not_read(tmp_path):
    """A .env inside the reviewed repo / cwd must be ignored entirely."""
    _clear_env()
    (tmp_path / ".env").write_text("PERF_GATE_MODEL=planted\nOLLAMA_HOST=http://attacker\n")
    config.load(str(tmp_path))
    assert os.environ.get("PERF_GATE_MODEL") != "planted"
    assert os.environ.get("OLLAMA_HOST") is None


def test_dotenv_allowlist_blocks_endpoint_keys(tmp_path):
    """Even an operator .env only honours safe keys, never host/backend."""
    _clear_env()
    env_file = tmp_path / "safe.env"
    env_file.write_text(
        "PERF_GATE_MODEL=llama3.1:latest\n"
        "OLLAMA_HOST=http://attacker\n"
        "PERF_GATE_BACKEND=openai\n"
        "PERF_GATE_BASE_URL=http://attacker/v1\n"
    )
    os.environ["PERF_GATE_ENV_FILE"] = str(env_file)
    cfg = config.load(str(tmp_path))
    assert cfg.llm["model"] == "llama3.1:latest"   # allowed key applied
    assert os.environ.get("OLLAMA_HOST") is None    # endpoint key ignored
    assert cfg.llm["backend"] == "ollama"
    assert cfg.llm["base_url"] == ""
    _clear_env()


def test_safe_rev_rejects_option_like_revisions():
    assert _safe_rev("--upload-pack=evil") is None
    assert _safe_rev("-x") is None
    assert _safe_rev("HEAD~1") == "HEAD~1"
    assert _safe_rev("a1b2c3d") == "a1b2c3d"
