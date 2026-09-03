"""Self-contained tests for agentic.config (no conftest fixtures needed)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from agentic.config import AgenticConfig, load_agentic_config
from utils.errors import AgenticConfigError
from utils.logger import reset_config_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = (REPO_ROOT / "data").resolve()


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, agentic_block: dict) -> str:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "agentic": agentic_block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def _base_block(**overrides: object) -> dict:
    block = {
        "enabled": True,
        "repo": "CGFixIT/CyClaw",
        "mode": "read",
        "writes_enabled": False,
        "gh_min_version": "2.40.0",
        "registry_path": "data/agentic/skills_registry.json",
    }
    block.update(overrides)
    return block


def test_valid_load(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert isinstance(cfg, AgenticConfig)
    assert cfg.repo == "CGFixIT/CyClaw"
    assert cfg.mode == "read"
    assert cfg.gh_min_tuple == (2, 40, 0)
    assert os.path.isabs(cfg.registry_path)
    assert cfg.deepagent_github.enabled is False
    assert cfg.harness_optimizer.enabled is False
    assert cfg.enabled is True  # type: ignore[attr-defined]


def test_relative_registry_path_is_repo_anchored_from_other_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write_config(tmp_path, _base_block())
    outside = tmp_path / "outside"
    outside.mkdir()

    monkeypatch.chdir(outside)
    cfg = load_agentic_config(path)

    assert cfg.registry_path == str(DATA_ROOT / "agentic" / "skills_registry.json")


def test_defaults_disabled_when_absent_enabled(tmp_path: Path) -> None:
    block = _base_block()
    del block["enabled"]
    cfg = load_agentic_config(_write_config(tmp_path, block))
    # Conservative: agentic is disabled unless explicitly enabled.
    assert cfg.enabled is False  # type: ignore[attr-defined]


def test_rejects_non_bool_enabled(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(enabled="false")))


def test_rejects_non_bool_writes_enabled(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(writes_enabled="false")))


def test_deepagent_config_defaults_disabled_and_path_anchored(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))

    assert cfg.deepagent_github.provider == "ollama"
    assert cfg.deepagent_github.base_url == "http://localhost:11434/v1"
    assert cfg.deepagent_github.allow_deepagents_dependency is False
    assert cfg.deepagent_github.allow_shell_execution is False
    assert cfg.deepagent_github.allow_filesystem_write_tools is False
    assert cfg.deepagent_github.allow_github_writes is False
    assert cfg.deepagent_github.allow_git_write_tools is False
    assert cfg.deepagent_github.workspace_root == str(DATA_ROOT / "agentic" / "workspaces")
    from agentic.config import DEFAULT_MAX_WRITE_BUDGET_BYTES, DEFAULT_PROTECTED_WRITE_PATH_PREFIXES

    assert cfg.deepagent_github.protected_write_paths == list(DEFAULT_PROTECTED_WRITE_PATH_PREFIXES)
    assert cfg.deepagent_github.max_write_budget_bytes == DEFAULT_MAX_WRITE_BUDGET_BYTES
    assert "tests/" in cfg.deepagent_github.protected_write_paths
    assert ".git/" in cfg.deepagent_github.protected_write_paths
    # config.yaml is itself the tunable this whole gate exists to protect
    # (banned_patterns, retrieval.min_score, this very list); CLAUDE.md governs
    # the loop that reads it. Both were a gap in the code default that shipped
    # config.yaml had already closed for .ruff.toml/tox.ini/etc.
    assert "config.yaml" in cfg.deepagent_github.protected_write_paths
    assert "CLAUDE.md" in cfg.deepagent_github.protected_write_paths


def test_deepagent_config_rejects_non_list_protected_write_paths(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"protected_write_paths": "tests/"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_rejects_empty_string_in_protected_write_paths(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"protected_write_paths": ["tests/", ""]})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


@pytest.mark.parametrize("bad", [0, -1, "100000", 1.5, True])
def test_deepagent_config_rejects_invalid_max_write_budget_bytes(tmp_path: Path, bad) -> None:
    block = _base_block(deepagent_github={"max_write_budget_bytes": bad})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_accepts_a_custom_protected_write_paths_and_budget(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"protected_write_paths": ["custom/"], "max_write_budget_bytes": 5000})
    cfg = load_agentic_config(_write_config(tmp_path, block))
    assert cfg.deepagent_github.protected_write_paths == ["custom/"]
    assert cfg.deepagent_github.max_write_budget_bytes == 5000


def test_deepagent_config_defaults_max_handoff_chars(tmp_path: Path) -> None:
    from agentic.config import DEFAULT_MAX_HANDOFF_CHARS

    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert cfg.deepagent_github.max_handoff_chars == DEFAULT_MAX_HANDOFF_CHARS


@pytest.mark.parametrize("bad", [0, -1, "200000", 1.5, True])
def test_deepagent_config_rejects_invalid_max_handoff_chars(tmp_path: Path, bad) -> None:
    block = _base_block(deepagent_github={"max_handoff_chars": bad})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_accepts_a_custom_max_handoff_chars(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"max_handoff_chars": 50_000})
    cfg = load_agentic_config(_write_config(tmp_path, block))
    assert cfg.deepagent_github.max_handoff_chars == 50_000


def test_deepagent_config_defaults_scan_code_shape_on(tmp_path: Path) -> None:
    """Fail safe: the code-shape scanner is on unless an operator turns it off."""
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert cfg.deepagent_github.scan_code_shape is True


def test_deepagent_config_rejects_non_bool_scan_code_shape(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"scan_code_shape": "true"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_accepts_scan_code_shape_off(tmp_path: Path) -> None:
    """The escape hatch is reachable -- a heuristic gate needs one."""
    block = _base_block(deepagent_github={"scan_code_shape": False})
    cfg = load_agentic_config(_write_config(tmp_path, block))
    assert cfg.deepagent_github.scan_code_shape is False


def test_deepagent_config_defaults_planner_timeout_sec(tmp_path: Path) -> None:
    from agentic.config import DEFAULT_PLANNER_TIMEOUT_SEC

    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert cfg.deepagent_github.planner_timeout_sec == DEFAULT_PLANNER_TIMEOUT_SEC


@pytest.mark.parametrize("bad", [0, -1, "600", 1.5, True])
def test_deepagent_config_rejects_invalid_planner_timeout_sec(tmp_path: Path, bad) -> None:
    block = _base_block(deepagent_github={"planner_timeout_sec": bad})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_accepts_a_custom_planner_timeout_sec(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"planner_timeout_sec": 900})
    cfg = load_agentic_config(_write_config(tmp_path, block))
    assert cfg.deepagent_github.planner_timeout_sec == 900


def test_deepagent_config_defaults_planner_max_tokens(tmp_path: Path) -> None:
    from agentic.config import DEFAULT_PLANNER_MAX_TOKENS

    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert cfg.deepagent_github.planner_max_tokens == DEFAULT_PLANNER_MAX_TOKENS


@pytest.mark.parametrize("bad", [0, -1, "2048", 1.5, True])
def test_deepagent_config_rejects_invalid_planner_max_tokens(tmp_path: Path, bad) -> None:
    block = _base_block(deepagent_github={"planner_max_tokens": bad})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_accepts_a_custom_planner_max_tokens(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"planner_max_tokens": 3072})
    cfg = load_agentic_config(_write_config(tmp_path, block))
    assert cfg.deepagent_github.planner_max_tokens == 3072


def test_deepagent_config_rejects_shell_metachar_model(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"model": "good;bad"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_rejects_retired_lmstudio_provider(tmp_path: Path) -> None:
    """Post-Ollama migration: the 'lmstudio' provider id is retired.

    Operators still running LM Studio's OpenAI-compatible server should set
    provider: openai_compatible (or ollama for Ollama itself). Accepting the
    legacy label would silently disagree with shipped config comments.
    """
    block = _base_block(deepagent_github={"provider": "lmstudio"})
    with pytest.raises(AgenticConfigError, match="ollama"):
        load_agentic_config(_write_config(tmp_path, block))


def test_deepagent_config_rejects_workspace_escape(tmp_path: Path) -> None:
    block = _base_block(deepagent_github={"workspace_root": "data/../outside"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_harness_optimizer_config_defaults_disabled_and_path_anchored(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))

    assert cfg.harness_optimizer.max_iterations == 3
    assert cfg.harness_optimizer.require_human_confirm_for_accept is True
    assert cfg.harness_optimizer.allow_local_model_judge is False
    assert cfg.harness_optimizer.output_dir == str(DATA_ROOT / "agentic" / "harness_optimizer" / "runs")
    assert cfg.harness_optimizer.memory_dir == str(DATA_ROOT / "agentic" / "harness_optimizer" / "memory")


def test_harness_optimizer_config_rejects_bad_iterations(tmp_path: Path) -> None:
    block = _base_block(harness_optimizer={"max_iterations": 0})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_harness_optimizer_config_rejects_memory_escape(tmp_path: Path) -> None:
    block = _base_block(harness_optimizer={"memory_dir": "/tmp/cyclaw-memory"})
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, block))


def test_missing_block_raises(tmp_path: Path) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(AgenticConfigError):
        load_agentic_config(str(path))


def test_rejects_bad_repo_slug(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError) as exc:
        load_agentic_config(_write_config(tmp_path, _base_block(repo="not-a-slug")))
    assert exc.value.code == "AGENTIC_CONFIG_INVALID"


def test_rejects_repo_with_metacharacters(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(repo="evil/x; rm -rf")))


@pytest.mark.parametrize("repo", ["-x/y", "x/-y", "-owner/-name", "--repo/x"])
def test_rejects_repo_with_leading_dash_flag_injection(tmp_path: Path, repo: str) -> None:
    # A slug whose owner or name starts with '-' would flow positionally into
    # `gh repo view <repo>` and be parsed by gh as an option. '-' is not in
    # _SHELL_METACHARS, so the slug regex (first char anchored to alphanumeric)
    # is what must reject it.
    with pytest.raises(AgenticConfigError) as exc:
        load_agentic_config(_write_config(tmp_path, _base_block(repo=repo)))
    assert exc.value.code == "AGENTIC_CONFIG_INVALID"


@pytest.mark.parametrize("repo", ["CGFixIT/CyClaw", "a/b", "o.rg/my-repo.name", "x_y/z_1"])
def test_accepts_valid_repo_slugs(tmp_path: Path, repo: str) -> None:
    # Legitimate slugs (dots, hyphens, underscores in non-leading positions) still load.
    cfg = load_agentic_config(_write_config(tmp_path, _base_block(repo=repo)))
    assert cfg.repo == repo


def test_rejects_bad_mode(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(mode="delete")))


def test_rejects_bad_gh_min_version(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(gh_min_version="2.40")))


def test_rejects_registry_path_outside_data(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(registry_path="/tmp/x.json")))


def test_rejects_registry_path_escape(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(registry_path="data/../etc/x.json")))


def test_gh_runtime_defaults(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    assert cfg.gh_timeout_sec == 30
    assert cfg.gh_retries == 2


def test_gh_runtime_overrides(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block(gh_timeout_sec=60, gh_retries=0)))
    assert cfg.gh_timeout_sec == 60
    assert cfg.gh_retries == 0


def test_rejects_bad_gh_timeout(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(gh_timeout_sec=0)))


def test_rejects_negative_gh_retries(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError):
        load_agentic_config(_write_config(tmp_path, _base_block(gh_retries=-1)))


def test_unknown_keys_collected_not_fatal(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block(typo="oops")))
    assert cfg._unknown_keys == ["typo"]  # type: ignore[attr-defined]


def test_to_dict_excludes_enabled(tmp_path: Path) -> None:
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    d = cfg.to_dict()
    assert "enabled" not in d  # plain attribute, not a dataclass field
    assert d["repo"] == "CGFixIT/CyClaw"


def test_is_loopback_url_fail_closed_on_malformed_ipv6() -> None:
    from agentic.config import _is_loopback_url

    assert _is_loopback_url("http://[::1") is False
    assert _is_loopback_url("http://127.0.0.1/v1") is True


def test_resolve_data_path_rejects_empty() -> None:
    from agentic.config import _resolve_data_path

    with pytest.raises(AgenticConfigError, match="required"):
        _resolve_data_path("", "agentic.registry_path")


def test_cloud_provider_model_must_be_string() -> None:
    from agentic.config import DeepAgentCloudProviderConfig

    with pytest.raises(AgenticConfigError, match="model must be a string"):
        DeepAgentCloudProviderConfig(enabled=False, model=12)  # type: ignore[arg-type]


def test_deepagent_rejects_empty_base_url_and_non_string_model(tmp_path: Path) -> None:
    from agentic.config import DeepAgentGitHubConfig

    with pytest.raises(AgenticConfigError, match="base_url"):
        DeepAgentGitHubConfig(enabled=False, base_url="")
    with pytest.raises(AgenticConfigError, match="model must be a string"):
        DeepAgentGitHubConfig(enabled=False, model=99)  # type: ignore[arg-type]


def test_coerce_providers_accepts_instance_and_rejects_bad_block(tmp_path: Path) -> None:
    from agentic.config import DeepAgentCloudProviderConfig, DeepAgentGitHubConfig

    already = DeepAgentCloudProviderConfig(enabled=False, model="")
    cfg = DeepAgentGitHubConfig(providers={"grok": already})
    assert cfg.providers["grok"] is already

    with pytest.raises(AgenticConfigError, match="invalid"):
        DeepAgentGitHubConfig(providers={"grok": {"enabled": False, "model": "", "bogus": 1}})


def test_repo_metachar_defense_after_regex(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # _REPO_RE and _SHELL_METACHARS are disjoint by design, so the metachar
    # branch is defense-in-depth. Widen the regex temporarily to reach it.
    import re

    import agentic.config as config_mod

    monkeypatch.setattr(config_mod, "_REPO_RE", re.compile(r".+"))
    cfg = load_agentic_config(_write_config(tmp_path, _base_block()))
    cfg.repo = "ok/name$"
    with pytest.raises(AgenticConfigError, match="forbidden characters"):
        cfg._validate_repo()


def test_empty_registry_path_rejected(tmp_path: Path) -> None:
    with pytest.raises(AgenticConfigError, match="registry_path is required"):
        load_agentic_config(_write_config(tmp_path, _base_block(registry_path="")))


def test_agentic_block_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "logging": {"audit_file": str(tmp_path / "a.jsonl")},
        "agentic": ["not", "a", "mapping"],
    }), encoding="utf-8")
    with pytest.raises(AgenticConfigError, match="must be a mapping"):
        load_agentic_config(str(path))


def test_agentic_block_typeerror_wrapped(tmp_path: Path) -> None:
    # Unexpected nested kwargs raise TypeError inside AgenticConfig.__post_init__
    # and load_agentic_config wraps them.
    with pytest.raises(AgenticConfigError, match="invalid"):
        load_agentic_config(_write_config(tmp_path, _base_block(
            deepagent_github={"enabled": False, "not_a_real_field": True},
        )))


def test_deepagent_provider_coercion_edge_cases() -> None:
    from agentic.config import DeepAgentGitHubConfig

    with pytest.raises(AgenticConfigError, match="loopback"):
        DeepAgentGitHubConfig(base_url="http://example.com/v1")
    with pytest.raises(AgenticConfigError, match="providers must be a mapping"):
        DeepAgentGitHubConfig(providers=["grok"])  # type: ignore[arg-type]
    with pytest.raises(AgenticConfigError, match="unknown"):
        DeepAgentGitHubConfig(providers={"openai": {"enabled": False, "model": ""}})
    with pytest.raises(AgenticConfigError, match="must be a mapping"):
        DeepAgentGitHubConfig(providers={"grok": "yes"})  # type: ignore[dict-item]
    with pytest.raises(AgenticConfigError, match="allow_cloud_providers is false"):
        DeepAgentGitHubConfig(
            allow_cloud_providers=False,
            providers={"grok": {"enabled": True, "model": "g"}},
        )


class TestShippedAgenticConfigContract:
    """Pins the SHIPPED config.yaml's agentic block, not a synthetic fixture.

    Every other test in this file constructs its own tmp_path config, which
    proves the loader/validator works but never confirms the real repo config
    matches the intended operator-armed posture. Mirrors the real-file-read
    pattern in tests/test_due_diligence_invariants.py and
    tests/test_sanitizer.py's TestShippedConfigContract.

    Posture (operator-signed 2026-08-07): write gates + cloud providers open in
    config, EXECUTION_ENABLED True in code, but agentic.enabled still false so
    the layer no-ops until an operator flips the master switch. deepagent_github
    tooling gates (shell, git write tools, github writes via deepagent tools)
    stay closed — only the writer pr_create path is armed.
    """

    @staticmethod
    def _load() -> AgenticConfig:
        return load_agentic_config(str(REPO_ROOT / "config.yaml"))

    def test_master_switch_disabled_write_gates_open(self) -> None:
        """Layer off by default; write mode + writes_enabled already armed."""
        cfg = self._load()
        assert cfg.enabled is False
        assert cfg.mode == "write"
        assert cfg.writes_enabled is True
        assert cfg.repo == "cgfixit/CyClaw"

    def test_protected_write_paths_covers_its_own_governing_files(self) -> None:
        # The shipped LIST (not just the code default above) is what a real
        # real-repo-run actually consults -- decide_real_repo_candidate reads
        # cfg.deepagent_github.protected_write_paths, and config.yaml always
        # sets this key, so the code default never applies here in practice.
        paths = self._load().deepagent_github.protected_write_paths
        assert "config.yaml" in paths
        assert "CLAUDE.md" in paths
        # Reward-hacking regression: reject a candidate that proposes to touch
        # either -- the same code path a self-improvement run's diff-scope
        # gate uses.
        from agentic.real_repo_loop import _matches_protected_path

        assert _matches_protected_path("config.yaml", paths)
        assert _matches_protected_path("CLAUDE.md", paths)

    def test_deepagent_github_shipped_gates(self) -> None:
        cfg = self._load().deepagent_github
        # Coding-loop package itself stays off until explicitly enabled.
        assert cfg.enabled is False
        assert cfg.allow_deepagents_dependency is False
        assert cfg.allow_filesystem_write_tools is False
        assert cfg.allow_shell_execution is False
        assert cfg.allow_github_writes is False
        assert cfg.allow_git_write_tools is False
        # Cloud chain: master gate open + per-provider flags open (still needs
        # agentic.enabled, deepagent_github.enabled, API keys, --confirm-online).
        assert cfg.allow_cloud_providers is True
        assert set(cfg.providers) == {"grok", "claude"}
        for name, provider in cfg.providers.items():
            assert provider.enabled is True, name
            assert provider.model, name  # non-empty model id pinned in config
            # cloud_provider() IS gate 3/4. It reads allow_cloud_providers and
            # provider.enabled and nothing else -- not deepagent_github.enabled,
            # not an API key (agentic/config.py). Both of its inputs now ship
            # true, so it hands back a live provider on a shipped checkout.
            #
            # This assertion previously read `is None` and was deleted (not
            # inverted) when the shipped config was armed, justified by a
            # comment claiming cloud_provider() still returns None "while the
            # deepagent package flag is off / no keys". That was factually
            # wrong about the method. Restored in the true direction so the
            # shipped behaviour of a live gate is pinned rather than unstated:
            # agentic/cli.py branches on `cloud_provider(...) is None` in three
            # places, so this is the return value those branches actually see.
            assert cfg.cloud_provider(name) is not None, name

    def test_harness_optimizer_shipped_gates_disabled(self) -> None:
        cfg = self._load().harness_optimizer
        assert cfg.enabled is False
        assert cfg.allow_local_model_judge is False
        # The one gate meant to stay TRUE by default: accepting an optimizer
        # candidate is supposed to require a human to confirm. Pinned here
        # because no runtime code path enforces it yet (see the tripwire in
        # tests/test_agentic_harness_optimizer.py) -- if this flips to False
        # in config.yaml with nothing enforcing it either way, that is a
        # silent downgrade of a documented safety default.
        assert cfg.require_human_confirm_for_accept is True


def test_cli_subcommand_surface_is_pinned() -> None:
    """Tripwire on what the agentic CLI exposes.

    Was "exposes no harness/deepagent subcommands". `deepagent-plan` was added
    deliberately: a read-only probe that asserts the six-condition cloud chain,
    fetches injection-scanned GitHub context, and reports the harness build's gate
    state. It invokes nothing and writes nothing.

    `real-repo-run`/`real-repo-run-status`/`real-repo-run-decide` were added
    deliberately too: the first live CLI route that actually clones a real repo,
    calls a model, and (only after a separate, explicit `real-repo-run-decide
    --decision approve`) commits inside that clone -- still no push, no PR, no
    GitHub API call. See `agentic/real_repo_loop.py`'s module docstring for the
    full gate chain.

    `real-repo-run-discard` was added deliberately too: `real-repo-run-decide`
    retains an approved (or rejected) run's clone on disk rather than deleting
    it, and nothing else in this codebase ever reclaims it -- an adversarial
    review found every approved run leaking a full repo clone under
    `workspace_root` forever. This is the explicit reclamation step, not a
    change to when approve/reject themselves clean up.

    `real-repo-run-push`/`real-repo-run-publish` were added deliberately too:
    each escalation past an approved run is its own decision point, and they
    are separate SUBCOMMANDS rather than flags on `real-repo-run-decide`
    because `require_pending_decision` correctly treats an approved run as
    terminal -- an approve-then-push sequence through the decide command could
    never reach the push. Both ship unable to succeed, though by different
    gates since the 2026-08-07 enablement: push needs
    `deepagent_github.allow_git_write_tools` (still false), while publish's
    `agentic/writer.py` gate `EXECUTION_ENABLED` now ships True -- publish is
    held by `agentic.enabled` (false) plus its per-call reason/confirm.
    See `docs/agentic/GITHUB_WRITE_ENABLEMENT.md`.

    `real-repo-run-plan` was added deliberately too: the first half of the
    two-stage split, where a capable (typically cloud) model reasons about the
    approach ONCE and a cheaper local model then implements it. It clones
    nothing, writes no run record, and touches no git -- it fetches context,
    calls one model, and prints text. It is a SEPARATE subcommand rather than a
    flag on `real-repo-run` precisely so a human sits between the two: a plan
    reaches the coding model only when an operator reads it and passes it
    forward with `--plan-file`, which also lets them edit it first.

    If this needs updating again, that wiring was added on purpose. Update it
    deliberately, not by accident."""
    import argparse

    from agentic.cli import build_parser

    parser = build_parser()
    sub_action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
    assert set(sub_action.choices.keys()) == {
        "status", "context", "propose-skill", "apply-skill", "deepagent-plan",
        "real-repo-run", "real-repo-run-plan", "real-repo-run-status", "real-repo-run-decide",
        "real-repo-run-push", "real-repo-run-publish",
        "real-repo-run-discard", "test",
    }
