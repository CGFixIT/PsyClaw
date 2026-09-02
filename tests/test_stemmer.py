"""Unit tests for the CyClaw stemmer (NLTK PorterStemmer + custom domain map).

`enhanced_porter_stem` is the public alias for `stem_token`. Expected values are
baselined against the stemmer's ACTUAL output (NLTK Porter + the `_CUSTOM_STEMS`
domain dictionary), not an idealized stemmer -- e.g. Porter yields `polici`/`run`
for `policies`/`running`, and the domain map intentionally folds `kubernetes`->`k8s`.
"""

from retrieval.stemmer import (
    _MAX_TOKEN_CHARS,
    stem_token as enhanced_porter_stem,
    tokenize_and_stem,
)


class TestPorterStem:
    def test_short_words_preserved(self):
        assert enhanced_porter_stem("api") == "api"
        assert enhanced_porter_stem("cpu") == "cpu"

    def test_plurals(self):
        assert enhanced_porter_stem("processes") == "process"
        assert enhanced_porter_stem("policies") == "polici"   # NLTK Porter: -ies -> -i
        assert enhanced_porter_stem("addresses") == "address"

    def test_verb_forms(self):
        assert enhanced_porter_stem("running") == "run"       # NLTK Porter collapses -ing
        assert enhanced_porter_stem("configured") == "configur"

    def test_technical_terms_domain_mapped(self):
        # `kubernetes` is an intentional domain normalization (_CUSTOM_STEMS -> k8s).
        assert enhanced_porter_stem("kubernetes") == "k8s"

    def test_ss_ending_preserved(self):
        assert enhanced_porter_stem("access") == "access"

    def test_ies_ending(self):
        assert enhanced_porter_stem("policies") == "polici"

    def test_suffix_reduction(self):
        assert enhanced_porter_stem("rationalization") == "ration"


class TestTokenizeAndStem:
    def test_basic_tokenization(self):
        assert tokenize_and_stem("Configure the backup repository") == [
            "configur", "the", "backup", "repositori"
        ]

    def test_preserves_letter_led_short_tokens(self):
        assert tokenize_and_stem("I am a big fan of AI") == ["am", "big", "fan", "of", "ai"]

    def test_empty_input(self):
        assert tokenize_and_stem("") == []

    def test_numeric_runs_ignored(self):
        assert tokenize_and_stem("version 2.5.1 release") == ["version", "releas"]

    def test_letter_led_alphanumeric_tokens_survive(self):
        assert tokenize_and_stem("kubernetes node a1 healthy") == ["k8s", "node", "a1", "healthi"]

    def test_consistency(self):
        t1 = tokenize_and_stem("backup configuration")
        t2 = tokenize_and_stem("backup configuration")
        assert t1 == t2

    def test_repeated_call_returns_independent_list(self):
        # tokenize_and_stem() is now backed by an lru_cache (keyed on the whole
        # query text) so repeated identical queries skip re-tokenizing. That
        # cache stores an immutable tuple internally and returns list(tuple)
        # -- a FRESH list -- on every call. Prove a caller mutating the first
        # result can't corrupt what a later identical call returns.
        t1 = tokenize_and_stem("backup configuration")
        t1.append("junk")
        t1.clear()
        t2 = tokenize_and_stem("backup configuration")
        assert t2 == ["backup", "configur"]


class TestTokenLengthCap:
    """CVE-2026-81722 defense in depth: Porter never sees a 20–50 KB token."""

    def test_max_length_token_is_stemmed_not_dropped(self) -> None:
        token = "a" * _MAX_TOKEN_CHARS
        stemmed = enhanced_porter_stem(token)
        assert stemmed
        assert len(stemmed) <= _MAX_TOKEN_CHARS

    def test_direct_y_run_is_capped_before_porter(self) -> None:
        # Direct caller, not tokenize_and_stem: the advertised DoS shape.
        payload = "y" * 20_000 + "ness"
        stemmed = enhanced_porter_stem(payload)
        assert len(stemmed) <= _MAX_TOKEN_CHARS

    def test_tokenize_and_stem_does_not_emit_overlong_tokens(self) -> None:
        tokens = tokenize_and_stem("y" * 20_000 + "ness")
        assert tokens
        assert all(len(t) <= _MAX_TOKEN_CHARS for t in tokens)
