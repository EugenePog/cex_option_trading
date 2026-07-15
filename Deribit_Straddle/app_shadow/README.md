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
- **Open price** (two-tier):
  1. **1st priority — average of real trades.** For each leg, the engine pulls
     the REAL trades executed on that instrument within a UTC window today
     (default `08:00–08:15`, see `config.py`) via Deribit's
     `get_last_trades_by_instrument` (backward-paged), and uses their **average
     price rounded to 4 decimals** as the open price. The trades used are saved
     to `data/straddles_history_prod_shadow_real_trades.csv`.

     Because this is the average over the **whole** window, the strategy **waits
     for the window to close** before opening — it skips earlier cycles (logging
     "waiting for trade-price window to close") and opens just after the window
     end. **Make sure the strategy's `timeframe_end` is later than
     `TRADE_PRICE_WINDOW_END`**, otherwise the trade window never gets used.
  2. **Fallback — order book at `timeframe_start`.** If the completed window had
     no trades, the leg fills at the top of book (SHORT at `best_bid`, LONG at
     `best_ask`) taken from the order-book snapshot captured at
     **`timeframe_start` (e.g. 08:01)** — *not* the book at window-close. Those
     snapshots are persisted (see below) and reloaded on restart, so a crash
     between 08:01 and open time doesn't lose the timeframe-start price.

     If that snapshot fails the **spread guard** (`|bid−ask| / max(bid,ask) >
     bid_ask_threshold`, or bid/ask missing), the snapshot is **refreshed with
     the current live book and re-checked on every run** — each refresh is
     appended to the order-book CSV and replaces the operative snapshot — until
     the spread condition is met and the simulated fill happens (or the
     timeframe ends).
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

## Output: `data/straddles_history_prod_shadow_real_trades.csv`
When the open price comes from real trades (tier 1 above), every trade used in
the average is logged here: `option, token, option_type, trade_time, price,
size, iv`. This is your audit trail for how each leg's open price was derived.
The time window and rounding are configurable in `config.py`
(`TRADE_PRICE_WINDOW_START` / `TRADE_PRICE_WINDOW_END` / `TRADE_PRICE_DECIMALS`,
toggle with `TRADE_PRICE_FROM_WINDOW`).

## Output: `data/straddles_history_prod_shadow_order_book.csv`
At `timeframe_start` the engine snapshots the live top-of-book for the target
call and put and records it here: `timestamp, instrument, bid_price, ask_price,
bid_size, ask_size`. The first snapshot per instrument per day is taken at
`timeframe_start`; if it fails the spread guard, a **refreshed snapshot row is
appended on each subsequent run** until the spread condition is met. On restart
the **latest** row per instrument (today) is reloaded as the operative book.
This is the book the **no-trades fallback** prices off, and it doubles as a
failure-resistance record of market state at decision time.

## Output: `data/straddles_history_prod_shadow_combined.csv`
After each settlement, a postprocessing step rebuilds this combined file, pairing
the call + put legs of each expiry (per token) into **one straddle row**. Columns:

`open_day, expiry_day, call_open_time, put_open_time, call_instId, put_instId,
expiry_time, call_sell_px, put_sell_px, open_premium, call_expiry, put_expiry,
call_expiry_pnl, put_expiry_pnl, close_pnl, fee, net_pnl`

where (amounts in the option's coin):
- `open_premium` = `(call_sell_px·sz + put_sell_px·sz) − fee` (premium net of fees)
- `call_expiry_pnl` / `put_expiry_pnl` = `−intrinsic` for that leg (0 if it
  expired worthless; the `*_expiry` column reads `expired_profit` / `expired_loss`)
- `close_pnl` = `call_expiry_pnl + put_expiry_pnl`
- `fee` = total entry + settlement fees for both legs
- `net_pnl` = `open_premium + close_pnl` = the straddle's all-in realized PnL

The file is rebuilt in full from the settled rows on every settlement
(idempotent). Open timestamps and contract size are recovered from the position
store by `position_id`.

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