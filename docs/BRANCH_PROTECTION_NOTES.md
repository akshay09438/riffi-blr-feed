# Branch protection - current setup and how to harden later

*Scaffolded by Zuko `/bootstrap`. Plain-language notes for the human, not the agent.*

## What is enabled right now

**Branch protection is intentionally OFF for this repo.** The team is small and chose a frictionless merge path: branch, open a PR, let CI run, merge after it is green. That discipline lives in the constitution and in how `/build` works - it is followed by practice and instruction, not enforced by GitHub refusing a push.

What still protects you, mechanically, with no branch protection:

- **The Zuko hooks** block dangerous edits, force-pushes to `main`, catastrophic deletes, TLS-disabling, and committed secrets at write time, inside the agent.
- **CI** runs on every PR and on direct pushes to `main`: install, typecheck, lint, tests, secret scan (gitleaks), static analysis (semgrep), and coverage-no-regression on touched files. A red run shows a visible red X.
- **CODEOWNERS** auto-requests the owner's review on dangerous and test files and labels the PR.

## The honest residual risk

Without branch protection:

- A teammate or a confused agent **can** push straight to `main` - nothing refuses it.
- A red CI run **warns** but does not **block** a merge.
- CODEOWNERS **requests** review but does not **require** it before merge.

For a two-person team this is an accepted trade. Revisit it when the team grows past three, or after any near-miss.

## How to harden later (when you want gates, not warnings)

On GitHub: **Settings -> Branches -> Add branch ruleset** (or "Add classic branch protection rule") for `main`, then enable:

1. **Require a pull request before merging** - no direct pushes.
2. **Require status checks to pass before merging** - select the CI checks: `verify`, `secret-scan`, `sast`, `coverage-no-regression`. Turn on "Require branches to be up to date before merging."
3. **Require review from Code Owners** - makes CODEOWNERS a hard gate on the dangerous and test files.

### Plan note

On a **Free** private repo, "Require review from Code Owners" and required reviews are limited. Branch rulesets for required status checks may work on Free; required CODEOWNERS review on a private repo generally needs **GitHub Team** or above. If you want the CODEOWNERS gate enforced, either upgrade the plan or make the repo public (where these features are free).
