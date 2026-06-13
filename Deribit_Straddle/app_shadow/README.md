# app_shadow — Shadow (paper) trading for the Deribit straddle

Runs your **real** short-straddle strategy logic against **real production
market data**, with **no real money** and **no testnet**. Fills are simulated
against Deribit's live mainnet order book, positions are tracked to expiry, and
realized PnL is written to `data/straddles_history_prod_shadow.csv`.

## Why not testnet?
Deribit's testnet is a synthetic environment with its own thin, stale order
book — its bid/ask prices are not related to the real market, which is exactly
the unrealistic pricing you wanted to avoid. This package instead reads the
**live mainnet public market** (real bid/ask, real chain, real index/delivery
prices) and only *simulates* the execution.

## Safety
This package talks **only to Deribit's public REST endpoints**. It uses **no API
key**, performs **no authentication**, and has **no code path that can place an
order or move funds**. It is physically incapable of trading.

## How it works
- **Decision logic** (`strategy.py`): a faithful mirror of your live
  `StrategyStraddleShort` — same sizing, same allowed-strike / near-money
  selection, same day/timeframe gating.
- **Fill model** (marketable): the whole size fills at the single top-of-book
  price — a SHORT leg at the real `best_bid`, a LONG leg at the real `best_ask`
  — after applying the same tradable-state and bid/ask-spread guards as the live
  `get_option_mark_price`. This models your live limit-past-the-touch orders
  crossing the spread.
- **Fees** (`shadow_engine.py`): Deribit's real model — `0.03%` of the
  underlying (`0.0003`/contract) for maker = taker, **capped at 12.5% of the
  option premium**. Delivery fees are off by default (daily expiries are exempt;
  see `config.py` to enable for non-daily).
- **Settlement**: a background sweep settles expired positions at Deribit's real
  **delivery price** (falling back to the index at expiry if delivery isn't
  published yet — such rows are flagged `settled_provisional`). Realized PnL is
  in the option's coin (BTC for BTC options).

## Position lifecycle: held to expiration
The strategy **never closes a leg early** — every option is held to expiry, just
like the live strategy. There are only two events per leg:
1. **Entry** — one simulated fill when the position is opened (the only active
   trade).
2. **Settlement** — at expiry, against Deribit's real delivery price.

Each run within the trading window only *tops up* to your target size (it never
sells to close). Realized PnL for a short leg is therefore:

```
premium_collected  −  intrinsic_at_settlement  −  entry_fee  −  delivery_fee
```

There is **no exit-trade fee**, because nothing is actively closed. `delivery_fee`
is 0 for your daily expiries (daily options are delivery-fee exempt).

> Note: marketable vs passive fill is about *entry* execution only, not exit.
> Since your live orders are placed past the touch (`bid × (1 − slippage)`),
> they cross the spread and fill at the bid — which is exactly the marketable
> model used here. A passive (resting-limit) model would be *less* accurate for
> your order style, so it is intentionally not used.

## Run
From the project root (the folder that contains `app/`, `app_reporting/`,
`data/`):

```bash
python -m app_shadow
```

It reads the **same** `data/settings.json` as the live app and trades every
token whose `straddle_short_strategy.run_flag == 1`.

## Output: `data/straddles_history_prod_shadow.csv`
Two row types per leg:
- `OPEN` — when a simulated fill happens (entry price + entry fee).
- `SETTLE` — at expiry (settlement price, intrinsic, settlement fee, and
  `realized_pnl_coin`).

Aggregate `realized_pnl_coin` over `SETTLE` rows for your shadow PnL.

State lives in `data/shadow_positions.json` (survives restarts).

## Optional notifications
Set `TELEGRAM_BOT_TOKEN_SHADOW` and `TELEGRAM_CHAT_ID_SHADOW` env vars to get
fill/settlement messages; otherwise everything is logged to
`data/logs/shadow_strategies_executor.log`.

## Realism caveats
- Fills assume your whole size executes at the single top-of-book price, i.e.
  the order doesn't move the market beyond the visible touch (fine for small
  size; optimistic for large size relative to top-of-book depth).
- The marketable model assumes you cross the spread and fill immediately. If
  your live orders often rest passively and earn the spread, real fills would be
  slightly better than modeled here — i.e. this is a *conservative* estimate.