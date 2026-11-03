# Product Requirements Document: CLI-to-AI Command Center

**Project Name:** `aicli` (CLI-to-AI Universal Command Center)  
**Version:** 1.0 PRD  
**Date:** September 2026  
**Status:** Research & Planning

---

## Executive Summary

A privacy-first, cost-optimized CLI tool that unifies interaction with multiple LLM providers, local models, and AI tools through a single command interface. Users manage prompts, route requests intelligently across models, track costs, and maintain audit trails—all without ever leaving the terminal.

**Core Value Proposition:**
- **One command, any model** – Switch between Claude, Gemini, DeepSeek, local Ollama without code changes
- **Privacy by default** – Local-first with optional cloud fallback
- **Cost-aware routing** – Automatically select the cheapest qualified model per task
- **Full transparency** – Every API call logged, auditable, reproducible

---

## Market Analysis & Competitive Landscape

### What Exists Today (as of Sept 2026)

#### Direct CLI Agents
- **Claude Code** (Anthropic) – Cloud-only, IDE-focused, closed-source
- **Gemini CLI** (Google) – Free tier, but locked to Gemini only
- **Aider** (Apache 2.0) – Git-integrated pair programmer, model-agnostic but code-focused
- **Goose CLI** (Block) – Multi-model but closed ecosystem
- **OpenCode** – Archived, minimal adoption

#### Model Abstraction Layer Tools
- **LLM CLI** (Simon Willison, 44K+ stars) – Excellent for prompt experimentation, not orchestration-focused
- **LiteLLM** – Python library with fallback routing, but not CLI-first; requires integration
- **CometAPI** – Unified API, but proprietary; costs per request

#### Multi-Model Routers (Gateway Layer)
- **Bifrost** (Maxim AI) – Enterprise-grade, CEL rules, sub-microsecond routing, **closed source**
- **OpenRouter** – Unified interface, but adds 20-30% markup
- **Amazon Bedrock Intelligent Routing** – AWS-locked, margin-based, limited transparency

#### Local LLM Runners
- **Ollama** – Best-in-class for running local models, but limited tool integration
- **LM Studio** – GUI-first, less scriptable
- **LocalAI** – OpenAI-compatible drop-in, but resource-heavy
- **llama.cpp** – Low-level, steep learning curve

#### Agent Orchestration Frameworks (Python/TypeScript)
- **LangChain + LangGraph** (119K stars) – General-purpose, requires custom CLI wrapper
- **LlamaIndex** (44K stars) – Retrieval-optimized, good data connectors, not CLI-native
- **AutoGPT** – Complex, overkill for simple tasks
- **OpenHands** – Full dev environment, heavyweight

#### Tool Integration (Emerging Standard)
- **Model Context Protocol (MCP)** – Anthropic's open standard, 10,000+ public servers as of Mar 2026, becoming de facto

---

## Gap Analysis

### What's Missing

1. **No unified CLI tool** that:
   - Abstracts multiple model providers AND local models in one command
   - Provides intelligent cost-aware routing natively (not via separate gateway)
   - Works entirely offline with graceful cloud fallback
   - Integrates MCP servers as first-class citizens

2. **No privacy-by-default solution** that:
   - Defaults to local inference, optionally routes to cloud on demand
   - Maintains audit logs of every API call
   - Doesn't require external account setup to use (e.g., works with just Ollama)

3. **No cost transparency tool** that:
   - Estimates cost before execution
   - Routes based on cost/latency/accuracy constraints
   - Shows cost breakdowns per prompt, per model

4. **No simple multi-turn context management** in a CLI that:
   - Persists conversation state locally
   - Allows prompt fragments and template reuse
   - Integrates with your project structure automatically

---

## Product Vision

### User Personas

**Persona 1: Privacy-Focused Developer**
- Runs models locally via Ollama
- Never wants data in cloud unless explicitly chosen
- Wants transparency on what leaves their machine
- Works in secure/offline environments sometimes

**Persona 2: Cost-Conscious Startup**
- Uses multiple models (Claude for reasoning, DeepSeek for bulk, local Phi for high-volume)
- Wants automatic routing to optimize spend
- Needs cost tracking and budgets
- CI/CD integration for batch processing

**Persona 3: Researcher / Prompt Engineer**
- Rapidly iterating on prompts across models
- Wants reproducible runs and version control
- Tests model outputs side-by-side
- Integrates with Git workflows

**Persona 4: AI Automation Builder**
- Scripting workflows that call multiple models and tools
- Needs MCP server integration for accessing external data
- Wants JSON output for piping
- Integrates with GitHub Actions, Zapier, Make.com

---

## Phase 1: Quick Win (MVP) – Est. 4-6 weeks

### Scope: Bring the "LLM CLI" experience to multi-model routing

**Core Features:**

1. **Unified prompt execution**
   ```bash
   aicli "What is machine learning?" --model claude-sonnet
   aicli "What is machine learning?" --model gpt-4o
   aicli "What is machine learning?" --model local/mistral  # Local via Ollama
   ```

2. **Smart provider detection & fallback**
   - Check available providers (local Ollama, API keys in env)
   - If provider offline, fallback to next configured provider
   - Log the fallback decision

3. **Simple config file** (`~/.aicli/config.yml`)
   ```yaml
   providers:
     - name: claude
       type: anthropic
       api_key: ${ANTHROPIC_API_KEY}
       models: [claude-opus, claude-sonnet]
     - name: openai
       type: openai
       api_key: ${OPENAI_API_KEY}
       models: [gpt-4o, gpt-4-turbo]
     - name: local
       type: ollama
       base_url: http://localhost:11434
       models: [mistral, llama3.2]

   defaults:
     model: claude-sonnet
     output: text  # or json, markdown
   ```

4. **Conversation storage** (SQLite)
   - Store prompts + responses locally
   - Searchable by model, date, tags
   - Export to markdown/JSON

5. **Cost estimation (read-only)**
   - Display estimated cost before execution
   - Actual cost after completion
   - Running total per session

6. **Basic output formats**
   - Plain text
   - JSON (for piping)
   - Markdown (for docs)

7. **Model listing & comparison**
   ```bash
   aicli list models
   aicli list providers
   aicli stats              # Cost/latency this session
   ```

### What's NOT in Phase 1
- Agent loops / tool calling (Phase 2)
- MCP server integration (Phase 2)
- GUI dashboard (Phase 3)
- Batch processing (Phase 2)
- Multi-turn chat with state (Phase 1.5)

### Technology Stack (Phase 1)

| Layer | Technology | Rationale |
|-------|-----------|-----------|
| **Language** | Python 3.11+ | Rich CLI lib ecosystem; data scientist friendly |
| **CLI Framework** | Typer + Rich | Modern, async-ready, great UX |
| **Config** | Pydantic + YAML | Type-safe, validation, no external deps |
| **API Abstraction** | LiteLLM (v1.0+) | 100+ providers, mature, Apache 2.0 |
| **Local LLMs** | Ollama (detect) | Most popular, lowest friction |
| **Storage** | SQLite (local) | Zero setup, portable, searchable |
| **HTTP Client** | httpx (async) | Fast, modern alternative to requests |
| **Build/Deploy** | uv + PyPI | Fast lockfile, modern Python packaging |
| **Testing** | pytest + pytest-asyncio | Standard, good async support |

### User Workflow (Phase 1)

```bash
# Install
pip install aicli

# One-time setup
aicli config --set anthropic.key $YOUR_KEY
aicli config --set openai.key $YOUR_KEY
aicli config detect-local     # Finds local Ollama

# Use
aicli "Explain quantum computing"
aicli "Explain quantum computing" --model gpt-4o --format json
aicli chat                        # Start multi-turn (exits with :quit)
aicli history                     # Show past prompts
aicli search "quantum"            # Search past responses
```

### Success Metrics (Phase 1)

- **Adoption:** 500+ GitHub stars, 2K+ weekly installs on PyPI
- **Feature Completeness:** All core features ship within 4 weeks
- **Performance:** Prompt-to-response <2s for cloud, <1s for local
- **Reliability:** 99% uptime; fallback logic works in 95% of provider-down scenarios
- **User Satisfaction:** No blocked critical issues in first month

---

## Phase 2: Agent & Orchestration (Weeks 7-14)

**Features:**
- **Tool calling** – Models can invoke MCP tools (filesystem, web search, Git)
- **Batch processing** – Run 100+ prompts with cost aggregation
- **Cost-aware routing** – Route by: min cost, min latency, exact model constraint
- **Multi-turn with memory** – Persistent conversation context
- **Prompt composition** – Templates with variables and fragments
- **Agentic loops** – ReAct-style loop (Thought → Action → Observation → Final Answer)

---

## Phase 3: Ecosystem & Scale (Weeks 15+)

**Features:**
- **Web dashboard** – Real-time cost, model health, conversation browser
- **GitHub Actions integration** – Run batch jobs in CI/CD
- **Slack/Discord integration** – Bot for team prompting
- **MCP ecosystem integration** – One-click install public MCP servers
- **Team features** – Shared budgets, team APIs, audit logs
- **Advanced routing** – SLA constraints, cost per token unit, model families

---

## Technical Architecture (Phase 1)

### System Diagram

```
┌─────────────────────────────────────────────────────┐
│                    User CLI                         │
│  aicli "prompt" --model claude-sonnet --format json │
└────────────────┬────────────────────────────────────┘
                 │
         ┌───────▼────────────────────────┐
         │    CLI Parser (Typer)          │
         │  - Parse args/options          │
         │  - Load config                 │
         └───────┬────────────────────────┘
                 │
         ┌───────▼──────────────────────────────┐
         │   Routing Engine                     │
         │  - Resolve model → provider          │
         │  - Check health / available          │
         │  - Estimate cost                     │
         │  - Select fallback if needed         │
         └───────┬──────────────────────────────┘
                 │
      ┌──────────┴──────────┬──────────────┐
      │                     │              │
   ┌──▼──┐            ┌──────▼────┐   ┌───▼─────┐
   │Local│            │  Anthropic│   │  OpenAI │
   │Ollama            │ Claude API│   │ API     │
   └──┬──┘            └──────┬────┘   └───┬─────┘
      │                      │             │
      └──────────┬───────────┴─────────────┘
                 │
         ┌───────▼─────────────────┐
         │  Response Processor     │
         │  - Format output        │
         │  - Calculate actual cost│
         │  - Save to history DB   │
         └───────┬─────────────────┘
                 │
         ┌───────▼──────────────────┐
         │   Output Formatter       │
         │  - Plain text / JSON     │
         │  - Markdown / syntax     │
         └──────────┬───────────────┘
                    │
            ┌───────▼─────────┐
            │  Stdout / File  │
            └─────────────────┘
```

### Key Components

**1. Config Manager** (`config.py`)
- Load from `~/.aicli/config.yml`
- Validate provider setup
- Resolve env vars
- Pydantic models for type safety

**2. Provider Registry** (`providers/`)
- Abstract base: `BaseProvider`
- Implementations: `AnthropicProvider`, `OpenAIProvider`, `OllamaProvider`
- Health checks, model listing, rate limits

**3. Routing Engine** (`router.py`)
- Parse model string: `"anthropic/claude-sonnet"` → Provider + Model
- Cost estimator (tokens × price)
- Fallback logic: if provider down, try next
- Load balancing: round-robin / random

**4. CLI Commands** (`commands/`)
- `run` – single prompt execution
- `chat` – multi-turn conversation
- `history` – search past prompts
- `list` – models, providers, stats
- `config` – setup and management

**5. Storage Layer** (`storage.py`)
- SQLite schema: `prompts`, `responses`, `cost_log`
- Full-text search on prompt + response
- Indexed by date, model, provider

**6. Formatters** (`formatters/`)
- PlainTextFormatter
- JSONFormatter (structured)
- MarkdownFormatter (docs)
- TerminalFormatter (colors, tables)

---

## Competitive Positioning

| Feature | aicli | LLM CLI | Aider | LiteLLM | OpenRouter |
|---------|-------|---------|-------|---------|-----------|
| **Single Command** | ✅ Yes | ✅ Yes | ❌ Code-focused | ❌ Library | ❌ API |
| **Multi-Model** | ✅ All | ✅ All | ✅ Config | ✅ Config | ✅ Yes |
| **Local Priority** | ✅ Default | ❌ Cloud | ❌ Cloud | ❌ Cloud | ❌ Cloud |
| **Cost Tracking** | ✅ Native | ❌ No | ❌ No | ❌ No | ❌ Limited |
| **MCP Ready** | ✅ Phase 2 | ❌ No | ❌ No | ❌ No | ❌ No |
| **Price** | 💰 Free | 💰 Free | 💰 Free | 💰 Free | 💰 $0-$∞ |
| **Privacy** | 🔒 Local-first | 🔒 Yes | 🔒 Yes | ⚠️ Via config | ❌ No |

---

## Go-to-Market Strategy

### Phase 1 Launch (Week 6)
1. **Release to ProductHunt** – "One CLI for every LLM"
2. **OSS community engagement** – LLM newsletter, HN, Reddit
3. **GH Actions template** – Starter "batch prompt runner"
4. **Discord/community** – Early adopter feedback loop

### Messaging
- For developers: "ChatGPT in your terminal, any model, zero lock-in"
- For researchers: "Reproducible multi-model prompt experiments"
- For startups: "Control your AI spend, automate routing"

### Growth Channels
- **Tier 1:** LLM engineer communities (LangChain Discord, Anthropic forums)
- **Tier 2:** OSS communities (Ollama, LiteLLM users)
- **Tier 3:** AI Twitter, blogs, newsletters

---

## Implementation Roadmap

### Phase 1 (Weeks 1-4)
- [ ] Core CLI structure (Typer + config)
- [ ] LiteLLM abstraction layer
- [ ] SQLite history storage
- [ ] Single-turn prompt execution
- [ ] Cost estimation (estimated + actual)
- [ ] Basic output formats (text, JSON, Markdown)
- [ ] Config management (`aicli config`)
- [ ] Model listing and provider detection
- [ ] Comprehensive testing (60%+ coverage)

### Phase 1.5 (Weeks 5-6)
- [ ] Multi-turn chat (`aicli chat`)
- [ ] History search and filtering
- [ ] Better error handling & fallback logging
- [ ] Documentation & tutorial
- [ ] Public PyPI release
- [ ] GitHub Actions starter template

---

## Risk Assessment & Mitigation

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|-----------|
| API rate limits break cost logic | Medium | Low | Cache pricing, validate against docs |
| Local Ollama detection fragile | Medium | Medium | Robust health checks, fallback gracefully |
| LiteLLM dependency lock-in | Medium | Low | Abstract behind our own provider interface |
| User confusion: model pricing varies | Medium | High | Clear docs on how to estimate costs |
| Competitor releases similar tool | High | High | Move fast (Phase 1 in 6 weeks), differentiate on MCP |

---

## Success Criteria

### Phase 1 Success = MVP Launch
- **Functional:** All features documented, tested, ship-ready
- **Usable:** Zero confusing errors; setup takes <5 min for any provider
- **Community:** 300+ GitHub stars, 5+ community contributors within month
- **Momentum:** Clear path to Phase 2 (agent loops, MCP)

### Long-term Vision
By end of 2027, `aicli` is the **de facto CLI for multi-model AI**, used by:
- 50K+ developers monthly
- Teams running cost-optimized AI pipelines
- Researchers experimenting with model combinations
- Privacy-conscious users who don't want cloud
- Enterprises with internal MCP servers

---

## Appendix: Existing Tools Deep Dive

### LLM CLI (Simon Willison, 44K+ stars)
**Strengths:**
- Mature, well-designed API abstraction
- Excellent for prompt experimentation
- Good plugin system

**Weaknesses:**
- Primarily single-model at a time
- Not designed for cost-aware routing
- No agentic features
- Less focus on local models

**Why not fork/extend?** Different architecture needed for routing layer.

### Aider
**Strengths:**
- Multi-file code editing
- Git integration
- Well-tested against many models

**Weaknesses:**
- Code-focused, not general-purpose
- Not optimized for non-coding tasks
- No cost tracking

**Why not use?** Different problem domain (code editing vs. general CLI).

### LiteLLM (Main Dependency)
**Strengths:**
- 100+ provider support (Anthropic, OpenAI, Bedrock, Azure, Cohere, Ollama, etc.)
- Active maintenance
- Apache 2.0 license
- Mature fallback & retry logic

**Why use as dependency?** De facto standard; saves 3-4 weeks of provider integration.

---

## Questions for Stakeholders

1. **Timeline:** Is 6 weeks realistic for Phase 1 given your team size?
2. **Focus:** Should we target developers first, or researchers, or startups?
3. **Licensing:** Apache 2.0 or dual licensing (community + commercial)?
4. **Monetization:** Free tier + Pro tier (cost optimization) or pure OSS?
5. **Cloud vs. SaaS:** Pure CLI tool, or hosted dashboard later (Phase 3)?

---

**Document Version:** 1.0  
**Last Updated:** September 23, 2026  
**Status:** Ready for review & feedback
