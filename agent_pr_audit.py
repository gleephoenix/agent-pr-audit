"""agent-pr-audit — read-only audit of AI-agent-authored pull requests.

MIT License. See LICENSE.

Usage:
  export GITHUB_TOKEN=...           # or have `gh auth token` available
  python3 agent_pr_audit.py --repo owner/name --since 2026-08-01 --until 2026-11-01 \
      --out audits/owner_name --max-prs 120 [--anonymize]

Reads public GitHub data only: search, pulls/{n}/files, pulls/{n}/reviews.
Never writes to GitHub, never triggers CI, never executes repository code.
"""
import argparse, json, re, subprocess, time, os, csv, statistics, datetime

AGENTS = {
    "copilot-swe-agent[bot]": "Copilot SWE agent",
    "devin-ai-integration[bot]": "Devin",
    "cursor[bot]": "Cursor",
    "google-labs-jules[bot]": "Jules",
}

CI_WF = re.compile(r'(^|/)\.github/workflows/|(^|/)\.gitlab-ci\.ya?ml$|(^|/)Jenkinsfile$|azure-pipelines', re.I)
CI_OTHER = re.compile(r'(^|/)\.github/(?!workflows/)|(^|/)\.circleci/|(^|/)\.buildkite/|codecov\.ya?ml$|(^|/)CODEOWNERS$', re.I)
TESTS = re.compile(r'(^|/)(tests?|__tests__|spec|specs)/|(^|/)test_[^/]+\.py$|_test\.go$|(\.|_)(test|spec)\.[a-z]+$', re.I)
LINT = re.compile(r'(^|/)(\.eslintrc|eslint\.config|\.prettierrc|prettier\.config|ruff\.toml|\.ruff\.toml|\.flake8|setup\.cfg|pyproject\.toml|tsconfig[^/]*\.json|biome\.json|\.pylintrc|\.editorconfig|\.pre-commit-config\.yaml)', re.I)
DEPS = re.compile(r'(^|/)(package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock|requirements[^/]*\.txt|poetry\.lock|Pipfile|go\.mod|go\.sum|Cargo\.toml|Cargo\.lock|pom\.xml|build\.gradle[^/]*|Gemfile|Gemfile\.lock|composer\.json|composer\.lock)$', re.I)
SECRETS = re.compile(r'(^|/)(\.env[^/]*|secrets?[^/]*|credentials[^/]*|\.npmrc|\.pypirc)$', re.I)
AUTH = re.compile(r'(^|/)[^/]*(auth|oauth|permission|acl|security|rbac|jwt|session|crypto)[^/]*$', re.I)
INFRA = re.compile(r'(^|/)(Dockerfile[^/]*|docker-compose[^/]*|\.dockerignore|\.tf$|k8s/|helm/|chart\.yaml)', re.I)
CATS = [("tests", TESTS), ("ci_workflow", CI_WF), ("ci_other", CI_OTHER), ("lint_config", LINT),
        ("deps", DEPS), ("secrets", SECRETS), ("auth", AUTH), ("infra", INFRA)]
GUARD = {"tests", "ci_workflow", "ci_other", "lint_config"}

WEAKEN = [
    ("skipped test", re.compile(r'\.skip\s*\(|pytest\.mark\.skip|@unittest\.skip|describe\.skip|it\.skip|test\.skip|(?<![A-Za-z])xit\(|(?<![A-Za-z])xdescribe\(')),
    ("expected-failure marker", re.compile(r'xfail')),
    ("CI continue-on-error", re.compile(r'continue-on-error\s*:\s*true', re.I)),
    ("CI allow_failure", re.compile(r'allow_failure\s*:\s*true', re.I)),
    ("CI force-true / no-verify", re.compile(r'\|\|\s*true\b|(?:^|[;&|])\s*exit\s+0\b|--exit-zero|--no-verify')),
    ("CI disabled step", re.compile(r'if\s*:\s*(false|\$\{\{\s*false\s*\}\})', re.I)),
    ("type/lint suppression", re.compile(r'#\s*noqa|#\s*type:\s*ignore|@ts-ignore|@ts-nocheck|eslint-disable|nolint')),
    ("broad CI permissions", re.compile(r'permissions\s*:\s*write-all', re.I)),
]
ASSERT = re.compile(r'\bassert\b|\bexpect\s*\(|\bshould\b|t\.Error|require\.|assertEqual|assertThat')
TESTF = re.compile(r'tests?/|__tests__|spec/|_test\.|\.test\.|\.spec\.', re.I)
CIF = re.compile(r'\.github/workflows/|\.github/|Jenkinsfile|\.gitlab-ci|\.circleci', re.I)


VERSION = "0.1.0"


def token():
    """GITHUB_TOKEN/GH_TOKEN first, then the gh CLI's stored token, else None."""
    for env in ("GITHUB_TOKEN", "GH_TOKEN"):
        val = os.environ.get(env, "").strip()
        if val:
            return val
    try:
        return subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    except Exception:
        return None


TOK = token()


def api(url, tries=5):
    headers = ["-H", "Accept: application/vnd.github+json"]
    if TOK:
        headers += ["-H", f"Authorization: token {TOK}"]
    for i in range(tries):
        out = subprocess.check_output(
            ["curl", "-sS", *headers, url], text=True)
        try:
            d = json.loads(out)
        except Exception:
            time.sleep(2); continue
        if isinstance(d, dict) and str(d.get("message", "")).startswith("API rate limit"):
            time.sleep(30); continue
        return d
    return {"error": "api_failed", "url": url}


def search_prs(repo, login, since, until, cap_pages=10):
    q = f"repo:{repo} is:pr author:{login} created:{since}..{until}"
    items, page, reported = [], 1, None
    while page <= cap_pages:
        url = ("https://api.github.com/search/issues?"
               + "q=" + __import__("urllib.parse", fromlist=["quote"]).quote(q)
               + f"&per_page=100&page={page}&sort=created&order=desc")
        d = api(url)
        if reported is None:
            reported = d.get("total_count")
        got = d.get("items", [])
        items.extend(got)
        if len(got) < 100 or len(items) >= 1000:
            break
        page += 1
        time.sleep(2)
    return items, reported


def even_sample(items, n):
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def classify(paths):
    cats = set()
    for p in paths:
        for name, rx in CATS:
            if rx.search(p):
                cats.add(name)
    return cats


def parse_patch(patch):
    a = [l[1:] for l in patch.splitlines() if l.startswith('+') and not l.startswith('+++')]
    d = [l[1:] for l in patch.splitlines() if l.startswith('-') and not l.startswith('---')]
    return a, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-prs", type=int, default=120)
    ap.add_argument("--anonymize", action="store_true")
    ap.add_argument("--version", action="version", version=f"agent-pr-audit {VERSION}")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    if not TOK:
        print("WARNING: no GITHUB_TOKEN and no gh token; unauthenticated search is "
              "limited to 10 requests/minute and will be slow.", flush=True)

    all_items, totals, reported_totals = [], {}, {}
    for login in AGENTS:
        items, reported = search_prs(args.repo, login, args.since, args.until)
        totals[login] = len(items)
        reported_totals[login] = reported
        all_items.extend(items)
        print(f"{login}: {len(items)} fetched (search total {reported})", flush=True)
    # dedupe by number
    seen, uniq = set(), []
    for it in all_items:
        if it["number"] in seen: continue
        seen.add(it["number"]); uniq.append(it)
    uniq.sort(key=lambda x: x["created_at"], reverse=True)
    sample = even_sample(uniq, args.max_prs)
    print(f"unique {len(uniq)}; sampling {len(sample)}", flush=True)

    prs = []
    for it in sample:
        num = it["number"]
        pr = {"number": num, "title": it["title"], "author": it["user"]["login"],
              "created_at": it["created_at"], "merged_at": (it.get("pull_request") or {}).get("merged_at"),
              "merged": bool((it.get("pull_request") or {}).get("merged_at")),
              "comments": it.get("comments", 0), "url": it["html_url"],
              "files": [], "reviews": []}
        files = api(f"https://api.github.com/repos/{args.repo}/pulls/{num}/files?per_page=100")
        if isinstance(files, list):
            pr["files"] = [{"filename": f["filename"], "additions": f["additions"],
                            "deletions": f["deletions"], "patch": (f.get("patch") or "")[:20000],
                            "status": f["status"]} for f in files]
            if len(files) == 100:
                more = api(f"https://api.github.com/repos/{args.repo}/pulls/{num}/files?per_page=100&page=2")
                if isinstance(more, list):
                    pr["files"] += [{"filename": f["filename"], "additions": f["additions"],
                                     "deletions": f["deletions"], "patch": (f.get("patch") or "")[:20000],
                                     "status": f["status"]} for f in more]
        if pr["merged"]:
            rv = api(f"https://api.github.com/repos/{args.repo}/pulls/{num}/reviews?per_page=100")
            pr["reviews"] = [r.get("state") for r in rv] if isinstance(rv, list) else []
        prs.append(pr)
        time.sleep(0.08)

    # ---- analysis
    for p in prs:
        paths = [f["filename"] for f in p["files"]]
        p["cats"] = sorted(classify(paths))
        p["churn"] = sum(f["additions"] + f["deletions"] for f in p["files"])
        p["test_add"] = p["test_del"] = p["ci_add"] = p["ci_del"] = 0
        p["assert_add"] = p["assert_del"] = 0
        p["weaken"] = []
        p["weaken_detail"] = []
        for f in p["files"]:
            a, d = parse_patch(f.get("patch", ""))
            is_test, is_ci = bool(TESTF.search(f["filename"])), bool(CIF.search(f["filename"]))
            if is_test:
                p["test_add"] += len(a); p["test_del"] += len(d)
                p["assert_add"] += sum(1 for x in a if ASSERT.search(x))
                p["assert_del"] += sum(1 for x in d if ASSERT.search(x))
            if is_ci:
                p["ci_add"] += len(a); p["ci_del"] += len(d)
            if is_test or is_ci:
                for label, rx in WEAKEN:
                    hit = next((x for x in a if rx.search(x)), None)
                    if hit is not None:
                        if label not in p["weaken"]:
                            p["weaken"].append(label)
                        if len(p["weaken_detail"]) < 6:
                            p["weaken_detail"].append({"label": label, "file": f["filename"],
                                                       "line": hit.strip()[:160]})
        if p["merged_at"]:
            try:
                c = datetime.datetime.fromisoformat(p["created_at"].replace("Z", "+00:00"))
                m = datetime.datetime.fromisoformat(p["merged_at"].replace("Z", "+00:00"))
                p["hours_to_merge"] = round((m - c).total_seconds() / 3600, 2)
            except Exception:
                p["hours_to_merge"] = None

    merged = [p for p in prs if p["merged"]]
    guard = [p for p in prs if GUARD & set(p["cats"])]
    no_rev = [p for p in merged if not p["reviews"]]
    hours = [p["hours_to_merge"] for p in merged if p.get("hours_to_merge") is not None]
    stats = {
        "repo": args.repo, "window": [args.since, args.until],
        "totals_by_agent": totals, "reported_totals_by_agent": reported_totals,
        "reported_total": sum(v for v in reported_totals.values() if isinstance(v, int)),
        "unique_prs": len(uniq), "sampled": len(prs),
        "guardrail_prs": len(guard), "merged": len(merged),
        "merged_no_review": len(no_rev),
        "median_hours_to_merge": round(statistics.median(hours), 2) if hours else None,
        "test_add": sum(p["test_add"] for p in prs), "test_del": sum(p["test_del"] for p in prs),
        "weakening_prs": [p["number"] for p in prs if p["weaken"]],
        "net_test_deletions": [p["number"] for p in prs if p["test_del"] > p["test_add"]],
    }

    findings = []
    for p in merged:
        if not p["reviews"] and (GUARD & set(p["cats"])):
            findings.append(("high", p["number"], p["author"],
                             f"merged with no formal review; touches {', '.join(sorted(GUARD & set(p['cats'])))}; {p['churn']} lines"))
        if p["hours_to_merge"] is not None and p["hours_to_merge"] < 1 and p["churn"] > 200:
            findings.append(("medium", p["number"], p["author"],
                             f"merged in {p['hours_to_merge']}h with {p['churn']} changed lines"))
        if p["weaken"]:
            labels = " ".join(p["weaken"]).lower()
            ci = "ci " in labels or "ci force" in labels or "allow_failure" in labels or "continue-on-error" in labels
            skip = "skip" in labels or "expected-failure" in labels
            sev = "high" if (ci and p["merged"]) else ("medium" if skip else "low")
            detail = p.get("weaken_detail", [])
            snippet = detail[0]["line"] if detail else ""
            where = detail[0]["file"] if detail else ""
            findings.append((sev, p["number"], p["author"],
                             f"bypass-style marker: {', '.join(p['weaken'])} — {where}: `{snippet[:110]}`"))
        if p["assert_del"] > p["assert_add"] and p["assert_del"] > 0:
            findings.append(("medium", p["number"], p["author"],
                             f"removes more assertions ({p['assert_del']}) than it adds ({p['assert_add']})"))
    findings.sort(key=lambda f: {"high": 0, "medium": 1, "low": 2}[f[0]])

    with open(os.path.join(args.out, "audit.json"), "w") as fh:
        json.dump({"stats": stats, "prs": prs, "findings": findings}, fh, indent=1)
    with open(os.path.join(args.out, "findings.csv"), "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["severity", "pr", "author", "finding"])
        for f in findings: w.writerow(f)

    name = "the audited repository" if args.anonymize else args.repo
    L = []
    L.append(f"# Agent PR Audit — {name}\n")
    rep = stats.get("reported_total")
    L.append(f"**Window:** {args.since} → {args.until}  ·  **Sampled:** {len(prs)} of "
             f"{len(uniq)} found" + (f" (search reports {rep})" if rep else "") +
             "  ·  **Method:** GitHub API, read-only\n")
    L.append("## Summary\n")
    L.append(f"- {len(guard)} of {len(prs)} sampled PRs ({len(guard)/max(1,len(prs)):.0%}) touch tests, CI, lint or CODEOWNERS.")
    L.append(f"- {len(merged)} merged; **{len(no_rev)} ({len(no_rev)/max(1,len(merged)):.0%}) with no formal review object**.")
    if hours: L.append(f"- Median time from open to merge: **{statistics.median(hours):.2f} h**.")
    L.append(f"- Test lines added vs deleted across sampled PRs: **{stats['test_add']} / {stats['test_del']}**.")
    by_type = {"CI-level": 0, "test skip": 0, "suppression": 0}
    for p in prs:
        if not p["weaken"]: continue
        lab = " ".join(p["weaken"]).lower()
        if "ci" in lab: by_type["CI-level"] += 1
        elif "skip" in lab or "expected" in lab: by_type["test skip"] += 1
        else: by_type["suppression"] += 1
    L.append(f"- {len(stats['weakening_prs'])} PRs contain a bypass-style marker "
             f"({by_type['CI-level']} CI-level, {by_type['test skip']} test skip, {by_type['suppression']} suppression); "
             "each is a lead to read, not a verdict."
             + (f" PRs: {', '.join('#'+str(x) for x in stats['weakening_prs'])}." if stats['weakening_prs'] else ""))
    L.append("\n## Findings\n")
    if not findings:
        L.append("No findings at the configured thresholds. That is a result; the evidence table is in `findings.csv`.")
    for sev, num, author, text in findings[:40]:
        L.append(f"- **{sev.upper()}** #{num} ({author}) — {text}")
    if len(findings) > 40:
        L.append(f"- … {len(findings)-40} more in `findings.csv`")
    L.append("\n## Recommendations\n")
    L.append("1. Require a human review on any agent PR that touches a guardrail file (tests, CI, lint, CODEOWNERS).")
    L.append("2. Treat `continue-on-error`, `|| true`, `if: false` and new skips in CI as review-blocking changes.")
    L.append("3. Record the reviewer and the check that passed on every agent PR before merge.")
    L.append("4. Re-run this audit monthly; compare the review gap, not just the volume.")
    L.append("\n## Limits\n")
    L.append("- Sampled: " + ("evenly across the unique PR set" if len(uniq) > len(prs) else "all unique PRs found") +
             "; GitHub search caps at 1,000 results per query.")
    L.append("- 'No formal review' = no review object via the API; a human may have reviewed by other means.")
    L.append("- File paths and line counts are not intent; read the diffs before acting on a finding.")
    L.append("- Read-only: no repository was modified, no CI triggered, no code executed.")
    with open(os.path.join(args.out, "report.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print(f"wrote {args.out}/report.md ({len(findings)} findings)")


if __name__ == "__main__":
    main()
