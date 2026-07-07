#!/usr/bin/env bash
# index-doctor verify — rebuild the index from the committed corpus and run the
# full probe set. Mirrors tests/ci_rag_smoke but richer (count parity, chunk
# hygiene, per-leg checks). Requires the project venv (torch CPU + chromadb +
# sentence-transformers). Exit 0 = healthy index.
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$here/../../.." && pwd)"
doctor="$here/doctor.py"

echo "== index-doctor verify =="
cd "$repo_root" || exit 1

# Dependency preflight. The retrieval stack (torch/chromadb/sentence-transformers)
# is heavy and absent in a bare container; skip cleanly rather than fail — the CI
# verify-skills leg installs the full env and DOES run this.
if ! python3 -c "import torch, chromadb, sentence_transformers, rank_bm25, nltk" 2>/dev/null; then
  echo "SKIP: retrieval deps not installed (need torch/chromadb/sentence-transformers). Install first." >&2
  exit 0
fi

# The RAG smoke needs a soul file + writable index/logs dirs (same hermetic prep
# as ci.yml). Create them non-destructively if missing.
mkdir -p data/personality index logs
[ -f data/personality/soul.md ] || echo '# Soul' > data/personality/soul.md

# Rebuild from the committed corpus and run every check.
if GROK_API_KEY="${GROK_API_KEY:-dummy}" python3 "$doctor" --rebuild; then
  echo "== index-doctor verify: OK =="
else
  echo "index-doctor verify: FAIL — see failures above" >&2
  exit 1
fi
