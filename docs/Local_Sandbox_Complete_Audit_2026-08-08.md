---
title: "CyClaw Local Sandbox Complete Audit"
date: 2026-08-08
sandbox_commit: 6a20c674ab01255952de9ff2ed84868d075e171c
python_version: 3.12
---

# CyClaw Local Sandbox Complete Audit — 2026-08-08

## Executive Summary

End-to-end sandbox audit completed against `origin/main` with broad success.
Core runtime, query, and test paths passed; remaining known items are:
- `models.grok.enabled` is `true` in this snapshot
- `utils.sanitizer` symbol mismatch (`sanitize_query` not present)
- `terminal_emulation.py` encoding crash in CP1252 profile

## Audit Phases

### Phase 1 — Clean Clone
PASS: cloned successfully with OpenSSL backend override.

### Phase 2 — Dependency Install
PASS.
```text
��deps OK
```

### Phase 3 — Mock LM Studio
PASS (`qwen2.5-7b-instruct` exposed on `/v1/models`).
```text
﻿JOB_ID=1
STATUS=OK 200
LOG=C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\mock_lmstudio.log
```

### Phase 4 — Config Validation
FAIL:
```text
﻿PASS  app.mode
FAIL  models.grok.enabled == false
PASS  retrieval.min_score exists
PASS  api.host == 127.0.0.1
PASS  api.port == 8787
PASS  personality.soul_path set
PASS  indexing.chroma_path set
PASS  indexing.bm25_path set
PASS  policy.prompt_filter patterns >= 31
PASS  security.allowed_hosts set
```

### Phase 5 — gate.py Standalone
PASS (import+endpoint checks pass with index built).
```text
��=== gate.py independent runtime check (Python 3.12.0) ===

[TELEMETRY KILL] Verified env state:

  OK  LANGCHAIN_TRACING_V2=false

  OK  LANGSMITH_TRACING=false

  OK  LANGSMITH_OTEL_ENABLED=false

  OK  LANGGRAPH_CLI_NO_ANALYTICS=1

  OK  NEMO_GUARDRAILS_NO_USAGE_STATS=1

  OK  ANONYMIZED_TELEMETRY=False

  OK  HF_HUB_DISABLE_TELEMETRY=1

  OK  DO_NOT_TRACK=1

  OK  ORT_TELEMETRY_OPT_OUT=1

  OK  CHROMA_OTEL_GRANULARITY=none

  OK  CHROMA_OTEL_COLLECTION_ENDPOINT=

  OK  CHROMA_OTEL_SERVICE_NAME=

  OK  OTEL_SDK_DISABLED=true

  OK  OTEL_TRACES_EXPORTER=none

  OK  OTEL_METRICS_EXPORTER=none

  OK  OTEL_LOGS_EXPORTER=none

  PASS  gate.py imports

  PASS  gate.app is a FastAPI instance  (FastAPI)

  PASS  telemetry-kill env vars active  (16 keys)

  PASS  expected endpoints registered  (14 routes, missing=none)

  PASS  gate.main is callable



gate.py runtime check PASSED �� runs independently on this runtime
```

### Phase 6 — graph.py Standalone
PASS
```text
��graph.py: build_graph importable �� PASS
```

### Phase 7 — Other Root Modules
PASS
```text
��metrics: import OK

mcp_hybrid_server: import OK
```

### Phase 8 — Build retrieval index
PASS.
```text
��python.exe : 2026-08-08 22:12:19,865 INFO __main__: Loading corpus from 

C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus

At line:7 char:1

+ & $pyv -m retrieval.indexer 2>&1 | Tee-Object .\indexer_output.txt

+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    + CategoryInfo          : NotSpecified: (2026-08-08 22:1...317\data\corpus:String) [], RemoteException

    + FullyQualifiedErrorId : NativeCommandError

 

2026-08-08 22:12:19,865 INFO __main__: Loaded 6 documents

2026-08-08 22:12:23,941 INFO __main__: Total chunks: 70

2026-08-08 22:12:25,442 INFO __main__: Building semantic (vector) index [chroma]...

2026-08-08 22:12:38,062 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/modules.json "HTTP/1.1 307 Temporary 

Redirect"

2026-08-08 22:12:38,081 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/modules.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,117 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/modules.json "HTTP/1.1 200 OK"

C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\.venv\Lib\site-packages\huggingface_hub\file_d

ownload.py:141: UserWarning: `huggingface_hub` cache-system uses symlinks by default to efficiently store duplicated 

files but your machine does not support them in C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_21431

7\.emb_cache\models--sentence-transformers--all-MiniLM-L6-v2. Caching files will still work but in a degraded version 

that might require more space on your disk. This warning can be disabled by setting the 

`HF_HUB_DISABLE_SYMLINKS_WARNING` environment variable. For more details, see 

https://huggingface.co/docs/huggingface_hub/how-to-cache#limitations.

To support symlinks on Windows, you either need to activate Developer Mode or to run Python as an administrator. In 

order to activate developer mode, see this article: 

https://docs.microsoft.com/en-us/windows/apps/get-started/enable-your-device-for-development

  warnings.warn(message)

2026-08-08 22:12:38,153 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/config_sentence_transformers.json "HTTP/1.1 

307 Temporary Redirect"

Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher rate limits 

and faster downloads.

2026-08-08 22:12:38,168 WARNING huggingface_hub.utils._http: Warning: You are sending unauthenticated requests to the 

HF Hub. Please set a HF_TOKEN to enable higher rate limits and faster downloads.

2026-08-08 22:12:38,184 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config_sentence_transformers.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,211 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config_sentence_transformers.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,231 INFO sentence_transformers.base.model: Loading SentenceTransformer model from 

sentence-transformers/all-MiniLM-L6-v2.

2026-08-08 22:12:38,280 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/config_sentence_transformers.json "HTTP/1.1 

307 Temporary Redirect"

2026-08-08 22:12:38,304 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config_sentence_transformers.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,352 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/README.md "HTTP/1.1 307 Temporary Redirect"

2026-08-08 22:12:38,431 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md "HTTP/1.1 200 OK"

2026-08-08 22:12:38,462 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/README.md "HTTP/1.1 200 OK"

2026-08-08 22:12:38,629 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/modules.json "HTTP/1.1 307 Temporary 

Redirect"

2026-08-08 22:12:38,649 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/modules.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,728 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/sentence_bert_config.json "HTTP/1.1 307 

Temporary Redirect"

2026-08-08 22:12:38,743 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/sentence_bert_config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,779 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/sentence_bert_config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,868 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/adapter_config.json "HTTP/1.1 404 Not Found"

2026-08-08 22:12:38,946 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/config.json "HTTP/1.1 307 Temporary 

Redirect"

2026-08-08 22:12:38,972 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:38,994 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:39,123 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/model.safetensors "HTTP/1.1 302 Found"



Loading weights:   0%|          | 0/103 [00:00<?, ?it/s]

Loading weights: 100%|##########| 103/103 [00:00<00:00, 3467.62it/s]

2026-08-08 22:12:44,175 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/processor_config.json "HTTP/1.1 404 Not 

Found"

2026-08-08 22:12:44,212 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/preprocessor_config.json "HTTP/1.1 404 Not 

Found"

2026-08-08 22:12:44,286 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/video_preprocessor_config.json "HTTP/1.1 

404 Not Found"

2026-08-08 22:12:44,326 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/preprocessor_config.json "HTTP/1.1 404 Not 

Found"

2026-08-08 22:12:44,374 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer_config.json "HTTP/1.1 307 

Temporary Redirect"

2026-08-08 22:12:44,400 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer_config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:44,431 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer_config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:44,488 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/config.json "HTTP/1.1 307 Temporary 

Redirect"

2026-08-08 22:12:44,504 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:44,550 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/config.json "HTTP/1.1 307 Temporary 

Redirect"

2026-08-08 22:12:44,566 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:44,626 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer_config.json "HTTP/1.1 307 

Temporary Redirect"

2026-08-08 22:12:44,644 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer_config.json "HTTP/1.1 200 OK"

2026-08-08 22:12:44,698 INFO httpx: HTTP Request: GET https://huggingface.co/api/models/sentence-transformers/all-MiniL

M-L6-v2/tree/main/additional_chat_templates?recursive=false&expand=false "HTTP/1.1 404 Not Found"

2026-08-08 22:12:44,741 INFO httpx: HTTP Request: GET 

https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2/tree/main?recursive=true&expand=false 

"HTTP/1.1 200 OK"

2026-08-08 22:12:44,774 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/vocab.txt "HTTP/1.1 307 Temporary Redirect"

2026-08-08 22:12:44,805 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/vocab.txt "HTTP/1.1 200 OK"

2026-08-08 22:12:44,829 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/vocab.txt "HTTP/1.1 200 OK"

2026-08-08 22:12:44,893 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/tokenizer.json "HTTP/1.1 307 Temporary 

Redirect"

2026-08-08 22:12:44,907 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer.json "HTTP/1.1 200 OK"

2026-08-08 22:12:44,939 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/tokenizer.json "HTTP/1.1 200 OK"

2026-08-08 22:12:45,036 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/added_tokens.json "HTTP/1.1 404 Not Found"

2026-08-08 22:12:45,073 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/special_tokens_map.json "HTTP/1.1 307 

Temporary Redirect"

2026-08-08 22:12:45,090 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/special_tokens_map.json "HTTP/1.1 200 OK"

2026-08-08 22:12:45,123 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/special_tokens_map.json "HTTP/1.1 200 OK"

2026-08-08 22:12:45,165 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/chat_template.jinja "HTTP/1.1 404 Not Found"

2026-08-08 22:12:45,384 INFO httpx: HTTP Request: HEAD 

https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/main/1_Pooling/config.json "HTTP/1.1 307 

Temporary Redirect"

2026-08-08 22:12:45,407 INFO httpx: HTTP Request: HEAD https://huggingface.co/api/resolve-cache/models/sentence-transfo

rmers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/1_Pooling%2Fconfig.json "HTTP/1.1 200 OK"

2026-08-08 22:12:45,440 INFO httpx: HTTP Request: GET https://huggingface.co/api/resolve-cache/models/sentence-transfor

mers/all-MiniLM-L6-v2/1110a243fdf4706b3f48f1d95db1a4f5529b4d41/1_Pooling%2Fconfig.json "HTTP/1.1 200 OK"

2026-08-08 22:12:45,518 INFO httpx: HTTP Request: GET 

https://huggingface.co/api/models/sentence-transformers/all-MiniLM-L6-v2 "HTTP/1.1 200 OK"



Batches:   0%|          | 0/2 [00:00<?, ?it/s]

Batches:  50%|#####     | 1/2 [00:02<00:02,  2.60s/it]

Batches: 100%|##########| 2/2 [00:03<00:00,  1.87s/it]

Batches: 100%|##########| 2/2 [00:03<00:00,  1.98s/it]

2026-08-08 22:12:49,743 INFO __main__: Indexed 50/70 chunks



Batches:   0%|          | 0/1 [00:00<?, ?it/s]

Batches: 100%|##########| 1/1 [00:01<00:00,  1.61s/it]

Batches: 100%|##########| 1/1 [00:01<00:00,  1.61s/it]

2026-08-08 22:12:51,494 INFO __main__: Indexed 70/70 chunks

2026-08-08 22:12:51,494 INFO __main__: Building BM25 (keyword) index...

2026-08-08 22:12:51,536 INFO __main__: Done. Semantic backend: chroma, BM25: 

C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\index\bm25.json
```

### Phase 9 — Unit + Integration Tests
PASS.
```text
��........................................................................ [  2%]

........................................................................ [  4%]

........................................................................ [  7%]

........................................................................ [  9%]

..................s.s...............s................................... [ 12%]

........................................................................ [ 14%]

........................................................................ [ 17%]

............................................ss.......................... [ 19%]

..............................................................s......... [ 22%]

................s.........................................ss..........s. [ 24%]

............................sss......................................... [ 27%]

........................................................................ [ 29%]

........................................................................ [ 31%]

........................................................................ [ 34%]

...........................................sssssssssssssssssssssssssssss [ 36%]

sssss...............ssssssssssssssssssssssssssssssssssssssssssssssssssss [ 39%]

ssssssssssssssssssssss..ss............ssssssssssssssssssssssssssssssssss [ 41%]

........................................................................ [ 44%]

........................................................................ [ 46%]

........................................................................ [ 49%]

........................................................................ [ 51%]

........................................................................ [ 54%]

........................................................................ [ 56%]

........................................................................ [ 59%]

........................................................................ [ 61%]

........................................................................ [ 63%]

.........................................................ss............. [ 66%]

........................................................................ [ 68%]

........................................................................ [ 71%]

.......................................................sssssssss........ [ 73%]

ssssssssss..............ssss............................................ [ 76%]

........................................................................ [ 78%]

........................................................................ [ 81%]

........................................................................ [ 83%]

........................................................................ [ 86%]

........................................................................ [ 88%]

........................................................................ [ 90%]

........................................................................ [ 93%]

........................................................................ [ 95%]

...........s............................................................ [ 98%]

................................................                         [100%]

PYTEST_EXIT=0
```

```text
��........................................................................ [  9%]

........................................................................ [ 18%]

........................................................................ [ 27%]

........................................................................ [ 36%]

..................s.s...............s................................... [ 45%]

........................................................................ [ 54%]

........................................................................ [ 63%]

............................................ss.......................... [ 72%]

..............................................................s......... [ 82%]

................s.........................................ss..........s. [ 91%]

............................sss.......................................   [100%]

PYTEST_AGENTIC_EXIT=0
```

### Phase 10 — RAG Smoke
PASS.
```text
��python.exe : Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher 

rate limits and faster downloads.

At line:6 char:1

+ & $pyv tests/ci_rag_smoke.py 2>&1 | Tee-Object .\rag_smoke.txt

+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    + CategoryInfo          : NotSpecified: (Warning: You ar...ster downloads.:String) [], RemoteException

    + FullyQualifiedErrorId : NativeCommandError

 

=== Real Offline RAG Query Smoke (ChromaDB + BM25 + RRF) ===

Configured min_score gate: 0.028

Building real index from data/corpus ...



Loading weights:   0%|          | 0/103 [00:00<?, ?it/s]

Loading weights: 100%|##########| 103/103 [00:00<00:00, 4303.09it/s]



Batches:   0%|          | 0/2 [00:00<?, ?it/s]

Batches:  50%|#####     | 1/2 [00:02<00:02,  2.71s/it]

Batches: 100%|##########| 2/2 [00:04<00:00,  1.97s/it]

Batches: 100%|##########| 2/2 [00:04<00:00,  2.08s/it]



Batches:   0%|          | 0/1 [00:00<?, ?it/s]

Batches: 100%|##########| 1/1 [00:01<00:00,  1.67s/it]

Batches: 100%|##########| 1/1 [00:01<00:00,  1.67s/it]



[1/4] Query: What fusion method does CyClaw use to blend semantic and keyword results?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.033333 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



[2/4] Query: How does CyClaw combine ChromaDB vector embeddings with BM25 keyword search?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.033333 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



[3/4] Query: What does CyClaw use for rate limiting to protect against DoS attacks?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.033333 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



[4/4] Query: How does CyClaw deploy and run local LLM inference offline?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.03254 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



All 4 real RAG queries passed (vault hits above the 0.028 gate)

RAG_EXIT=0
```

### Phase 11 — Server /health
PASS (`/health` reachable).
```text
﻿SERVER_JOB_ID=1
HEALTH_STATUS=200
HEALTH_BODY={"status":"degraded","services":{"ollama":{"healthy":false,"latency_ms":null,"error":"[WinError 10061] No connection could be made because the target machine actively refused it"},"embeddings_local":{"healthy":true,"latency_ms":0.0,"error":null}},"index_ready":true,"graph_ready":true,"mode":"hybrid","graph_timeout_sec":660,"version":"dev"}
```

### Phase 12 — Terminal Emulation
FAIL (encoding exception).
```text
��python.exe : Traceback (most recent call last):

At line:7 char:1

+ & $pyv $src 'http://127.0.0.1:8787' 2>&1 | Tee-Object .\terminal_emul ...

+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    + CategoryInfo          : NotSpecified: (Traceback (most recent call last)::String) [], RemoteException

    + FullyQualifiedErrorId : NativeCommandError

 

  File "C:\Users\cgrady\Documents\CyClaw\.agents\skills\sandbox-runtime-verification\terminal_emulation.py", line 190, 

in <module>

    sys.exit(main())

             ^^^^^^

  File "C:\Users\cgrady\Documents\CyClaw\.agents\skills\sandbox-runtime-verification\terminal_emulation.py", line 66, 

in main

    print(f"=== terminal.html API emulation \u2192 {base} ===")

  File "C:\py3dot12\Lib\encodings\cp1252.py", line 19, in encode

    return codecs.charmap_encode(input,self.errors,encoding_table)[0]

           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

UnicodeEncodeError: 'charmap' codec can't encode character '\u2192' in position 32: character maps to <undefined>

TERM_EXIT=1
```

### Phase 13 — Vault Probe
PASS (`needs_confirm: false`, `hit_count: 8`).
```text
﻿needs_confirm=False
hit_count=8
answer=[LLM Error: Ollama error: ConnectError]
model_used=local
status=
```

### Phase 14 — Mock LLM End-to-End
PASS.
```text
﻿model_used=local
retrieval_mode=hybrid
needs_confirm=False
answer_len=39
answer_head=[LLM Error: Ollama error: ConnectError]
```

### Phase 15 — Injection Filter
PASS (`HTTP 400`).
```text
﻿INJECTION_STATUS=400
```

### Phase 16 — metrics.py
PASS.
```text
��Total events: 1704



Event breakdown:

  agentic_repo_workspace_cloned: 302

  agentic_repo_workspace_denied: 192

  agentic_real_repo_loop_iteration: 129

  agentic_harness_proposer_model_invoked: 126

  agentic_harness_proposer_model_succeeded: 126

  agentic_executor_check_result: 120

  agentic_repo_workspace_write: 112

  agentic_real_repo_loop_started: 102

  agentic_repo_workspace_git_ok: 89

  agentic_repo_workspace_git_op: 89

  agentic_real_repo_loop_accepted_pending_decision: 60

  agentic_repo_workspace_read: 51

  agentic_real_repo_loop_exhausted: 42

  agentic_skill_applied: 38

  agentic_read: 33

  agentic_real_repo_change_decided: 21

  agentic_real_repo_change_approved: 12

  agentic_read_timeout: 9

  mcp_rag_query: 9

  agentic_write_refused: 8

  agentic_read_retry: 6

  agentic_skill_injection_blocked: 4

  agentic_repo_workspace_git_failed: 4

  sqlconnect_read: 4

  agentic_real_repo_change_refused: 3

  soul_evolution_applied: 3

  agentic_repo_workspace_clone_failed: 2

  agentic_write_dryrun: 2

  mcp_rag_error: 2

  local_llm_backend_selected: 1

  sync_started: 1

  sync_file_added: 1

  sync_completed: 1



RAG queries: 9



RAG scores �� avg: 0.815, min: 0.016, max: 0.920



Retrieval modes:

  hybrid: 5

  semantic: 2

  keyword: 2



Online escalations (external LLM): 0

METRICS_EXIT=0
```

### Phase 17 — Subsystem checks

#### 17a utils/
FAIL
```text
��python.exe : Traceback (most recent call last):

At line:7 char:7

+ try { & $pyv -c "from utils.sanitizer import sanitize_query; from uti ...

+       ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    + CategoryInfo          : NotSpecified: (Traceback (most recent call last)::String) [], RemoteException

    + FullyQualifiedErrorId : NativeCommandError

 

  File "<string>", line 1, in <module>

ImportError: cannot import name 'sanitize_query' from 'utils.sanitizer' 

(C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\utils\sanitizer.py). Did you mean: 

'sanitize_chunk'?
```

#### 17b tests/
PASS (﻿116 files).
```text

tests/test_telegram_isolation.py: 7

tests/test_telegram_media.py: 18

tests/test_telegram_runner.py: 41

tests/test_telegram_state.py: 10

tests/test_telemetry_kill.py: 18

tests/test_terminal_contract.py: 27
```

#### 17c sync/
PASS.
```text
﻿sync/: import OK
```

#### 17d agentic/
PASS (wrapper warning noted).
```text
﻿python.exe :   [ERR ] Could not execute GitHub CLI (gh): [WinError 193] %1 is not a valid Win32 application
At line:12 char:1
+ & $pyv -m agentic.cli status 2>&1 | Out-File .\agentic_status.txt -En ...
+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    + CategoryInfo          : NotSpecified: (  [ERR ] Could ...n32 application:String) [], RemoteException
    + FullyQualifiedErrorId : NativeCommandError
 

CyClaw Agentic Status
---------------------
  enabled............... False
  repo.................. cgfixit/CyClaw
  mode.................. write
  writes_enabled........ True
  gh_min_version........ 2.40.0
  registry_path......... C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\agentic\skills_registry.json
  allowed_read_ops...... pr_view, pr_list, pr_diff, issue_view, issue_list, repo_view
  registry_version...... 0
  skills................ (none)
```

#### 17e .Codex/
PASS.
```text
﻿OK
```

#### 17f .github/
PASS.
```text
﻿PASS_ALL
```

## Issues Found

- FAIL: `config.yaml` check `models.grok.enabled == false` failed.
- FAIL: `utils.sanitizer` import target `sanitize_query` missing.
- FAIL: `terminal_emulation.py` not encoding-safe for CP1252 in this environment.

## Recommendations

- Align config policy and verifier expectations for grok.
- Normalize `utils.sanitizer` API (preferred symbol or compatibility alias).
- Make terminal emulation Unicode-safe for Windows legacy shells.

## Appendix A — Full pytest Output
```text
��........................................................................ [  2%]

........................................................................ [  4%]

........................................................................ [  7%]

........................................................................ [  9%]

..................s.s...............s................................... [ 12%]

........................................................................ [ 14%]

........................................................................ [ 17%]

............................................ss.......................... [ 19%]

..............................................................s......... [ 22%]

................s.........................................ss..........s. [ 24%]

............................sss......................................... [ 27%]

........................................................................ [ 29%]

........................................................................ [ 31%]

........................................................................ [ 34%]

...........................................sssssssssssssssssssssssssssss [ 36%]

sssss...............ssssssssssssssssssssssssssssssssssssssssssssssssssss [ 39%]

ssssssssssssssssssssss..ss............ssssssssssssssssssssssssssssssssss [ 41%]

........................................................................ [ 44%]

........................................................................ [ 46%]

........................................................................ [ 49%]

........................................................................ [ 51%]

........................................................................ [ 54%]

........................................................................ [ 56%]

........................................................................ [ 59%]

........................................................................ [ 61%]

........................................................................ [ 63%]

.........................................................ss............. [ 66%]

........................................................................ [ 68%]

........................................................................ [ 71%]

.......................................................sssssssss........ [ 73%]

ssssssssss..............ssss............................................ [ 76%]

........................................................................ [ 78%]

........................................................................ [ 81%]

........................................................................ [ 83%]

........................................................................ [ 86%]

........................................................................ [ 88%]

........................................................................ [ 90%]

........................................................................ [ 93%]

........................................................................ [ 95%]

...........s............................................................ [ 98%]

................................................                         [100%]

PYTEST_EXIT=0
```

## Appendix B — Full RAG Smoke Output
```text
��python.exe : Warning: You are sending unauthenticated requests to the HF Hub. Please set a HF_TOKEN to enable higher 

rate limits and faster downloads.

At line:6 char:1

+ & $pyv tests/ci_rag_smoke.py 2>&1 | Tee-Object .\rag_smoke.txt

+ ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

    + CategoryInfo          : NotSpecified: (Warning: You ar...ster downloads.:String) [], RemoteException

    + FullyQualifiedErrorId : NativeCommandError

 

=== Real Offline RAG Query Smoke (ChromaDB + BM25 + RRF) ===

Configured min_score gate: 0.028

Building real index from data/corpus ...



Loading weights:   0%|          | 0/103 [00:00<?, ?it/s]

Loading weights: 100%|##########| 103/103 [00:00<00:00, 4303.09it/s]



Batches:   0%|          | 0/2 [00:00<?, ?it/s]

Batches:  50%|#####     | 1/2 [00:02<00:02,  2.71s/it]

Batches: 100%|##########| 2/2 [00:04<00:00,  1.97s/it]

Batches: 100%|##########| 2/2 [00:04<00:00,  2.08s/it]



Batches:   0%|          | 0/1 [00:00<?, ?it/s]

Batches: 100%|##########| 1/1 [00:01<00:00,  1.67s/it]

Batches: 100%|##########| 1/1 [00:01<00:00,  1.67s/it]



[1/4] Query: What fusion method does CyClaw use to blend semantic and keyword results?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.033333 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



[2/4] Query: How does CyClaw combine ChromaDB vector embeddings with BM25 keyword search?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.033333 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



[3/4] Query: What does CyClaw use for rate limiting to protect against DoS attacks?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.033333 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



[4/4] Query: How does CyClaw deploy and run local LLM inference offline?

  Top source: C:\Users\cgrady\Documents\CyClaw\_sandbox\cyclaw-sandbox-20260808_214317\data\corpus\cyclaw_overview.md

  Top score:  0.03254 (gate: 0.028)

  Mode:       hybrid

  PASS: vault hit above gate, correct source



All 4 real RAG queries passed (vault hits above the 0.028 gate)

RAG_EXIT=0
```

## Appendix C — metrics.py Full Output
```text
��Total events: 1704



Event breakdown:

  agentic_repo_workspace_cloned: 302

  agentic_repo_workspace_denied: 192

  agentic_real_repo_loop_iteration: 129

  agentic_harness_proposer_model_invoked: 126

  agentic_harness_proposer_model_succeeded: 126

  agentic_executor_check_result: 120

  agentic_repo_workspace_write: 112

  agentic_real_repo_loop_started: 102

  agentic_repo_workspace_git_ok: 89

  agentic_repo_workspace_git_op: 89

  agentic_real_repo_loop_accepted_pending_decision: 60

  agentic_repo_workspace_read: 51

  agentic_real_repo_loop_exhausted: 42

  agentic_skill_applied: 38

  agentic_read: 33

  agentic_real_repo_change_decided: 21

  agentic_real_repo_change_approved: 12

  agentic_read_timeout: 9

  mcp_rag_query: 9

  agentic_write_refused: 8

  agentic_read_retry: 6

  agentic_skill_injection_blocked: 4

  agentic_repo_workspace_git_failed: 4

  sqlconnect_read: 4

  agentic_real_repo_change_refused: 3

  soul_evolution_applied: 3

  agentic_repo_workspace_clone_failed: 2

  agentic_write_dryrun: 2

  mcp_rag_error: 2

  local_llm_backend_selected: 1

  sync_started: 1

  sync_file_added: 1

  sync_completed: 1



RAG queries: 9



RAG scores �� avg: 0.815, min: 0.016, max: 0.920



Retrieval modes:

  hybrid: 5

  semantic: 2

  keyword: 2



Online escalations (external LLM): 0

METRICS_EXIT=0
```