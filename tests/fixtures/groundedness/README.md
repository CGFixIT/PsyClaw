# Groundedness evaluation fixtures

This directory contains a deliberately fictional, public-safe corpus and the
fixed 24-case rubric used by `tests/judge_eval.py`. It must never contain or
reference an operator's `data/corpus/`, production `index/`, personal data, or
secrets.

The corpus names, organizations, measurements, and relationships are synthetic.
Each case declares expected claims, forbidden claims, and expected source IDs.
Live reports store only case IDs, scores, reason codes, and source IDs; they do
not persist queries, answers, evidence excerpts, or claim text.
