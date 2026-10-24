@AGENTS.md

# Phase status
- **Phase 0**: COMPLETE. PR #1 merged to `main` on GitHub (2026-10-14). All CI green.
- **Phase 1**: COMPLETE. PR #2 merged to `main` on GitHub (2026-10-15). All CI green. Gate passed: dca_trend/tsmom/breakout all ≥5pp lower max drawdown vs buy-and-hold across 8 assets, 12 OOS windows. Report: `research/reports/phase1_strategy_comparison.md`.
- **Phase 2**: COMPLETE. PR #3 merged to `main` on GitHub (2026-10-16). All CI green. Gate passed: paper engine ≈ backtester within ±1 trade and 2% equity; regime classifier directional signal confirmed (trending_up mean return > trending_down). Reports: `research/reports/phase2_regime_validation.md`.
- **Phase 3**: COMPLETE. PR #4 merged to `main` on GitHub (2026-10-19). All CI green. Gate: shadow mode active — A3 (News/Sentiment) and A4 (Smart Money) agents collecting data; scoring pipeline operational; non-zero Coordinator weight deferred until ≥3 months OOS data available. Report: `research/reports/phase3_shadow_signal_report.md`.
- **Phase 4**: COMPLETE. PR #5 merged to `main` on GitHub (2026-10-24). All CI green. Gate: code gate passed — Coordinator (code-side weighter + bounded LLM review), Recommendation Engine and Reflection Agent implemented and tested. Ablation on synthetic data FAILS the Sharpe/drawdown criterion as expected (strategy warm-up vs immediate-entry buy-and-hold); live LLM ablation deferred until ≥30 days of paper trading. Report: `research/reports/phase4_coordinator_ablation.md`.
- **Phase 5** (split into sub-plans 5a–5e; Phase 5 gate needs all five): **5a COMPLETE**. PR #8 merged to `main` on GitHub (2026-10-24). All 5 CI checks green. Gate: cross-user isolation proven by integration tests against real Postgres (forced RLS, non-owner NOBYPASSRLS role) and Redis in CI; API startup refuses an unsafe DB login role. Threat model: `docs/threat-model.md`. Plan: `docs/superpowers/plans/2026-10-24-phase5a-tenancy-api-foundations.md`.
- **Next**: Phase 5b — passkey (WebAuthn) auth, sessions, recovery, step-up. Write its plan first (`superpowers:writing-plans`), then `superpowers:subagent-driven-development`. Carry-overs from 5a are listed in PR #8 (request dependency on `tenant_connection`, reject disabled users, SECURITY DEFINER fns for user creation/invite redemption, uvicorn access-log config, 422 input echo, migration checksum). Later: 5c keys + exchange adapter + execution, 5d workers/allocation/co-pilot/audit, 5e Next.js + Telegram.
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
