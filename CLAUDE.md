@AGENTS.md

# Phase status
- **Phase 0**: COMPLETE (gate passed 2026-10-13). PR #1 open: `phase-0-foundations` → `main` on GitHub. Merge once CI is green.
- **Next**: Phase 1 — market data ingestion (A1 agent), event-driven backtester, baseline strategies. Plan: `PLAN.md`. SDD ledger: `.sdd/progress.md`. Start by reading PLAN.md Phase 1 section and invoking `superpowers:subagent-driven-development`.
- **Local branch**: `main` is at `e3697fc`. `phase-0-foundations` branch tracks the PR.

# Claude Code specifics
- Plugins: superpowers, security-guidance, code-review, pr-review-toolkit, feature-dev, pyright-lsp, typescript-lsp, commit-commands, claude-md-management, frontend-design, Trail of Bits skills, codex (openai/codex-plugin-cc). See PLAN.md 8.2–8.3.
- Before merging changes to auth/, keys/, risk/, execution/: run /security-review, the risk-auditor or security-reviewer subagent, and /codex:review (adversarial mode for risk/ and execution/).
- Never delegate changes to auth/, keys/, risk/, execution/ to Codex; delegation only for scoped low-risk tasks in a separate worktree.
- Codex review gate: off by default; enable only during focused risk/ or execution/ work.
- LLM model IDs (verify against Anthropic docs before use):
  - High-volume / news classifier: claude-haiku-4-5-20251001
  - Coordinator review + Reflection: claude-sonnet-4-6
  - Never hardcode model IDs in logic code; read from config/env.
