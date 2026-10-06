# 0016 — Paper-trading ledger (no real money)

Status: accepted (2026-09-28)

## Context
Goal: make the workbench usable for tracking trading decisions with money at stake. Options
considered: a paper-trading ledger, importing real market quotes, or live broker trading.
Real-money trading (broker integration, order placement) was excluded by the build plan, the
engines are validated only on synthetic data, and real US equity options are American with
discrete dividends and illiquid quotes. A paper-trading ledger is the first step.

## Decision
- **No real money moves.** The app records trades the user makes or would make; it never
  connects to a broker or places orders. Every paper response carries that notice; nothing is
  investment advice.
- **Append-only, hash-chained ledger** per account (`data/paper/<acct-id>.jsonl`): events
  `open_account`, `trade`, `void`, `settlement`, `mark`, each with effective timestamp,
  recorded-at time, payload, previous hash and own hash. Appends hold an exclusive file lock;
  trade ids are assigned from the sequence number under the lock (a race in the first draft,
  found in review, is covered by a concurrent-writer test). `verify` reports the first broken
  event if a line is edited.
- **Corrections are visible:** a `void` event (with a required reason) excludes a trade from
  positions and cash; the original entry stays in the log.
- **Exact money:** cash, prices, fees and realized P&L are Decimal. Average-cost accounting per
  contract; a fill that crosses zero closes at average cost and opens the remainder at the fill
  price. Fees are separate; net realized = realized - fees.
- **Marks:** each open position is valued from an observed market price (preferred when given)
  and/or a model price from the engines (European: closed form; American: CRR 500 steps), with
  one market (spot, carry) per underlying. Model minus market is recorded, so the ledger doubles
  as an out-of-sample record of model error. Marks at time t use only trades effective at or
  before t.
- **Expiry:** positions past expiry are marked at intrinsic and flagged; a settlement event
  closes them at intrinsic against the recorded settlement price (cash-equivalent).
- **Invariant:** equity - starting cash = net realized + unrealized, checked on every mark
  (tolerance 1e-9 currency units, because average prices come from Decimal division).
- **Privacy:** ledgers live under git-ignored `data/`, are never logged, and tests are forced to
  use temporary data directories.

## Not done (deliberately)
Live broker connectivity and order placement; real-time quotes; multi-currency accounts; tax lots
(FIFO/LIFO) and corporate-action adjustments. Any move toward real orders should start in the
broker's own paper environment, with explicit per-order confirmation.
