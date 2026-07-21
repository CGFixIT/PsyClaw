# CyClaw: Cybersecurity Knowledge Base

CyClaw is a hybrid retrieval system combining semantic search with keyword-based retrieval using BM25 algorithm. It provides intelligent question-answering capabilities for cybersecurity topics.

## Core Features

- **Hybrid Retrieval**: Combines ChromaDB vector embeddings with BM25 keyword search
- **RRF Fusion**: Uses Reciprocal Rank Fusion to blend semantic and keyword results
- **Security Hardening**: Includes prompt injection detection and input validation
- **Rate Limiting**: Protects against DoS attacks with configurable rate limits
- **Personality System**: Optional persistent personality for consistent responses
- **Audit Logging**: Comprehensive audit trail for compliance

## Architecture

The system uses a FastAPI server on localhost:8787 that accepts JSON queries and returns context-augmented answers from the knowledge base. The retrieval pipeline includes:

1. Query validation and sanitization
2. Semantic search via ChromaDB embeddings
3. Keyword search via BM25 index
4. Result fusion using Reciprocal Rank Fusion
5. LLM response generation with a local Ollama model, or an optional Grok/Claude fallback

## Deployment

CyClaw runs offline by default, using Ollama for local LLM inference. It can optionally escalate to Grok or Claude when high-confidence answers aren't found locally.
