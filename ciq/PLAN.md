# ciq-portgpt: plan

Goal: when a CVE (and maybe an exploit) comes in, find which CIQ branches are affected and
produce correct backports in CIQ format — **or say it can't, and why**.

Full details, evidence and lessons: [PLAN-details.md](PLAN-details.md)

## Principles
- **Accurate or abstain.** No patch is better than a wrong patch.
- **Upstream code only.** Cherry-pick, prerequisites, or existing backports (CentOS / stable).
  No invented code; hand adaptation stays with engineers.
- **Check the code, not the version.** RHEL backports newer code onto old kernels.
- **Engineers approve.** Nothing is pushed without approval.

## Flow (v1)
| # | Step | Who |
|---|---|---|
| 1 | **Understand:** fix commit, follow-up fixes, what the exploit needs | Scripts + Claude |
| 2 | **Exposure table:** for each branch — already fixed? bug present? built in config? reachable? duplicate work? | Scripts + Claude; **engineer confirms** |
| 3 | **Backport** affected branches, easiest route first | PortGPT agent (Claude) |
| 4 | **Checks:** builds, no invented code, symbols exist, kABI, commit format, Claude reviews the patch | Scripts + Claude; any failure = abstain |
| 5 | **Verify:** push branch → kernel CI (build, boot, kselftest, LTP) | Kernel CI; **engineer approves push** |
| 6 | **Package + learn:** PR text, summary, record outcome, add lesson | Claude |

## Difficulty tiers
| Tier | Case | Tool does it? |
|---|---|---|
| T0 | Not affected / already fixed (with evidence) | Yes |
| T1 | Clean cherry-pick | Yes (existing bot today) |
| T2 | Mechanical conflict: context, renamed API, code absent here | Yes |
| T3 | Missing prerequisites, or an existing CentOS / stable backport | Yes |
| T4 | Parts of other named upstream commits | Yes |
| T5 | Hand adaptation (old code paths, kABI redesign, extra init) | No — hand over analysis |
| T6 | Embargo, big feature series, critical code (x86 entry, KVM, mm) | No — engineer-led |

## Status
- Done: PortGPT on Claude, builds in branch CI images, CIQ commit format, cost caps,
  wrong-function check, eval harness. Blind test: 6/10 correct (all 4 misses compiled fine).

## Phases
1. **Exposure table** — mostly scripts; test by replaying the NebuSec batch (known answers)
2. **Checks** — no-invented-code, symbols, kABI, follow-up fixes, duplicate work, commit format
3. **Backport ladder** — PortGPT limited to upstream-sourced code; held-out eval set
4. **Integration** — Jira intake, shadow mode on live tickets, PR creation

Rollout: only after 100% correct output on two held-out runs, then shadow mode, tier by tier.

## Later
Reviewing colleagues' PRs · reproducing exploits before/after patch · KASAN stage

## Open questions
Embargo policy for API use · team API key · where the diffofdiffs bot can run
