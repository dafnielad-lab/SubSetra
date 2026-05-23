# Design & Algorithm

This document explains *how* Subsetra finds subsets that sum to a target, and
*why* each design choice is there. It is meant to be read for
learning/practice. The whole engine lives in `subset_sum_reconcile.py`.

---

## 1. The problem

Given a multiset of transaction amounts `T = {t₁, …, tₙ}` and a target `S`,
find subsets `X ⊆ T` with `sum(X) = S`. This is **Subset Sum**, which is
NP-hard: in the worst case there is no known way to avoid exponential work.
Subsetra does **not** beat that worst case (nothing does) — it makes *typical
bookkeeping instances* fast, and it stays safe on the bad ones via a time limit.

Two requirements shape everything:

1. **Exactness.** Money must never be mis-summed by floating-point error, so all
   arithmetic is in integer **cents**: `to_cents(x) = round(x * 100)`. `100.10`
   becomes `10010`. There are no floats anywhere in the search.
2. **It must always make progress and never get stuck.** The search is
   length-incremental and cooperative: at any moment it can stop and return the
   (shortest) solutions found so far.

---

## 2. Preprocessing

`preprocess()` does three things:

- **Filter.** Any single transaction larger than the (positive) target can't be
  in a solution, so it's dropped. Zero values are removed.
- **Split by cents.** Each amount is classified as **round** (a whole currency
  unit — `c % 100 == 0`, e.g. `400.00`) or **non-round** (it has cents, e.g.
  `250.20`). This split is the key idea — see §4.
- **Sort descending.** Both groups are sorted high-to-low, which makes the
  bounds in §5 tight and lets the DFS prune early.

---

## 3. Two solver paths

```
solve(transactions, target, k_max, max_seconds, …)
   ├─ target ≤ 0  OR any negative amount  ──▶  _solve_general()     (§6)
   └─ otherwise (all positive)            ──▶  cents decomposition  (§4,§5)
```

The positive all-positive case is the common one and gets the fast
cents-decomposition path. Anything with negatives (credits/refunds) or a
non-positive target (e.g. finding entries that cancel to **zero**) goes to a
unified, still-pruned, general DFS.

---

## 4. The cents / whole-unit decomposition (the core trick)

Look at the target's **cents digit**, `target mod 100`. Round transactions
contribute `0 mod 100`. So **only the non-round transactions can supply the
cents**. That decomposes a solution of total size `K` into:

- `c` **non-round** items whose sum is `≡ target (mod 100)`, plus
- `needed = K − c` **round** items that complete the remaining whole-unit amount.

The search iterates over total length `K = 1, 2, 3, …` (iterative deepening, so
**shortest solutions are found first**), and for each `K` over the split
`c + needed`:

1. **Cents stage — `find_modular_candidates()`** enumerates subsets of the
   non-round items of size `c` whose sum is congruent to `target mod 100`.
   It's a **generator** (`yield` / `yield from`), so candidates are produced
   lazily in `O(K)` memory — no giant list, no out-of-memory, and it stops
   early once enough full solutions are found.
2. **Completion stage — `find_subset_sum()`** takes each candidate's leftover
   `rem = target − sum(candidate)` (guaranteed to be a whole multiple of 100)
   and finds exactly `needed` round items summing to it.

A candidate that already equals the target (when `needed == 0`) is a complete
solution on its own.

---

## 5. Length-aware two-sided bounds (the pruning that makes it fast)

Both stages prune with **prefix sums** of the sorted groups. If you still need
exactly `m` more items from a descending-sorted pool, then whatever you pick has

```
sum_of_m_smallest  ≤  (sum of those m items)  ≤  sum_of_m_largest
```

Both bounds are read off prefix sums in `O(1)`. Any partial path whose required
remainder falls outside `[m-smallest, m-largest]` is pruned immediately.

A subtle but important refinement is the **tight per-`needed` window** passed
into the cents stage. Because the leftover will be completed by *exactly*
`needed` round items, the candidate's own sum must lie in

```
[ target − sum(needed largest round) ,  target − sum(needed smallest round) ]
```

Restricting candidate sums to this window (instead of a loose
`target − sum(all round)`) was measured to cut ~4× the branches and hold ~80×
fewer candidates in memory on a mixed test. Each `(K, c)` pair maps to a unique
`needed`, so the window is computed once per pair from `prefix_round` and handed
in — no candidate cache is required.

---

## 6. The general path (negatives, zero target)

When values can be negative or the target is `≤ 0`, the clean "round items only
add whole units" reasoning breaks (and `target mod 100` games don't apply the
same way), so `_solve_general()` runs a single **iterative-deepening DFS** over
length `K`, with the same **two-sided `m`-smallest…`m`-largest** bound as §5
(computed from prefix sums of the values sorted descending). This is what lets
Subsetra find, for example, a `+500` and a `−500` that cancel to a target of
`0`.

---

## 7. Guarantees and limits

- **Shortest-first.** Because length is the outer loop, solutions come out
  ordered by number of items, smallest first, up to `max_results` (default 10).
- **Always returns what it found.** A timeout, the result cap, `Ctrl+C`, or the
  GUI Stop button all unwind cleanly and return the solutions gathered so far.
  The time limit is **mandatory, not optional** — the NP-hard worst case (e.g.
  60 items all ≈ `1000.50`) is inherent and cannot be pruned away.
- **Duplicates.** Two rows with the same amount are treated as **distinct
  entities** (both can participate), but an identical *value-list* is reported
  only once, so you don't see the "same" answer twice.
- **No floats, ever.** Every comparison and sum is on integer cents; the final
  display divides by 100 only for presentation.

The engine deliberately avoids heavier machinery (meet-in-the-middle, etc.) —
the decomposition + bounds are enough for the target workload, and simplicity
keeps it correct and auditable.

---

## 8. Testing

Correctness is checked against an independent oracle, never against the engine
itself.

- **Brute-force oracle (exact).** For small cases (`n ≤ 14`, small `k`), every
  answer is compared to a full `2^n` enumeration: the engine must find *exactly*
  the same set of value-lists.
- **Partial oracle (large cases).** For larger `n`, a known solution is
  *planted* and the test asserts (a) every returned subset really sums to the
  target, and (b) if the search reported completion, it must have found the
  planted solution.
- **Deterministic suite.** `python test_reconcile.py` runs ~thousands of fixed,
  seeded cases plus targeted scenarios (cents precision, duplicates,
  shortest-first ordering, the result cap, super-increasing/unique-solution
  pruning, cancellations, no-solution-under-time-limit, Excel round-trip, input
  validation). It must print **`0 failed`**.
- **Fuzzing.** `python test_reconcile.py --fuzz [N]` generates *fresh* random
  cases each run (it prints the seed; `--seed S` reproduces), drawing from many
  diverse generators plus occasional edge/large cases. It accumulates total
  unique cases ever tested in `test_log.json` (the "since last engine change"
  counter resets automatically when the engine file's hash changes).
- **Growing failure corpus.** Any failing case is appended (deduped) to
  `fuzz_failures.jsonl` and **replayed as a permanent regression on every run**,
  in both modes — so a bug found once can never silently return.

Run the suite after any change to the engine.
