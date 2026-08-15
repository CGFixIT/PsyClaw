# llama.cpp / Ollama / LM Studio / Qwen3.6 — Research Note

**Bottom line:** llama.cpp (and its underlying `ggml` tensor library) is verified as the shared inference engine beneath both Ollama and LM Studio. That supports the architectural premise of CyClaw's Ollama-primary/LM-Studio-fallback local-LLM backend (`llm/client.py`'s `resolve_local_backend`, `config.yaml`'s `models.local_llm` + `models.local_llm.fallback`): the fallback picks between two wrappers around one shared inference core, not between fundamentally different engines. The premise the research does **not** support is behavioral parity — each tool layers its own sampling defaults, context-window policy, chat-template resolution, and independently-versioned vendored llama.cpp build on top of the same GGUF, so a fallback is a hedge against *availability*, not a guarantee of identical output. Qwen3.6-27B itself is corroborated as a real April-2026 Alibaba release across three independently-operated primary sources, with the caveat noted in the model-identity section below.

**Provenance of this note:** written 2026-08-10 by a research pass (four parallel web-research agents plus a synthesis pass) during the Phase 1 optimize routine, for Phase 2 to re-verify. Every claim here is web-sourced and post-dates the researching model's January 2026 training cutoff, so nothing in it carries offline corroboration. Confidence tags are inline per section.

**Superseded note (2026-08-15):** `config.yaml`'s `models.local_llm.model` (and the paired `agentic.deepagent_github.model` / `guardrails.model` knobs) moved from `qwen3.6:27b` to `qwen3.8:27b`. Everything below — the model-identity findings, the GGUF/llama.cpp friction analysis, and every citation in the "Qwen3.6 verification" Sources section — was researched against `qwen3.6:27b` specifically and does **not** carry over to `qwen3.8:27b`. Treat this note as a historical record of the `qwen3.6:27b` investigation, not as current guidance; a fresh research pass against `qwen3.8:27b` is a prerequisite before trusting any of its conclusions for the model CyClaw now ships.

---

## Qwen3.6 Model Identity and GGUF Support

`qwen3.6:27b` is CyClaw's shipped `models.local_llm.model` value in `config.yaml` — it is the repository's own configured default, not a name introduced by this research pass. The question this section answers is whether that tag names a real, loadable model with working llama.cpp/GGUF support.

**Existence: corroborated** (confidence: **verified across three independently-operated primary sources; see the cutoff caveat below**). The research pass directly fetched — not merely search-snippeted — the official `QwenLM/Qwen3.6` GitHub repo, the official `Qwen/Qwen3.6-27B` Hugging Face model card, and Ollama's `qwen3.6:27b` library page, and got structurally detailed, mutually consistent specs from all three: 27B params (27.8B by Ollama's count), 5120 hidden dim, 64 layers, hybrid Gated DeltaNet + Gated Attention, 262,144-token native context (extensible toward ~1M), April 2026 release, Apache 2.0, 17GB at Q4_K_M. Secondary listings on OpenRouter, LM Studio, and Unsloth's docs agree. Qwen3.6 is described as Alibaba's successor to Qwen3.5, with hosted variants (Plus, Max-Preview, Flash) alongside two open-weight checkpoints: `Qwen3.6-35B-A3B` (MoE) and `Qwen3.6-27B` (dense). CyClaw's tag points at the **dense** one.

**Cutoff caveat** (the one real limitation, stated plainly): the researching model's knowledge cutoff is January 2026 and this release postdates it, so every fact above rests on live fetches with no offline corroboration. Convergent structural detail across three independently-operated sites is meaningfully stronger evidence than agreeing search snippets would be, but it is still a single research pass. Confirm against the operator's own `ollama list` before betting anything expensive on it.

**GGUF/llama.cpp support: exists, with a documented friction pattern for this model generation** (confidence: **verified** for the friction pattern's existence; **could not verify** whether it touches the dense 27B specifically). GGUF conversions ship (e.g. `unsloth/Qwen3.6-27B-GGUF` with a run-locally guide), and llama.cpp merged Qwen3.6 support including MTP/speculative decoding (cited as PR `#22673`, ~2x throughput claims). Against that: "unknown model architecture" failures are a recurring, well-documented pain point across the Qwen3.5/3.6 generation in Ollama's vendored llama.cpp fork — five open Ollama issues (`#15834`, `#15499`, `#15898`, `#15747`, `#14512`) report it, with the root cause cited as Ollama's fork lagging upstream on new architecture-table entries.

**The load-bearing gap for CyClaw:** every one of those concrete failure reports concentrates on the **35B-A3B MoE sibling**'s architecture string (`qwen35moe`) and on vision/mmproj loading paths — *not* on the dense 27B checkpoint CyClaw actually configures. The research pass looked for and did not find a dated failure report tied specifically to dense `qwen3.6:27b`, and could not confirm the absence either way. A separate unofficial derivative (`Qwen3.6-27B-DFlash-GGUF`, architecture string `dflash-draft`) is confirmed unrecognized by llama.cpp, but that is a third-party build, not the base model. So: the friction pattern is real for the generation, and CyClaw's specific checkpoint sits outside every confirmed instance of it. That is a genuinely open question, not a known problem — and it resolves in one command on the operator's machine (`ollama pull qwen3.6:27b` and load it), which is why it heads the Phase 2 list.

---

## What llama.cpp Is (verified)

llama.cpp is an open-source C/C++ inference library and toolset, "runs LLM (and vision-model) inference with minimal setup and state-of-the-art performance on a wide range of hardware — locally and in the cloud," per the project's own GitHub description. It has no required external runtime dependencies, supports CPU SIMD paths (ARM NEON/Accelerate/Metal, AVX/AVX2/AVX512/AMX) and 15+ GPU/accelerator backends (CUDA, HIP, Vulkan, Metal, SYCL, WebGPU), hybrid CPU+GPU inference, and 1.5-bit through 8-bit quantization. Georgi Gerganov open-sourced it on March 10, 2023, originally to run Meta's LLaMA weights on a MacBook CPU. (confidence: **verified**, multiple corroborating sources)

Governance: Gerganov remains lead maintainer under the `ggml-org` GitHub org. On February 20, 2026, Hugging Face announced Gerganov and the GGML team joined Hugging Face, with the stated commitment that "the project will continue to be 100% open-source and community driven." (confidence: **verified** via the official HF blog post — Gerganov's exact post-acquisition title was not independently checked beyond what that post states)

Project health as of August 2026 (confidence: **verified** for stars/forks/license/commit count via a direct GitHub fetch; **approximate/unreconciled** for contributor count, where three sources gave three different figures — treat as "several hundred to roughly a thousand," not a precise number):
- License: MIT
- 123.3k stars, 21.5k forks, 10,340+ commits on master, 687 open issues, ~1.3k open PRs
- No semantic versioning — a monotonically increasing `b<number>` build tag ships on effectively every merged change via CI; latest tag at research time was b10333 (Aug 9, 2026), with multiple builds tagged per day during active periods
- A cumulative "2,600+ tagged releases" figure and a PyTorch/TensorFlow stars-growth-rate comparison came from a single secondary source each and are **not verified** here

---

## GGUF File Format (verified)

GGUF ("GGML Universal File") is a binary format defined by the llama.cpp/GGML project that packages a model's tensors, tokenizer vocabulary/config, and architecture metadata into one self-contained, memory-mappable file. It debuted August 21, 2023 as the successor to the earlier GGML/GGMF/GGJT formats, which required code changes to support new architectures or quantization schemes. Structurally: a header (magic number, version, section counts), a flexible key-value metadata store (e.g. `llama.context_length`, tokenizer fields, an optional Jinja2 `tokenizer.chat_template`), per-tensor descriptors (name, dimensions, GGML quant type, byte offset), and an aligned tensor-data section. Design goals: self-contained, memory-mappable, extensible via key-value pairs without breaking older readers. (confidence: **verified**, multiple corroborating primary/near-primary sources)

GGUF is not exclusive to Ollama/LM Studio — it's also supported by GPT4All and other tools, and Hugging Face hosts tens of thousands of GGUF checkpoints. (confidence: **verified**)

---

## How a New Architecture Gets Supported, and the "Unknown Model Architecture" Failure Mode (verified)

This is a documented two-stage process, confirmed by the project's own contributor docs:

**Stage 1 — Python conversion (`convert_hf_to_gguf.py`):** register a new model class via `@ModelBase.register()`; define the GGUF tensor layout in `constants.py` (new `MODEL_ARCH` enum entry, `MODEL_ARCH_NAMES` entry, `MODEL_TENSORS` list); map original tensor names to standardized GGUF names in `tensor_mapping.py`; override conversion hooks (`set_gguf_parameters()`, `set_vocab()`, `modify_tensors()`) as needed.

**Stage 2 — C++ engine implementation:** add a new `llm_arch` enum value in `src/llama-arch.h`; register its name and tensor-name maps in `src/llama-arch.cpp`; handle non-standard metadata in `src/llama-model-loader.cpp` and RoPE-type cases if needed; build the actual inference computation graph in `src/llama-model.cpp`; grep for `LLM_ARCH_` usages to confirm every dispatch site was updated.

**Why loading fails with "unknown model architecture":** every supported architecture is a compiled-in `llm_arch` enum value mapped to a canonical string that must appear in a GGUF's `general.architecture` metadata key (e.g. `llama`, `mistral`, `qwen2`). At load time, `llama_model_loader` reads that string and looks it up against a finite, hardcoded C++ switch statement. If the string doesn't match any compiled-in case, loading fails. Three documented root causes: (1) the architecture genuinely isn't implemented yet in the installed build (a newly released model family whose GGUF predates that build's support), (2) a stale or third-party converter produced a malformed/missing `general.architecture` field, or (3) version skew between the GGUF-producing tooling and the GGUF-consuming runtime — one guide states "90% of the time this is a version problem." The universally cited fix is to update to a newer llama.cpp build rather than patch the GGUF file itself. (confidence: **verified**, corroborated by the project's own docs plus multiple independent bug-tracker/how-to sources)

This mechanism is exactly why CyClaw's fallback design (Ollama primary, LM Studio fallback) is not immune to a shared failure mode: if a model's architecture string isn't in *either* vendor's currently-built-in switch table, both backends fail the same way for the same underlying reason, just via two independently-versioned copies of the same table.

---

## Ollama's Coupling to llama.cpp/ggml (verified, with caveats)

Ollama's relationship to llama.cpp is a **vendored-and-patched hybrid**, not a clean fork or a thin wrapper:

- Through roughly 2024, Ollama was a Go service that shelled out to a CGo-wrapped build of llama.cpp for essentially all inference.
- Starting May 15, 2025 (Ollama's own blog), Ollama built a second, in-house Go inference engine (`ollamarunner`) that calls the `ggml` tensor/backend library directly from Go, motivated by making multimodal architectures first-class and isolating each model's "blast radius." This engine covers roughly 21 mainstream architectures.
- The old CGo-wrapped llama.cpp runner (`llamarunner`) remains vendored as a fallback for the long tail (~120+ architectures) the newer engine doesn't yet implement. Ollama tries the new engine first, falls back transparently.
- A claimed opt-in MLX preview engine for Apple Silicon as a "third path" was reported by only one secondary aggregator and is **not independently verified** here.

**Documented version lag** (confidence: **verified**, specific numbers from a live GitHub issue): as of Ollama v0.20.5, the vendored llama.cpp pin was commit b7437 (Dec 16, 2025). Two upstream Vulkan/AMD performance PRs merged Feb 24 and Mar 15, 2026 had not been picked up ~5 months after the pin's cutoff. A reproducible benchmark on identical AMD/Vulkan hardware showed Ollama at ~34 tok/s vs. standalone llama.cpp (with both PRs) at ~52–56 tok/s — a ~56% throughput gap for that specific backend/hardware combination. The lag is **not fixed or predictable**; it depends on backend and how recently Ollama last ran its manual vendoring PR.

**Format/registry layer on top of GGUF** (confidence: **verified** via Ollama's own docs): GGUF remains the underlying weight format. Ollama adds two proprietary layers on top: (1) **Modelfile**, a Dockerfile-like build format (`FROM`, `TEMPLATE`, `PARAMETER`, `SYSTEM`, `ADAPTER`, `LICENSE`) used to build a named model via `ollama create`; (2) an **OCI-inspired manifest+blob registry** (JSON manifest + SHA-256-content-addressed blob layers) for storage/distribution, distinct from vanilla llama.cpp which just wants a bare `.gguf` file path.

**Known compatibility gotchas** (confidence: **verified**, each backed by a specific GitHub issue): architecture-support version skew causing the same GGUF to load in one tool and fail "unknown model architecture" in the other; chat-template autodetection gaps on Modelfile import (falls back to a generic `{{ .Prompt }}` template in reported cases, breaking stop-token/turn-boundary behavior); an open, unconfirmed-root-cause report of output discrepancies between Ollama and llama.cpp at `temperature=0.01` for the same GGUF (confidence on root cause: **could not verify**, thread not fully fetched).

**OpenAI-compatible API surface** (confidence: **verified** via live docs check, directly relevant to `llm/client.py` talking to `127.0.0.1:11434/v1`): the surface now includes `/v1/chat/completions` (streaming, JSON mode, seed, vision via base64 only — no remote URLs, tool calling, reasoning-control fields), `/v1/completions` (string prompt only, no token arrays), `/v1/embeddings`, `/v1/models`, `/v1/models/{model}`, and a newer `/v1/responses` (no stateful `previous_response_id`/`conversation` fields). Explicitly **unsupported** across these endpoints: `tool_choice`, `logit_bias`, `user`, `n`, and logprobs everywhere (no `logprobs`/`top_logprobs` on any endpoint); `best_of` and `echo` are also unsupported on `/v1/completions`. No API key is enforced by Ollama itself — any string or none is accepted, consistent with CyClaw treating Ollama as an unauthenticated loopback-only backend behind its own auth layer. **Practical implication for CyClaw:** if any current or future code path in `llm/client.py` passes `logprobs`, `tool_choice`, `logit_bias`, `n>1`, or an image URL (vs. base64) through this surface, Ollama will silently ignore or reject it.

---

## LM Studio's Coupling to llama.cpp/ggml (verified, with caveats)

LM Studio does not implement its own inference engine for GGUF models — it embeds llama.cpp/ggml as its "llama.cpp engine," and since roughly LM Studio 0.3.x runs a second, parallel MLX engine for Apple-Silicon-native MLX-format models, mixable per-model in the same install. (confidence: **verified**, multiple corroborating comparison sources plus LM Studio's own blog)

LM Studio versions and ships its own build of the engine on its own release cadence, separate from upstream — its 0.4.0 release notes state the bundled "llama.cpp engine graduated to version 2.0.0," adding concurrent inference requests to the same loaded model. A secondary aggregator additionally claimed an "LM Studio Engine Protocol" adding speculative decoding/continuous batching "starting at 0.4.17+" — this specific branding and version number is **not verified** against LM Studio's own changelog; only the general fact of a versioned, wrapped engine is confirmed.

**Model-ID naming divergence from Ollama** (confidence: **verified**, real and consistently documented): LM Studio uses a HuggingFace-style `publisher/model` id (e.g. `google/gemma-4-26b-a4b`), stored on disk as `~/.lmstudio/models/<publisher>/<model>/<file>.gguf`. Ollama uses its own `name:tag` registry scheme (e.g. `qwen3.6:27b`), unrelated to any HuggingFace path. **This is directly relevant to CyClaw's config**, since `config.yaml` carries two different id strings for what is meant to be the same model — `qwen3.6:27b` (Ollama) and `qwen3.6-27b-instruct` (LM Studio) — and those two strings are not mechanically derivable from each other; each must independently resolve to a real, loadable model in its respective tool. Community bridging tools (`llamalink`, `Link-Ollama-Models-to_LM_Studio`) exist specifically because this gap is real enough to need tooling.

**OpenAI-compatible local server** (confidence: **verified** via live docs check): base URL `http://localhost:1234/v1` (default, changeable), covering `/v1/models`, `/v1/chat/completions`, `/v1/completions`, `/v1/embeddings`, and `/v1/responses`. No API-key auth is applied by the server itself — access control is the loopback bind (confidence on the "no auth" specifics: **moderate**, corroborated by a secondary source since the primary LM Studio doc page fetched didn't itself state the auth posture explicitly).

**Same-GGUF-different-behavior risk, four documented classes** (confidence: **verified** for the existence of each class via a llama.cpp GitHub discussion and Ollama's own context-length docs; **unverified specifics** for a cited Qwen-MoE version-lag example, flagged below):
1. **Sampling defaults** — GGUF metadata doesn't reliably carry author-recommended sampling params consistently across tools; Ollama's Modelfile can bake in per-model overrides that a bare wrapper wouldn't apply.
2. **Context-length handling** — Ollama has historically applied its own default context window (often cited as 2048 or 4096) rather than always honoring the GGUF's trained/declared max, requiring an explicit `num_ctx` override; LM Studio exposes a per-model context control in its UI with warning/auto-truncation near the limit. Different default policies, same underlying parameter.
3. **Chat-template handling** — a template mismatch (ChatML vs. Llama-3-style vs. Alpaca-style, etc.) changes the effective prompt seen by the model independent of weights or sampling, and is called the most likely source of *silently* wrong output across runtimes.
4. **Quantization/architecture support lag vs. upstream** — LM Studio bundles/versions its own llama.cpp build rather than tracking upstream commit-by-commit, with a lag search-summarized as "a few days to several weeks." A specific cited example (an early Qwen MoE routing bug fixed upstream before reaching LM Studio) is **unverified** — sourced from a secondary aggregator, not checked against llama.cpp's commit history or LM Studio's changelog directly. The general lag pattern itself is corroborated across multiple independent 2026 comparison sources and is architecturally inevitable for any downstream packager of a fast-moving upstream project.

**Net implication:** "same GGUF, same SHA-256" is necessary but not sufficient for identical behavior between Ollama and LM Studio. Sampling config, context-window policy, and chat-template resolution can all differ even with byte-identical weights.

---

## Confidence in CyClaw's Ollama-Primary/LM-Studio-Fallback Design

What this research **supports** (confidence: **verified**): the architectural premise that Ollama and LM Studio are both llama.cpp/ggml-based local runtimes, sharing the GGUF format and a broadly similar OpenAI-compatible HTTP surface, is real. `resolve_local_backend`'s choice of Ollama as primary and LM Studio as fallback is choosing between two wrappers around a shared inference core, not between fundamentally different engines — that part of the design's premise holds up.

What this research **does not support with confidence**, and what CyClaw's fallback design should account for given the findings above:

- **The two backends are not guaranteed to behave identically for the same nominal model.** Different default context windows, different sampling defaults, different chat-template resolution, and independently-versioned/lagging vendored llama.cpp builds mean a fallback from Ollama to LM Studio (or vice versa) is not guaranteed to be behaviorally transparent to the caller, even if both successfully load "the same" model. If `llm/client.py` or `config.yaml` assumes output parity across the fallback boundary, that assumption is **not verified** by this research and should be treated as an open risk, not a given.
- **Both backends can fail on the identical GGUF for the identical reason** — an architecture string not yet in a given build's compiled-in switch table — but at *different times*, since each vendors/patches llama.cpp on its own release cadence. A model that loads in Ollama today is not guaranteed to load in LM Studio today, and the reverse. This means the fallback is not a hedge against "the model architecture isn't supported" in general — it's only a hedge against one specific tool's *current* build lagging, and only if the other tool's build happens to be ahead for that specific architecture at that specific moment.
- **The `qwen3.6:27b` / `qwen3.6-27b-instruct` id pair is a real cross-tool naming divergence — and `config.yaml` already handles it correctly.** LM Studio uses HuggingFace-style `publisher/model` ids while Ollama uses `name:tag` registry tags, so the two strings are not mechanically derivable from each other. CyClaw's shipped config already says so: the `fallback.model` line is annotated `EXAMPLE ONLY — set to your LM Studio loaded model id when enabling`, the surrounding comment warns `do not reuse the Ollama tag; LM Studio ids usually differ`, and `fallback.enabled` ships `false`. This research **confirms that existing warning is well-founded** rather than uncovering an unflagged bug. The residual action is operator-side, at the moment someone flips `fallback.enabled` to `true`: the example id must be replaced with a real id from their own LM Studio install.
- **What `llm/client.py` does and does not normalize across the fallback boundary** (verified against the code, not inferred): `LocalLLMClient` sends `max_tokens` and `temperature` explicitly in every request body (`llm/client.py:500-501`), so those two sampling knobs *are* pinned identically regardless of which backend answers. Not sent per-request, and therefore inherited from whatever each backend defaults to: the **context window** (`num_ctx` is an Ollama server/Modelfile setting, and LM Studio's is a per-model UI control) and the **chat template** (resolved server-side by each tool from its own model metadata). Those two are the concrete parity gaps across a fallback, and the chat template is the one most likely to change output *silently* rather than visibly.

---

## Open Questions for Phase 2

Phase 2 (or the maintainer, cgfixit, directly) should check the following against the live repo and running environment rather than trusting this research pass:

1. **Does `ollama list` on the operator's machine show a pulled `qwen3.6:27b`, and does it load without an "unknown model architecture" error?** This is the one check that closes the largest open question in this note, and it costs one command. Capture the digest/manifest if it succeeds.
2. **Does the dense 27B behave differently from the 35B-A3B MoE sibling here?** Every confirmed "unknown model architecture" report for this model generation lands on the MoE variant's `qwen35moe` string or on vision/mmproj paths — none on the dense checkpoint CyClaw configures. Close that gap with a direct load test rather than reasoning from the sibling's bug reports.
3. **What llama.cpp build do the operator's installed Ollama and LM Studio actually vendor?** Ollama's vendoring is manual and periodic, with a documented instance of a ~5-month lag behind upstream. If either build predates the point Qwen3.6 architecture support landed upstream, that is the failure mode to expect — and updating the tool, not patching the GGUF, is the fix.
4. **Is `num_ctx` set high enough on the Ollama side?** `config.yaml`'s own `models.local_llm` comment works the arithmetic: Ollama reserves `max_tokens` (3000) up front before generating token #1, so `num_ctx` must exceed `retrieval.max_context_tokens` (4000) + `max_tokens` (3000) + ~1500 headroom, i.e. a recommended 10k–12k. Since `num_ctx` is *not* sent per-request by `llm/client.py`, this is a server-side setting on the operator's machine that no amount of CyClaw config can enforce — worth confirming directly, since the documented failure symptom is a silent stall at "0% processing" rather than an error.
5. **If `models.local_llm.fallback.enabled` is ever flipped to `true`, does `fallback.model` still hold the shipped `EXAMPLE ONLY` placeholder?** LM Studio ids are not Ollama tags; the placeholder will not resolve. The config comment already warns about this — the check is that an operator enabling the fallback actually acted on the warning.
6. **Does chat-template resolution match across the two backends?** `max_tokens`/`temperature` are pinned per-request by `llm/client.py:500-501`, but the chat template is resolved server-side by each tool independently, and a template mismatch changes the effective prompt while producing plausible-looking output. This is the parity gap most likely to go unnoticed if the fallback ever fires in production.
7. **Re-verify the Qwen3.6 claims against live sources at Phase 2 time.** This note is one research pass whose every Qwen3.6 fact postdates the researching model's January 2026 training cutoff. Three independently-operated primary sources agreeing on structural detail is decent evidence, but it is not a substitute for a check made at the time of use.

---

## Sources

### llama.cpp core
- https://github.com/ggml-org/llama.cpp
- https://github.com/ggml-org/llama.cpp/blob/master/LICENSE
- https://github.com/ggml-org/llama.cpp/releases
- https://github.com/ggml-org/llama.cpp/discussions/16111
- https://en.wikipedia.org/wiki/Llama.cpp
- https://en.wikipedia.org/wiki/GGUF
- https://huggingface.co/blog/ggml-joins-hf
- https://huggingface.co/blog/introduction-to-ggml
- https://x.com/ggerganov/status/2062443065214702027
- https://raw.githubusercontent.com/ggml-org/llama.cpp/master/docs/development/HOWTO-add-model.md
- https://deepwiki.com/ggml-org/llama.cpp/3.11-supported-model-architectures
- https://ggml-org-llama-cpp.mintlify.app/concepts/gguf-format
- https://apxml.com/courses/practical-llm-quantization/chapter-5-quantization-formats-tooling/gguf-format
- https://bizon-tech.com/blog/best-llm-inference-engines
- https://buttondown.com/weekly-project-news/archive/weekly-github-report-for-llamacpp-july-07-2026-3956/
- https://aithinkerlab.com/llama-cpp-100k-github-stars-2026/
- https://explainx.ai/blog/what-is-llama-cpp-run-models-locally-2026
- https://markaicode.com/errors/llamacpp-quantization-error-fix/
- https://markaicode.com/errors/llamacpp-model-load-failed-fix/
- https://runaihome.com/blog/unknown-model-architecture-gguf-ollama-llama-cpp-fix-2026/
- https://avenchat.com/blog/fix-unknown-model-architecture-gemma4-llama-cpp
- https://github.com/lmstudio-ai/lmstudio-bug-tracker/issues/1742

### Ollama coupling
- https://ollama.com/blog/multimodal-models
- https://github.com/ollama/ollama/issues/9959
- https://jonathanding.github.io/llm-learning/en/articles/ollama-architecture/
- https://github.com/ollama/ollama/pull/11823
- https://github.com/ollama/ollama/issues/15601
- https://ollama.readthedocs.io/en/modelfile/
- https://docs.ollama.com/import
- https://deepwiki.com/ollama/ollama/4.2-model-registry-and-layers
- https://medium.com/@dewasheesh.rana/inside-ollamas-model-storage-understanding-blobs-and-manifests-06f1620dd0b2
- https://github.com/ollama/ollama/issues/6371
- https://github.com/ollama/ollama/issues/10751
- https://ollama.com/blog/openai-compatibility
- https://docs.ollama.com/api/openai-compatibility

### LM Studio coupling
- https://lmstudio.ai/docs/developer/openai-compat
- https://lmstudio.ai/docs/developer/rest/list
- https://lmstudio.ai/docs/app/modelyaml
- https://lmstudio.ai/blog/lmstudio-v0.3.7
- https://lmstudio.ai/blog/0.4.0
- https://docs.ollama.com/context-length
- https://github.com/ggml-org/llama.cpp/discussions/17088
- https://github.com/sammcj/llamalink
- https://github.com/MarSchra/Link-Ollama-Models-to_LM_Studio
- https://machinelearningmastery.com/ollama-vs-lm-studio-vs-llama-cpp-which-local-ai-runtime-should-you-use-in-2026/
- https://everylocalai.com/tool/llama-cpp
- https://insiderllm.com/guides/lm-studio-vs-llamacpp-speed-gap/
- https://inventivehq.com/blog/ollama-vs-llama-cpp-vs-lm-studio-benchmark
- https://codersera.com/blog/local-ai-runtimes-may-2026-update/
- https://markaicode.com/lm-studio-api-server-openai-compatible/

### Qwen3.6 verification (unverified/could-not-confirm claims — see the callout section)
- https://github.com/QwenLM/Qwen3.6
- https://huggingface.co/Qwen/Qwen3.6-27B
- https://ollama.com/library/qwen3.6:27b
- https://openrouter.ai/qwen/qwen3.6-27b
- https://unsloth.ai/docs/models/qwen3.6
- https://huggingface.co/unsloth/Qwen3.6-27B-GGUF
- https://lmstudio.ai/models/qwen/qwen3.6-27b
- https://www.alibabacloud.com/blog/qwen3-6-plus-towards-real-world-agents_603005
- https://www.alibabacloud.com/blog/qwen3-6-max-preview-smarter-sharper-still-evolving_603055
- https://github.com/ollama/ollama/issues/15834
- https://github.com/ollama/ollama/issues/15499
- https://github.com/ollama/ollama/issues/15898
- https://github.com/ollama/ollama/issues/15747
- https://github.com/ollama/ollama/issues/14512
- https://mer.vin/2026/05/run-qwen-3-6-mtp-in-llama-cpp-faster-local-inference-with-built-in-speculative-decoding/
