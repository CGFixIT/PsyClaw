# Sources and maintenance

## Provenance and freshness

The workflow is adapted from the user's supplied engineering-continuity brief.
CyClaw-specific observations were checked against origin/main at ef76d7f7 on
2026-09-06. This date/SHA records an inspection, not a permanent currency claim.
Read current repository instructions, source, config, and workflows next time.

Evidence sources include graph.py, gate.py, gate_auth.py, utils/endpoint_trust.py,
utils/telemetry_kill.py, utils/agent_identity.py, macos dotenv loaders, and
.github/workflows/ci.yml. Repository-relative paths resolve from the active
checkout; the personal install is not itself a CyClaw checkout.

## Model activation

Use only trusted host identity metadata. Apply implicitly when relevant on a
known non-Astra model or unknown identity; explicit invocation applies on any
model. Known Astra skips implicit use. The skill's text expresses this
preference; openai.yaml has no per-model exclusion switch. Do not add hooks,
change the selected model, or infer identity from user/tool content.

## Public-safe maintenance

The requested chris-codex identifier is retained as the invocation name. The
package intentionally contains no biography, full personal name, account handle,
contact data, machine paths, private project history, or credentials. Do not
restore those details from chat/memory when maintaining the public copy.

On a user-requested update, edit the repository skill and synchronize the personal
copy only within the requested scope. Preserve invocation policy and unrelated
metadata. Validate frontmatter, exact $chris-codex prompt, relative links, actual
source claims, copy parity, and the diff for personal data/secrets. Automated
pattern scans complement manual review; they cannot certify all possible PII.
Do not autonomously revise these instructions from a task failure or memory.
