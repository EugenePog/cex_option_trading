Important: open_position is no longer atomic
This is the biggest behavioral change. OKX's place_multiple_orders sends both legs in a single HTTP request — even if one leg's sCode came back non-zero, the network round-trip was atomic. Deribit has no batch endpoint for options, so the call leg goes first, then the put leg, sequentially via /private/sell (or /private/buy).
What this means for failure modes:

If call succeeds and put fails (network blip, rate limit, post-only race), app end up with a single-leg short — naked call exposure.
The function still returns the OKX-shaped {call: {...}, put: {...}} dict, with sCode="1" and sMsg containing the error string on whichever leg failed.
The strategy's existing logic (if position and position.get("status") != "error") won't catch a single-leg failure — it'll happily save a half-position to CSV.

Two ways to harden this to choose:

Rollback on partial failure: if call succeeded but put failed, cancel the call before returning. Adds 1 API call but guarantees no half-positions.
Mark the whole thing as error if any leg failed: change the status field at the bottom to "placed" only if both legs succeeded. Easier to add and matches a reasonable retry semantic.

Worth doing #2 at minimum before going live.
State name mapping
Deribit's order states are open, filled, cancelled, rejected, untriggered. Now strategy formatter (state_emoji dict in strategy_straddle_short.py:10) expects OKX names like live, partially_filled, mmp_canceled. I added a _map_state helper that translates:

open + 0 < filled_amount < amount → partially_filled
open (otherwise) → live
rejected → mmp_canceled
untriggered → live
filled / cancelled → pass through

So emoji lookup keeps working with no formatter changes.

Unit / semantics differences that don't break the contract but matter operationally

Prices (avg_px, px, mark_px) are in BTC for BTC options, not USD. App formatter prints them as-is (e.g. px: 0.0345) — readable, just be aware the number is BTC.
Sizes are in contracts where 1 BTC contract = 1 BTC (Deribit), versus 0.01 BTC on OKX. The deribit_position_size_multiplier=100 in settings.json will produce 100× the OKX exposure at the same amount=0.01. Recalibrate before going live (multiplier=1 is closer to right; check the instrument's min_trade_amount field — typically 0.1 for BTC options on Deribit).
fee comes from Deribit's commission field on the order/fill object. Same units as price (BTC). OKX returned USD-denominated fees. App formatter just prints the raw value.

Endpoint choices worth knowing

Historical price (get_token_price with price_time): Deribit doesn't have a clean per-minute historical index endpoint, so app use /public/get_tradingview_chart_data on BTC-PERPETUAL with 1-minute resolution. Perpetual tracks spot tightly via funding, so it's a fine proxy for "BTC price at 8:00 UTC." If app ever need true index history, the /public/get_volatility_index_data endpoint exists for vol indices but not spot indices at minute granularity.
Tick size: Deribit has a singular /public/get_instrument that returns one instrument's details directly — much simpler than OKX where app had to fetch the whole chain and filter locally.
Open positions: /private/get_positions requires a currency parameter, so app pass token straight through. One call per token instead of fetching all options and filtering by instId.startswith.