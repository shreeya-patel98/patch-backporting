#!/bin/bash
# Blind evaluation of ciq-portgpt against already-merged CIQ backports.
# For each "<branch> <merged-ciq-commit>": start from the merged commit's parent,
# run ciq-portgpt with the upstream sha, compare result files with the merged commit.
#
#   eval_batch.sh cases.txt [max-cost-per-case]
set -u
CASES=$1; MAXCOST=${2:-3}
REPO=~/ciq/kernel-src-tree; WORK=~/ciq/portgpt-work; WT=$WORK/kst
OUT=$WORK/eval-$(date +%m%d-%H%M); mkdir -p "$OUT"
. ~/ciq/portgpt/.env.sh
PY=~/ciq/portgpt/.venv/bin/python; TOOL=~/ciq/portgpt/ciq/ciq_portgpt.py
printf "%-10s %-12s %-12s %-9s %-6s %-6s %-6s %s\n" branch merged upstream result diff cost secs note > "$OUT/summary.txt"

while read -r BR MERGED; do
  [ -z "$BR" ] || [[ $BR == \#* ]] && continue
  MSG=$(git -C $REPO log -1 --format=%B $MERGED)
  UP=$(sed -n 's/^commit \([0-9a-f]\{40\}\).*/\1/p' <<<"$MSG" | head -1)
  CVE=$(grep -oE 'CVE-[0-9]{4}-[0-9]+' <<<"$MSG" | head -1)
  JIRA=$(sed -n 's/^jira \(VULN-[0-9]*\).*/\1/p' <<<"$MSG" | head -1)
  TARGET=$(git -C $REPO rev-parse $MERGED^)
  # series check: does the previous commit carry the same jira?
  NOTE=""; git -C $REPO log -1 --format=%B $TARGET | grep -q "^jira ${JIRA:-none}" && NOTE="series(prereqs present)"
  LOG=$OUT/${BR}-${MERGED:0:12}.log
  echo "=== $BR $MERGED $CVE ($UP) $NOTE" | tee -a "$OUT/progress.txt"

  START=$(date +%s)
  $PY $TOOL $UP $BR --cve "${CVE:-CVE-UNKNOWN}" --jira "${JIRA:-VULN-0}" --target $TARGET \
      --max-cost $MAXCOST --fresh --no-commit > "$LOG" 2>&1
  SECS=$(( $(date +%s) - START ))
  COST=$(grep -oE 'total cost: \$[0-9.]+' "$LOG" | tail -1 | grep -oE '[0-9.]+$')
  PATCH=$WORK/runs/${BR}-${CVE:-CVE-UNKNOWN}/backport.patch

  RESULT=failed; DIFF=-
  if [ -s "$PATCH" ]; then
    git -C $WT reset -q --hard; git -C $WT clean -q -fdx; git -C $WT checkout -q --detach $TARGET
    if git -C $WT apply "$PATCH" 2>/dev/null; then
      FILES=$( (git -C $REPO show --name-only --format= $MERGED; git -C $WT diff --name-only) | sort -u)
      git -C $WT diff $MERGED -- $FILES > "$OUT/${BR}-${MERGED:0:12}.vs-merged.diff"
      DIFF=$(grep -cE '^[+-][^+-]' "$OUT/${BR}-${MERGED:0:12}.vs-merged.diff")
      [ "$DIFF" = 0 ] && RESULT=exact || RESULT=differs
      cp "$PATCH" "$OUT/${BR}-${MERGED:0:12}.backport.patch"
    else
      RESULT=noapply
    fi
    git -C $WT reset -q --hard; git -C $WT clean -q -fdx
  fi
  grep -q "Cost cap reached" "$LOG" && NOTE="$NOTE cost-cap"
  printf "%-10s %-12s %-12s %-9s %-6s %-6s %-6s %s\n" $BR ${MERGED:0:12} ${UP:0:12} $RESULT $DIFF ${COST:-?} $SECS "$NOTE" | tee -a "$OUT/summary.txt"
  rm -rf $WORK/build-${BR}-${TARGET:0:12}   # keep disk usage bounded
done < "$CASES"

echo; cat "$OUT/summary.txt"
awk 'NR>1 && $6!="?" {s+=$6} END {printf "TOTAL COST: $%.2f\n", s}' "$OUT/summary.txt" | tee -a "$OUT/summary.txt"
echo "results in $OUT"
