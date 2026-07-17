"""Analysis agents.

Phase 2 quantitative (chart) analysis lives in `src.agents.chart`: deterministic
indicator + structure plugins that EXTRACT facts (no judgment, no LLM). The
claude -p judgment agent interprets those facts using the harness rules in
docs/chart-judgment-rules.md. LLM backend (`llm_backend.ClaudeCliBackend`) is
used only by that judgment layer.

See docs/chart-analysis-design.md (symlinked to Obsidian Vault).
"""
