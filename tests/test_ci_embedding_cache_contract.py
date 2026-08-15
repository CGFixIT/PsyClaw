"""Lock the .emb_cache actions/cache step onto every CI job that can trigger it.

retrieval/embeddings.py fetches the sentence-transformers embedding model
(~90MB) from HuggingFace on a cold cache. Any job step that runs
`python -m retrieval.indexer` -- directly, or indirectly via the
CyClaw-Sandbox skill's verify.sh -- pays that fetch unless the job also caches
`.emb_cache` under the `emb-model-${{ hashFiles('config.yaml') }}` key.

ci.yml's `test` job and python-package-conda.yml's `ci` job have always had
this cache step. ci.yml's `verify-skills` matrix job runs the CyClaw-Sandbox
leg's verify.sh (which calls `python -m retrieval.indexer`) but did not cache
`.emb_cache` -- only the unrelated torch-wheel cache -- so every matrix
invocation of that leg re-downloaded the model fresh. This is the same class
of gap `test_ci_coverage_flag_contract.py` closes for `--cov=` enumeration,
applied to this cache step instead.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_EMB_CACHE_KEY = "emb-model-${{ hashFiles('config.yaml') }}"

# job name -> workflow file. Every job here is known (by prior incident or by
# code inspection) to run something that can trigger the embedding fetch.
_JOBS_THAT_CAN_TRIGGER_THE_FETCH = {
    ("ci.yml", "test"): ".github/workflows/ci.yml",
    ("ci.yml", "verify-skills"): ".github/workflows/ci.yml",
    ("python-package-conda.yml", "ci"): ".github/workflows/python-package-conda.yml",
}


def _job_block(rel: str, job_name: str) -> str:
    """Return the YAML text of one top-level job, from its header to the next
    top-level job header (or EOF). Good enough for a substring-presence check
    without a full YAML-aware job splitter."""
    text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
    job_header = re.compile(rf"^  {re.escape(job_name)}:\s*$", re.MULTILINE)
    match = job_header.search(text)
    assert match, f"job {job_name!r} not found in {rel}"
    start = match.end()
    next_job = re.compile(r"^  [A-Za-z0-9_-]+:\s*$", re.MULTILINE)
    following = next_job.search(text, start)
    end = following.start() if following else len(text)
    return text[start:end]


def test_every_indexer_triggering_job_caches_the_embedding_model() -> None:
    for (workflow, job_name), rel in _JOBS_THAT_CAN_TRIGGER_THE_FETCH.items():
        block = _job_block(rel, job_name)
        assert "path: .emb_cache" in block, (
            f"{workflow}::{job_name} can trigger the embedding-model fetch "
            f"(via retrieval.indexer or the CyClaw-Sandbox skill) but does not "
            f"cache .emb_cache -- every run re-downloads the model fresh."
        )
        assert _EMB_CACHE_KEY in block, (
            f"{workflow}::{job_name} caches .emb_cache under a key that does not "
            f"match the shared emb-model-${{{{ hashFiles('config.yaml') }}}} key "
            f"the other jobs use -- cache entries would never be shared."
        )


def test_verify_skills_job_is_the_regression_this_contract_pins() -> None:
    """Regression pin for the specific job this contract was added for."""
    block = _job_block(".github/workflows/ci.yml", "verify-skills")
    assert "path: .emb_cache" in block, (
        "ci.yml's verify-skills job lost its .emb_cache cache step -- it runs "
        "the CyClaw-Sandbox skill's verify.sh, which calls "
        "`python -m retrieval.indexer` and re-downloads the embedding model "
        "on every matrix leg without this cache."
    )
