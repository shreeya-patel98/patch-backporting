# ciq-portgpt: plan for an accurate-or-abstain CVE backport agent

Sources: 6 past Claude Code sessions (~1 MB of backport conversation, 4 independent
readers), 388 merged `upstream-diff` notes, and the 10-case blind eval (2026-11-03).

## 0. End-to-end flow: "a CVE + exploit just came in"

```
TRIGGER  CVE id + exploit link (Slack #linux-security, oss-security, kernelCTF, ZDI, SECO)
  |
  1 UNDERSTAND (automatic)
  |   fix SHA(s) via vulns.git + kernel.org record + RH API; Fixes: parent; follow-ups;
  |   sibling CVEs; read the exploit: privileges needed (unpriv / userns), configs,
  |   arch, target kernel, primitive (UAF, OOB...)
  2 EXPOSURE MATRIX (automatic)
  |   every branch: fixed? bug present (code)? shipped (config)? reachable (sysctl/userns)?
  |   duplicate work (open PRs, colleague branches, RHEL rebuilds)?  -> priority
  |   >> CHECKPOINT 1: engineer confirms matrix; agent posts short summary to thread/Jira
  3 REPRODUCE  [LATER - not in v1; exploits vary and may not exist]
  |   placeholder: isolated VM, run exploit/repro -> RED evidence; "not reproduced"
  |   is never "not vulnerable"; porting exploits = human only
  4 BACKPORT per affected branch (strategy ladder, tiers T0-T6)
  |   T1-T4 -> agent builds commits;  T5/T6 -> abstain, dossier to engineer
  5 GATES G1-G10 (provenance, symbols, kABI, anchors, configs, msg audit, reviewer...)
  |   any failure -> abstain with reason
  6 VERIFY (v1 = kernel CI)
  |   push the fix branch -> existing kernel CI: build, boot, kselftest, LTP, compare
  |   with base; agent reads results critically (booted kernel == built, regressions
  |   hidden under a green heading, missing baseline)
  |   [LATER] exploit/repro on patched kernel -> GREEN; KASAN when control flow changed
  7 PACKAGE
  |   branches/commits in CIQ format, evidence (matrix, red/green logs, gates, upstream-diff),
  |   draft PR text + short Slack summary
  |   >> CHECKPOINT 2: engineer reviews, approves push
  8 AFTER MERGE (human-led, agent assists)
  |   close not-affected tickets with code reasons; content release; ticket status
  9 LEARN
      record outcome (accepted / edited / rejected), add case to eval set,
      add new lesson to skill / per-branch cookbook
```

Embargoed CVEs: same flow, local builds/VMs only, no shared CI, no external API unless approved.
Timing (measured vs estimated; set real targets after the phase-1 replay):
- measured: PortGPT backport 73-287 s/case; touched-object compile ~10 s; CI build ~11-16 min/arch
- estimated: steps 1-2 a few minutes per CVE (git/config checks are seconds per branch);
  a 22-CVE batch ~15-45 min; full CI with kselftest/LTP likely 1-2 h (not yet measured)
- slowest parts: kernel CI and human checkpoints
v1 scope: steps 1, 2, 4, 5, 6 (via kernel CI), 7, 9. Step 3 and exploit-based proof come later.

What makes it "CIQ-smart" (instead of training a model):
- Claude for reasoning; CIQ process as a skill/system prompt (`ciq-cve-backport`)
- deterministic tools for every checkable fact (git, configs, CI images, VMs, kABI)
- a growing eval set of real CIQ cases (replay shipped batches) as the quality bar
- a learning loop: every reviewer correction becomes a rule, a check, or an eval case

## 1. Principles
1. **Accurate or abstain.** Emit a patch only when every gate passes; otherwise hand the
   engineer an analysis. Success metric = precision of what we emit, then coverage.
2. **Code over version numbers.** RHEL backports newer code onto old bases (workqueue v6.9
   in 9.x, sch_ets in 8.x, DRM GEM ioctl). Every verdict cites literal code.
3. **Upstream first, no invented code.** Order: upstream cherry-pick (+ prereqs) > precedent
   (CentOS/RHEL, stable) > fragments of named upstream commits > abstain. Hand adaptation
   is a human decision.
4. **Verify, never assume.** Fetch before every verdict and every push. Never claim exploit
   absence, agreement with a source, or "merged/not merged" without checking.
5. **Separate verified from inferred**, and attribute third-party claims.
6. **Nothing visible to others without approval.** Show, then push (only when told).

## 2. Three modes (one engine)
| Mode | What | Why |
|---|---|---|
| **Triage** | Exposure matrix for a CVE batch across all branches, with code evidence | Most of the effort in past batches (NebuSec 22 CVEs, kernelCTF/ZDI); most past mistakes were triage mistakes |
| **Backport** | Produce branches/commits for affected branches | The original goal |
| **Review** [LATER] | Review colleagues' backport PRs (Brett, Jonathan, automation bot) | Deferred: not in v1; reuses the same gates once they exist |
Build order (v1): Triage -> Backport. Review mode comes later, reusing the same checks.

## 3. Stage 0: intake and environment
- Inputs: VULN tickets (Collider service account, one per CVE per product: lts-8.6, lts-9.2,
  lts-9.6, fipslegacy-8.6, cbr-7.9, bpftool...), disclosures (oss-security, kernelCTF, ZDI,
  NebuSec), SECO customer escalations, Slack threads with SHAs.
- Read the *original* source (e.g. the openwall post) and diff it against the ticket list:
  past batches had CVEs missing from the relayed list (43074, 64560).
- CVE -> upstream fix(es) via vulns.git (`~/ciq/vulns`), cross-check kernel.org CVE record
  and RH security API.
- `git fetch --all` (kernel-src-tree, kernel-src-tree-tools). Tips move during a session
  (rlc 211.47 -> 211.49 made a PR redundant).
- Use the working tools copy (`~/ciq/claude/kernel-src-tree-tools/ciq-cherry-pick.py`;
  the older copy failed with ImportError).

## 4. Stage 1: Triage (exposure matrix)
Branch set: ciqcbr7_9, ciqlts8_6, ciqlts9_2, ciqlts9_4, ciqlts9_6, latest rlc-8/9/10 tips,
fips-legacy-8, CLK ciq-6.12.y/6.18.y (+ -next). Only latest RLC streams ("don't think about
older active streams"); only x86_64 + aarch64 unless told otherwise.

Per (CVE, branch) cell, in this order:
1. **Fix already present?** ancestry (`check-upstream-commit.sh` CONTAINS) AND content
   (RHEL rebuild PRs carry fixes without CVE tags; "splat" commits on rebuilt trees hide
   them). Note: run it on the *fix*, not the bug commit.
2. **Bug present?** `Fixes:` parent / vulnerable construct present - read the function.
   Never treat "fix marker absent" as "vulnerable" (false VULNs: 64560 & 23274 on EL8,
   52929, 43502 on CBR).
3. **Shipped?** CONFIG in the branch config chosen with kernel_build.sh precedence
   (`ciq/configs/kernel-$ARCH.config` > `configs/kernel-$ARCH.config` >
   `configs/kernel-$ARCH-rhel.config` > `configs/kernel-$VERSION-$ARCH.config`), all arches.
4. **Reachable?** runtime gates (`io_uring_disabled=2`), USER_NS (undermines =m gating).
5. **Exploit?** check actual sources (NebuSec, kernelCTF, ZDI); record target build.
Output cell = FIXED / VULN / NOT-AFFECTED / N/A / UNCLEAR, each with code evidence.
Then: sibling CVEs sharing the same Fixes: parent; prioritise by reachability + exposure
(core ungated code first), RH CVSS over kernel.org's, then exploit availability.
Closing a ticket "not affected" requires a code reason (e.g. "8.x has no request-migration
code", "CBR ESP always calls skb_cow_data()").

## 5. Stage 2: Fix set and duplicate check
- Fix + **Fixes-forward follow-ups** (`git log mainline --grep "Fixes: <sha12>"`) - e.g. ovs
  31678 needs 46165 or it ships a self-deadlock.
- **Companion fixes**: a fix that only works with another (#1763 f84eca581739 needs
  f4c50a4034e6) - ship together or report "correct but ineffective".
- Prereqs that carry their own CVE get `cve`, not `cve-pre` (60e6489f8e3b, e5b31d988a41).
- Prereq vs series sibling: prereq = needed for correctness on this branch; sibling =
  optional (5b5d8f351dd9) -> separate ticket.
- **Duplicate check** right before building and again before pushing: open/merged PRs in
  ctrliq/kernel-src-tree, colleagues' `{user_topic}_*` branches, automation-bot PRs, RHEL
  rebuild PRs (by file diff).

## 6. Stage 3: Strategy ladder (per affected branch; tier = first rung that works)
| Tier | Rung | Allowed for the tool? |
|---|---|---|
| T0 | Not affected / already fixed (with evidence) | Yes (human confirms) |
| T1 | Clean `ciq-cherry-pick` (+ symbol-resolution check) | Yes (existing bot today) |
| T2 | Mechanical: context-only, documented rename (name the rename commit), verified dropped hunk | Yes |
| T3 | Missing prereqs: minimal chain, per-branch (9.2 didn't need 97c8a3c1f73d, 9.6 did), each clean, kABI-safe; stable ordering as evidence | Yes, human checks scope |
| T3b | Precedent: CentOS Stream/RHEL (public GitLab link) or nearest stable backport. **Verify the precedent itself** (stable shipped eaf1f4a5c38a without prereq b2e584aa3c71) and that upstream really can't be picked (Safe-RET: stable's hand port was unnecessary) | Yes |
| T4 | Fragments of named upstream commits (f8471173506a) | Yes, provenance must pass |
| T5 | Hand adaptation: predecessor code paths, kABI-preserving redesign (mpls file-static seqcount), extra init for old alloc paths, lock-semantics changes | **No - abstain with proposal** |
| T6 | Embargo, large feature series, x86 entry/KVM/mm, refactor-sized prereq chains with kABI impact (74581 fib6_result, 43042 mpls) | **No - human-led; defer with recorded reason** |
Any red flag promotes a case to a higher tier.

## 7. Stage 4: Gates (all must pass, else abstain)
Deterministic first:
- G1 per-touched-object build in the branch CI image (`.container_build_image`) [done]
- G2 function-anchor check [done]
- G3 **provenance**: every added line from upstream fix/prereq/precedent/tree, or a named rename
- G4 **symbol resolution**: identifiers used by the patch exist in this tree's relevant headers
  (23274 referenced XT_IDLETIMER_ALARM; a tree-wide grep gave a false pass)
- G5 per-commit self-containment + bisectability (each commit builds)
- G6 kABI: no new exports (incl. dead EXPORT_SYMBOL from prereqs), no struct layout changes in
  embedded/exported structs; RH_KABI_EXTEND only at tail; full modules build + symvers for a
  formal answer (`check_kabi.sh`)
- G7 new Kconfig symbols -> config entries (process_configs/listnewconfig would fail), FIPS
  `fips_allowed` for new crypto
- G8 diff-of-diffs vs upstream and vs precedent; every difference explained in upstream-diff
- G9 commit-message audit: tag order (jira -> cve/cve-pre/cve-bf -> commit-author -> commit ->
  upstream-diff), jira line: LTS = the product's VULN ticket; RLC = no VULN line, only a
  ticket specifically tied to the work (e.g. SECO); VULN lines copied from LTS ports are
  dropped (confirmed by Jonathan Maple, 2026-11-18), tabs incl. committer SoB after
  `(cherry picked from` (known ciq-cherry-pick bug), commit/cherry-pick SHA match, no
  placeholders, VULN ticket matches the branch's product, real engineer as author
LLM-assisted:
- G10 reviewer agent: semantic dependencies of adaptations (nft_lookup relied on 17a20e09f086),
  lock audit of every caller, init on all alloc paths, bounds claims, scope, RHEL changes
  silently reverted in conflicts
Runtime:
- G11 kernel CI: build, boot (verify booted `uname -r` == built), kselftest, LTP; read past
  the ✅ (PASS->FAIL can hide under it); missing baseline -> request base run
- G12 functional/KASAN test when control flow changes (74480 bridge `break;`); x86
  entry/critical path: no approval without clean full CI; CBR = build+boot

## 8. Stage 5: Output
- Branch `{shreeya_<batch>}_<base>` (one branch per base, all CVEs of the batch folded in);
  base = current tip **that has the CI trigger** (newest RLC tips lacked it).
- Commits via ciq-cherry-pick (`--upstream-ref origin/kernel-mainline --vulns-dir ~/ciq/vulns`);
  `--ignore-fixes-check` only with a recorded reason (RHEL-squashed history).
- upstream-diff: 2-3 lines, tree-specific, provenance first, names the missing commit, no
  wrong/stale reasons.
- Report: exposure matrix, tier, evidence, verified vs inferred, defer list with reasons,
  short thread-ready summary and plain-voice review comment drafts.
- Push only on explicit approval; `--force-with-lease` (pinned SHA) only on PRs without
  reviews; when fixing a colleague's PR, update their branch, verify with `git range-diff`.

## 9. Review mode [LATER - not in v1] (same checks, applied to someone else's PR)
Base is current tip; series complete incl. Fixes-forward; per-branch prereqs; each adaptation
vs upstream semantics; upstream-diff accurate; CI incl. hidden regressions; ticket numbers
match product; kABI; bisectability. Output a short reviewer comment; never post without
approval.

## 10. Evaluation and rollout
- **Replay shipped work** (gold labels exist): NebuSec/ZcopyReaper batch (ENGD-847), MDASH
  batch (#1809-1812), mpls 43042, 68376 on CBR, SECO-762 workqueue; plus the 10-case set.
- Triage eval: compare the agent's matrix with the final shipped/closed tickets, incl. the
  known traps (64560/23274 EL8, XT_IDLETIMER_ALARM, io_uring sysctl, 46165 follow-up,
  #1763 companion fix, 9.2 prereq difference).
- Backport eval: held-out ~30 merged cases stratified by tier + ~10 negative (correct = abstain).
- Rollout bar: 100% precision on two consecutive held-out runs; then shadow mode on live
  tickets (no pushes), compare with engineers for several weeks; per-tier rollout
  (T0-T2 first, T3/T3b/T4 after, T5/T6 stay human).

## 11. Phases
| Phase | Deliverable | LLM cost |
|---|---|---|
| 1 | Triage mode (deterministic: fix-present, bug-present, config, reachability) + replay eval on NebuSec batch | ~0 |
| 2 | Gates G3-G9 + Fixes-forward/companion/duplicate checks | small |
| 3 | Backport ladder T1-T4 with PortGPT constrained to provenance; reviewer agent G10; held-out eval | ~$10-20/run |
| 4 | Intake from Jira/vulns, shadow mode, CI/PR integration | small |
| Later | Review mode for colleagues' PRs; exploit reproduction + red/green; KASAN stage | - |

## 12. Communication rules (from feedback)
Short plain summaries first ("3-4 line summary"); details on request; plain reviewer voice
("without sounding like AI"); evidence she can verify (links, commands); never truncate
verification output; say "unsure" when unsure.

## 13. Open questions
Embargo policy for API use; team API key; where diffofdiffs / Fixes-checker bots live and whether
they can run locally; who reviews tool output during shadow mode.
