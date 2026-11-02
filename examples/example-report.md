# Agent PR Audit — an illustrative repository

> **This example is fabricated** to show the report format. The numbers are not from a
> real audit. A real report names the repository, window and sample size, and every
> finding carries a PR number and the matching line.

**Window:** 2026-08-01 → 2026-11-01 · **Sampled:** 120 of 1,080 unique agent-authored PRs · **Method:** GitHub API, read-only

## Summary

- 108 of 120 sampled PRs (90%) touch tests, CI, lint or CODEOWNERS.
- 89 merged; **12 (13%) with no formal review object**.
- Median time from open to merge: **2.58 h**.
- Test lines added vs deleted across sampled PRs: **54,814 / 11,702**.
- 12 PRs contain a bypass-style marker (2 CI-level, 4 test skip, 6 suppression); each is a lead to read, not a verdict.

## Findings

- **HIGH** #1234 (devin) — bypass-style marker: CI continue-on-error — `.github/workflows/test-unit.yml`: `continue-on-error: true`
- **HIGH** #1198 (copilot) — merged with no formal review; touches tests; 340 lines
- **MEDIUM** #1240 (cursor) — merged in 0.36 h with 15,154 changed lines
- **MEDIUM** #1211 (jules) — removes more assertions (95) than it adds (0)
- **LOW** #1203 (devin) — type/lint suppression — `src/cache.py`: `except Exception:  # noqa: BLE001`

## Recommendations

1. Require a human review on any agent PR that touches a guardrail file (tests, CI, lint, CODEOWNERS).
2. Treat `continue-on-error`, `|| true`, `if: false` and new skips in CI as review-blocking changes.
3. Record the reviewer and the check that passed on every agent PR before merge.
4. Re-run this audit monthly; compare the review gap, not just the volume.

## Limits

- Sampled evenly across the unique PR set; GitHub search caps at 1,000 results per query.
- "No formal review" = no review object via the API; a human may have reviewed by other means.
- File paths and line counts are not intent; read the diffs before acting on a finding.
- Read-only: no repository was modified, no CI triggered, no code executed.
