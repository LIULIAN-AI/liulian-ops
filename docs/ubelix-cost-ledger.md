# UBELIX Paygo Cost Ledger — methodology & rules

_Created 2026-05-15. Tracks real money spent on UBELIX Paygo for the
entity-identifier effort. Tool: `jobs/ubelix_cost_tracker.py`; live record:
`jobs/ubelix_cost_ledger.json`._

## Hard rules

| Rule | Value |
|---|---|
| **Hard cap** (total Paygo spend, this user/effort) | **10.00 CHF** |
| **Alarm threshold** (90 %) | 9.00 CHF — raise alarm, ask user to lift the cap |
| **Stop threshold** (100 %) | 10.00 CHF — cancel/stop all running Paygo jobs |
| Free tiers (gratis-full, preemptable) | not counted — 0 CHF |

The "committed total" is conservative: a job counts its **actual** cost once
known, otherwise its **projected** cost (the `sbatch` estimate). Cancelled jobs
that never ran count 0.

## Workflow

1. **Before** submitting a Paygo job — estimate the cost and check it fits:
   ```bash
   python3 jobs/ubelix_cost_tracker.py can-afford --estimate <CHF>
   ```
   Exit 0 = allow, 1 = would cross 90 % (ask user), 2 = would breach cap (deny).
2. **At** submission — read the `Projected costs this job (CHF)` line from the
   `sbatch` output and log it:
   ```bash
   python3 jobs/ubelix_cost_tracker.py add --jobid <id> --run-tag <tag> \
       --projected <CHF> --notes "<what>"
   ```
3. **After** the job finishes — record the real cost (two methods, below):
   ```bash
   # Method 1 — elapsed-time (default, verified):
   python3 jobs/ubelix_cost_tracker.py set-actual --jobid <id> --actual <CHF> --method elapsed
   # Method 2 — scan the job output file (unverified):
   python3 jobs/ubelix_cost_tracker.py set-actual --jobid <id> --from-log outputs/<file>.o<id>
   ```

### Two ways to obtain a job's actual cost

**Method 1 — from elapsed time (verified, default).** UBELIX bills per-minute
on confirmed rates (RTX 4090 = 0.10 CHF/GPU-h — see `ubelix-cluster-tiers.md`):

```
actual_CHF = 0.10 × n_gpu × (ElapsedRaw_seconds / 3600)
```

`ElapsedRaw` comes from `sacct -j <id> --format=ElapsedRaw -n -P`. The
completion watcher applies this automatically for every Paygo job. This is the
trustworthy path today.

**Method 2 — from job output files (implemented, VERIFIED INAPPLICABLE on
UBELIX).** Some clusters append a final/billed-cost line to the job
`outputs/*.o<JOBID>` file via an epilog. The tracker can scan for it:

```bash
python3 jobs/ubelix_cost_tracker.py scan-cost --logfile outputs/<file>.o<id>
```

`scan-cost` greps the file for patterns like `actual cost … <n>`,
`billed … <n> CHF`, etc.

**Verification result (2026-05-20, against finished Paygo job 4310212):**
`grep -iE 'cost|chf|billed|wckey' outputs/eid.paygo.traffic.lstm.o4310212`
returns **zero matches**. UBELIX's cost plugin emits its cost block only as a
**submit-time prolog** to the `sbatch` stdout — it does **not** write any
cost-related line into the job's runtime `.o` file on completion. Therefore
**Method 2 is unavailable on UBELIX**; Method 1 (elapsed-time × rate) is the
canonical path. The Method-2 code is kept for portability to other clusters
and as a guard against future UBELIX behaviour changes.

**Resume / preemption — summing multiple runs.** One experiment (`run_tag`) can
consume several SLURM job IDs: a 12 h walltime times out and is resubmitted, or
a preemptable job is killed and requeued. Each (re)submission is `add`-ed as its
own job-ID entry, and `report` prints a **"Per experiment (run_tag)"** block
that **sums all job IDs sharing a run_tag** — that sum is the experiment's true
cost. For a single job ID that was itself preempted-and-requeued, sum
`ElapsedRaw` across all of its `sacct` rows before applying Method 1.
(Note: Paygo `job_gpu` jobs run uninterrupted — preemption only affects the
free preemptable tier, so paid jobs are not preemption-fragmented; only the
walltime-timeout resubmission case applies to Paygo cost summation.)
4. **Anytime** — full picture:
   ```bash
   python3 jobs/ubelix_cost_tracker.py report
   ```
   Prints per-job rows, per-day / per-month / per-session spend, the
   calibration factor, and the cap status (✅ OK / ⚠️ ALARM / 🛑 STOP).

## Calibration — how predictions get adjusted

The `sbatch` projection assumes the job runs the **full requested walltime**.
In practice HPO jobs finish early (ASHA pruning, early stopping), so the
**actual cost is usually lower** than projected.

After each finished job we record `ratio = actual / projected`. The
**calibration factor** is the mean of those ratios across all finished jobs:

```
calibration_factor = mean(actual_i / projected_i)   for finished jobs i
calibrated_estimate(new_job) = sbatch_projected × calibration_factor
```

- Before any job finishes: factor = 1.0 (use raw projection — conservative).
- After N jobs: the factor reflects the observed projected→actual bias, so
  `can-afford` plans with calibrated (more realistic) numbers while the cap
  itself is still enforced on the conservative committed total.

Example: if the first job was projected 2.0 CHF but actually cost 1.4 CHF,
factor = 0.70; a next job projected at 3.0 CHF is planned as 3.0 × 0.70 =
2.1 CHF expected.

## Current state

Run `python3 jobs/ubelix_cost_tracker.py report` for the live ledger.
As of creation: **0 Paygo jobs logged, 0.00 / 10.00 CHF spent.**

> Free-tier runs (swiss-river full matrix, traffic×DLinear, etc.) are **not**
> in this ledger — they cost nothing. Only `--account=paygo` jobs are tracked.
