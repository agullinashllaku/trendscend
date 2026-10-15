@AGENTS.md

# Phase status
- **Phase 0**: COMPLETE. PR #1 merged to `main` on GitHub (2026-10-14). All CI green.
- **Phase 1**: COMPLETE. PR #2 open on GitHub (2026-10-15), CI green. Gate passed: dca_trend/tsmom/breakout all ≥5pp lower max drawdown vs buy-and-hold across 8 assets, 12 OOS windows. Report: `research/reports/phase1_strategy_comparison.md`.
- **Next**: Phase 2 — A2 Regime Classifier, U3 Risk Manager, paper trading. Merge PR #2 first, then `git pull origin main`, read PLAN.md Phase 2 section, invoke `superpowers:subagent-driven-development`.
- **Phase 1.1 cleanup backlog** (non-blocking): rebalance_core monthly calendar, realized_vol annualisation param, DataLoader deduplication, candles_ux index direction, grid_range ADX period constant.

# Claude Code specifics
- Plugins: superpowers, security-guidance, code-review, pr-review-toolkit, feature-dev, pyright-lsp, typescript-lsp, commit-commands, claude-md-management, frontend-design, Trail of Bits skills, codex (openai/codex-plugin-cc). See PLAN.md 8.2–8.3.
- Before merging changes to auth/, keys/, risk/, execution/: run /security-review, the risk-auditor or security-reviewer subagent, and /codex:review (adversarial mode for risk/ and execution/).
- Never delegate changes to auth/, keys/, risk/, execution/ to Codex; delegation only for scoped low-risk tasks in a separate worktree.
- Codex review gate: off by default; enable only during focused risk/ or execution/ work.
- LLM model IDs (verify against Anthropic docs before use):
  - High-volume / news classifier: claude-haiku-4-5-20251001
  - Coordinator review + Reflection: claude-sonnet-4-6
  - Never hardcode model IDs in logic code; read from config/env.
