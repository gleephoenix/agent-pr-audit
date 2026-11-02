# agent-pr-audit

A read-only audit of the pull requests your AI coding agents opened.

**What it answers:** which agent PRs touched the files that gate your build (tests, CI,
lint config, CODEOWNERS), which merged without a formal review, how fast they merged
against their size, and which added a bypass (`skip`, `continue-on-error`, `|| true`,
`if: false`, type/lint suppression).

No write access, no CI triggers, no code execution. Public GitHub data only.

```bash
export GITHUB_TOKEN=ghp_...        # read-only classic or fine-grained token
python3 agent_pr_audit.py \
  --repo owner/name \
  --since 2026-08-01 --until 2026-11-01 \
  --out audits/owner_name \
  --max-prs 120
```

Outputs:

| File | Content |
|---|---|
| `report.md` | summary, severity-ranked findings with PR numbers and the exact added line, recommendations, limits |
| `findings.csv` | every finding, machine-readable |
| `audit.json` | full evidence: per-PR files, patches, categories, review states, merge times |

## What it measures

- **Volume and merge behaviour by agent** — PRs found, merged, median open→merge time.
- **Guardrail surface** — tests, CI workflows/config, lint/type config, CODEOWNERS.
- **Review coverage** — formal review objects on merged PRs (approvals, comments,
  change requests). "No review" means no review object in the API.
- **Test integrity** — test lines and assertion-like lines added vs deleted.
- **Bypass markers** — skipped tests, `continue-on-error`, `|| true`, `if: false`,
  type/lint suppressions, broad CI permissions — with the matching line.
- **Merge velocity vs size** — large diffs merged in under an hour.

## Severity calibration

Bypass markers are **leads, not verdicts**:

| Severity | Meaning |
|---|---|
| HIGH | a CI-level bypass was merged, or a guardrail PR merged with no formal review |
| MEDIUM | a test skip, a large diff merged in under an hour, or more assertions removed than added |
| LOW | a suppression comment (often justified) |

Read the diff before acting. The report includes the matching line so you can judge in
seconds.

## Limits

- GitHub search caps at 1,000 results per query; high-volume repos are sampled evenly
  from the first 1,000 and the report says "sampled N of M".
- File lists are fetched 100 at a time (two pages); very large PRs are truncated.
- Patch hunks are truncated at 20,000 characters per file.
- "No formal review" is an API fact, not proof that no human looked.
- Public repositories only. The sample is not a census, and file paths are not intent.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The tests are offline. They cover path classification, patch parsing, bypass-marker
detection (including a regression for `WaitForExit(` matching the `xit(` skip pattern),
and the sampling function.

## Ethics

- Public data only; no repository is contacted, no CI is triggered, no code is executed.
- Do not use this to harass a project. The report is about patterns, not people.
- If you publish results, give the vendor a right of reply and state the limits.

## Who made this

Built and run by [GLEE](https://gleephoenix.com), an independent AI lab in Canada, as the
delivery engine behind its Agent PR Audit. We run the full audit — including a human
read of every HIGH finding and a policy page — as a service starting at $250 for one
repository and 90 days: [gleephoenix.com/bench](https://gleephoenix.com/bench).

The tool is MIT-licensed. Run it yourself; hire us when you want the readout.

## License

MIT — see [LICENSE](LICENSE).
