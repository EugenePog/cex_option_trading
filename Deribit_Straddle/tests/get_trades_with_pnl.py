
import os
import okx.Account as Account
from dotenv import load_dotenv
import csv
from datetime import datetime, timezone
from collections import defaultdict

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH       = os.path.join(BASE_DIR, "token.json")


def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
    return creds


def _get_drive_service():
    return build("drive", "v3", credentials=_get_credentials())


from collections import defaultdict

def add_pnl_waterfall_chart(spreadsheet_id: str, tab_title: str = "PnL Chart"):
    """
    Create or refresh a tab containing a waterfall chart of net_pnl by expiry_day,
    with a final 'Total' bar. Re-running replaces the existing chart in place.
    """
    sheets = build("sheets", "v4", credentials=_get_credentials())

    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets_meta = meta["sheets"]
    data_sheet_title = sheets_meta[0]["properties"]["title"]

    # Read source data
    rows = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{data_sheet_title}'",
    ).execute().get("values", [])
    if not rows:
        print("No data to chart")
        return

    headers = rows[0]
    expiry_idx = headers.index("expiry_day")
    pnl_idx    = headers.index("net_pnl")

    # Aggregate net_pnl by expiry_day (handles split call/put rows on same day)
    daily = defaultdict(float)
    for row in rows[1:]:
        if len(row) <= max(expiry_idx, pnl_idx):
            continue
        day = row[expiry_idx]
        if not day or day == "-":
            continue
        try:
            daily[day] += float(row[pnl_idx])
        except (ValueError, TypeError):
            continue

    sorted_days = sorted(daily.keys())
    total = sum(daily.values())
    chart_data = (
        [["expiry_day", "net_pnl"]]
        + [[d, daily[d]] for d in sorted_days]
        + [["Total", total]]
    )
    n_rows = len(chart_data)
    last_data_idx = len(sorted_days)  # zero-based index of the Total row in the series

    # Find or create the chart tab
    chart_tab = next((s for s in sheets_meta if s["properties"]["title"] == tab_title), None)
    if chart_tab is None:
        resp = sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_title}}}]},
        ).execute()
        chart_tab_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    else:
        chart_tab_id = chart_tab["properties"]["sheetId"]
        # Wipe values and any existing embedded charts so re-runs stay idempotent
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_title}'",
        ).execute()
        del_requests = [
            {"deleteEmbeddedObject": {"objectId": ch["chartId"]}}
            for ch in chart_tab.get("charts", [])
        ]
        if del_requests:
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": del_requests},
            ).execute()

    # Write aggregated data
    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": chart_data},
    ).execute()

    # Add waterfall chart
    chart_request = {
        "addChart": {
            "chart": {
                "spec": {
                    "title": "Net PnL by Expiry Day (BTC)",
                    "waterfallChart": {
                        "stackedType": "STACKED",
                        "hideConnectorLines": False,
                        "firstValueIsTotal": False,
                        "domain": {
                            "data": {"sourceRange": {"sources": [{
                                "sheetId":         chart_tab_id,
                                "startRowIndex":   0,
                                "endRowIndex":     n_rows,
                                "startColumnIndex": 0,
                                "endColumnIndex":  1,
                            }]}}
                        },
                        "series": [{
                            "data": {"sourceRange": {"sources": [{
                                "sheetId":         chart_tab_id,
                                "startRowIndex":   0,
                                "endRowIndex":     n_rows,
                                "startColumnIndex": 1,
                                "endColumnIndex":  2,
                            }]}},
                            "positiveColumnsStyle": {"color": {"red": 0.26, "green": 0.52, "blue": 0.96}},
                            "negativeColumnsStyle": {"color": {"red": 0.92, "green": 0.26, "blue": 0.21}},
                            "subtotalColumnsStyle": {"color": {"red": 0.62, "green": 0.62, "blue": 0.62}},
                            "customSubtotals": [{
                                "subtotalIndex":  last_data_idx,
                                "label":          "Total",
                                "dataIsSubtotal": True,
                            }],
                        }],
                    },
                },
                "position": {"overlayPosition": {
                    "anchorCell":   {"sheetId": chart_tab_id, "rowIndex": 1, "columnIndex": 3},
                    "widthPixels":  900,
                    "heightPixels": 450,
                }},
            }
        }
    }

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [chart_request]},
    ).execute()

    print(f"Added waterfall chart with {len(sorted_days)} days, total = {total:.6f}")


def upload_csv_as_gsheet(csv_path: str, sheet_name: str | None = None,
                         folder_id: str | None = None) -> str:
    """
    Upload a CSV file to Google Drive, converting it to a Google Sheets file.
    If a sheet with the same name already exists (in the same folder), its
    contents are replaced instead of creating a duplicate.
    Returns the Google Drive file ID.
    """
    service = _get_drive_service()
    name = sheet_name or os.path.splitext(os.path.basename(csv_path))[0]

    # Look for an existing sheet with the same name to update in place
    query = (
        f"name = '{name}' "
        f"and mimeType = 'application/vnd.google-apps.spreadsheet' "
        f"and trashed = false"
    )
    if folder_id:
        query += f" and '{folder_id}' in parents"

    existing = service.files().list(q=query, fields="files(id)").execute().get("files", [])
    media = MediaFileUpload(csv_path, mimetype="text/csv", resumable=True)

    if existing:
        file_id = existing[0]["id"]
        file = service.files().update(
            fileId=file_id,
            media_body=media,
            fields="id, webViewLink",
        ).execute()
        print(f"Updated existing sheet: {file.get('webViewLink')}")
    else:
        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.spreadsheet",
        }
        if folder_id:
            metadata["parents"] = [folder_id]
        file = service.files().create(
            body=metadata,
            media_body=media,
            fields="id, webViewLink",
        ).execute()
        print(f"Created new sheet: {file.get('webViewLink')}")

    return file["id"]

def _date_from_time(time_str: str) -> str:
    """Extract YYYY-MM-DD from a 'YYYY-MM-DD HH:MM:SS UTC' string."""
    if not time_str or time_str == "-":
        return "-"
    return time_str[:10]

def get_trades_history(api_key: str, api_secret: str, passphrase: str, flag: str, inst_type: str = "OPTION") -> list:
    account_api = Account.AccountAPI(api_key, api_secret, passphrase, use_server_time=False, flag=flag)
    
    all_trades = []
    after = ""

    while True:
        params = {"instType": inst_type, "limit": "100"}
        if after:
            params["after"] = after

        response = account_api.get_account_bills_archive(**params)

        if response.get("code") != "0":
            raise ValueError(f"Failed to get trades: {response.get('msg')}")

        trades = response.get("data", [])
        if not trades:
            break

        all_trades.extend(trades)
        after = trades[-1].get("billId", "")
        
        if len(trades) < 100:
            break

    return all_trades


def _fmt_time(ts: str) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

SUBTYPES = {
    "1":   "buy",
    "2":   "sell",
    "171": "expired_loss",
    "172": "expired_profit",
}

TYPES = {
    "2": "trade",
    "3": "delivery",
}

def parse_trades(raw_trades: list) -> list:
    result = []
    for t in raw_trades:
        sub_type = t.get("subType", "")
        trade_type = t.get("type", "")

        # px is trade price for trades, index price for delivery
        is_trade = trade_type == "2"
        px = float(t.get("px", 0) or 0) if is_trade else None

        result.append({
            "time":       _fmt_time(t.get("ts")),
            "instId":     t.get("instId") or "",
            "type":       TYPES.get(trade_type, trade_type),
            "action":     SUBTYPES.get(sub_type, sub_type),
            "fill_sz":    float(t.get("sz", 0) or 0),
            "fill_px":    px,                                    # trade price (None for expiry)
            "fill_px_usd": float(t.get("fillPxUsd", 0) or 0),  # USD equivalent
            "pnl":        float(t.get("pnl", 0) or 0),
            "fee":        float(t.get("fee", 0) or 0),
            "bal_chg":    float(t.get("balChg", 0) or 0),
            "ord_id":     t.get("ordId") or "",
        })
    return result

def print_trades(trades: list):
    if not trades:
        print("No trades found")
        return

    total_pnl = sum(t["pnl"] for t in trades)
    total_fee = sum(t["fee"] for t in trades)

    print(f"\n{'Time':<26} {'instId':<30} {'Type':<10} {'Action':<16} {'Size':>5} {'Price':>8} {'PnL':>12} {'Fee':>10}")
    print("-" * 120)
    for t in trades:
        px_str = f"{t['fill_px']:.4f}" if t["fill_px"] is not None else "expiry"
        print(
            f"{t['time']:<26} "
            f"{t['instId']:<30} "
            f"{t['type']:<10} "
            f"{t['action']:<16} "
            f"{t['fill_sz']:>5.0f} "
            f"{px_str:>8} "
            f"{t['pnl']:>12.6f} "
            f"{t['fee']:>10.6f}"
        )
    print("-" * 120)
    print(f"{'TOTAL':<97} PnL: {total_pnl:>12.6f}  Fee: {total_fee:>10.6f}")

def combine_straddle_trades(trades: list) -> list:
    """
    Match call and put legs by their underlying series (same expiry + strike).
    Sells with matching base instId (everything except trailing -C/-P) are paired
    into a straddle, regardless of when each leg was opened.
    """
    sells    = [t for t in trades if t["action"] == "sell"]
    expiries = {t["instId"]: t for t in trades if t["action"] in ("expired_profit", "expired_loss")}

    def base(inst_id: str) -> str:
        # "BTC-USD-260226-65000-C" -> "BTC-USD-260226-65000"
        return inst_id.rsplit("-", 1)[0] if inst_id else ""

    # Group sells by the underlying series
    series_legs = defaultdict(list)
    for s in sells:
        series_legs[base(s["instId"])].append(s)

    straddles = []
    for series, legs in series_legs.items():
        call = next((l for l in legs if l["instId"].endswith("-C")), None)
        put  = next((l for l in legs if l["instId"].endswith("-P")), None)

        if not call and not put:
            continue

        call_expiry = expiries.get(call["instId"]) if call else None
        put_expiry  = expiries.get(put["instId"])  if put  else None

        open_premium = ((call.get("bal_chg", 0) or 0) if call else 0) + \
                       ((put.get("bal_chg", 0)  or 0) if put  else 0)
        close_pnl    = ((call_expiry.get("bal_chg", 0) or 0) if call_expiry else 0) + \
                       ((put_expiry.get("bal_chg", 0)  or 0) if put_expiry  else 0)
        total_fee    = ((call.get("fee", 0) or 0) if call else 0) + \
                       ((put.get("fee", 0)  or 0) if put  else 0) + \
                       ((call_expiry.get("fee", 0) or 0) if call_expiry else 0) + \
                       ((put_expiry.get("fee", 0)  or 0) if put_expiry  else 0)
        net_pnl = open_premium + close_pnl

        open_times = [t for t in (call["time"] if call else "",
                                  put["time"]  if put  else "") if t]
        open_day = _date_from_time(min(open_times)) if open_times else "-"

        expiry_time_str = (call_expiry["time"] if call_expiry
                           else put_expiry["time"] if put_expiry else "")
        expiry_day = _date_from_time(expiry_time_str)

        straddles.append({
            "open_day":        open_day,
            "expiry_day":      expiry_day,
            "call_instId":     call["instId"] if call else "-",
            "put_instId":      put["instId"]  if put  else "-",
            "call_open_time":  call["time"] if call else "-",
            "put_open_time":   put["time"]  if put  else "-",
            "expiry_time":     call_expiry["time"] if call_expiry
                               else (put_expiry["time"] if put_expiry else "-"),
            "call_sell_px":    call["fill_px"] if call else None,
            "put_sell_px":     put["fill_px"]  if put  else None,
            "call_expiry":     call_expiry["action"] if call_expiry else "-",
            "put_expiry":      put_expiry["action"]  if put_expiry  else "-",
            "call_expiry_pnl": call_expiry["pnl"] if call_expiry else 0,
            "put_expiry_pnl":  put_expiry["pnl"]  if put_expiry  else 0,
            "open_premium":    round(open_premium, 8),
            "close_pnl":       round(close_pnl, 8),
            "fee":             round(total_fee, 8),
            "net_pnl":         round(net_pnl, 8),
        })

    # Sort by the earliest leg open time
    def sort_key(s):
        times = [t for t in (s["call_open_time"], s["put_open_time"]) if t != "-"]
        return min(times) if times else ""
    straddles.sort(key=sort_key)
    return straddles


def print_straddles(straddles: list):
    if not straddles:
        print("No straddles found")
        return

    total_net = sum(s["net_pnl"] for s in straddles)

    print(f"\n{'Call Open Time':<26} {'Put Open Time':<26} "
          f"{'Call instId':<30} {'Put instId':<30} "
          f"{'C.Px':>7} {'P.Px':>7} {'C.Exp':>16} {'P.Exp':>16} "
          f"{'Premium':>11} {'Call PnL':>11} {'Put PnL':>11} "
          f"{'Cls PnL':>11} {'Fee':>10} {'Net PnL':>11}")
    print("-" * 200)
    for s in straddles:
        c_exp = s["call_expiry"] or "-"
        p_exp = s["put_expiry"]  or "-"
        c_px  = f"{s['call_sell_px']:.4f}" if s["call_sell_px"] else "-"
        p_px  = f"{s['put_sell_px']:.4f}"  if s["put_sell_px"]  else "-"
        print(
            f"{s['call_open_time']:<26} "
            f"{s['put_open_time']:<26} "
            f"{s['call_instId']:<30} "
            f"{s['put_instId']:<30} "
            f"{c_px:>7} "
            f"{p_px:>7} "
            f"{c_exp:>16} "
            f"{p_exp:>16} "
            f"{s['open_premium']:>11.6f} "
            f"{s['call_expiry_pnl']:>11.6f} "
            f"{s['put_expiry_pnl']:>11.6f} "
            f"{s['close_pnl']:>11.6f} "
            f"{s['fee']:>10.6f} "
            f"{s['net_pnl']:>11.6f}"
        )
    print("-" * 200)
    print(f"{'TOTAL':<175} {'Net PnL:':>8} {total_net:>11.6f}")

def merge_straddles_with_csv(new_straddles: list, filepath: str) -> list:
    """
    Merge newly computed straddles with the existing CSV:
      - new series are appended;
      - series whose previous row was still unexpired are updated with the latest data;
      - fully complete (expired) rows from the CSV are preserved as-is, even if they
        have fallen outside the current API archive window.
    Returns the merged list sorted by earliest open time.
    """
    def row_key(row):
        inst = row.get("call_instId") if row.get("call_instId") not in (None, "", "-") \
               else row.get("put_instId", "")
        return inst.rsplit("-", 1)[0] if inst else ""

    def is_complete(row):
        has_call = row.get("call_instId") not in (None, "", "-")
        has_put  = row.get("put_instId")  not in (None, "", "-")
        if has_call and row.get("call_expiry") in (None, "", "-"):
            return False
        if has_put and row.get("put_expiry") in (None, "", "-"):
            return False
        return has_call or has_put

    # Load existing rows (if any)
    existing = {}
    if os.path.exists(filepath):
        with open(filepath, "r", newline="") as f:
            for row in csv.DictReader(f):
                key = row_key(row)
                if key:
                    existing[key] = row

    # Merge — new wins for missing or incomplete rows; old wins if already complete
    added = updated = 0
    for ns in new_straddles:
        key = row_key(ns)
        if not key:
            continue
        if key not in existing:
            existing[key] = ns
            added += 1
        elif not is_complete(existing[key]):
            existing[key] = ns
            updated += 1
        # else: row is already final — keep it as-is

    unchanged = len(existing) - added - updated
    print(f"Merge: {added} new, {updated} updated, {unchanged} unchanged")

    # Sort by earliest leg open time
    def sort_key(row):
        times = [t for t in (row.get("call_open_time", ""), row.get("put_open_time", ""))
                 if t and t != "-"]
        return min(times) if times else ""
    return sorted(existing.values(), key=sort_key)

def save_straddles_to_csv(straddles: list, filepath: str = "data/straddles_history.csv"):
    """Merge with existing CSV and write the full history."""
    fieldnames = [
        "open_day", "expiry_day",
        "call_open_time", "put_open_time",
        "call_instId", "put_instId", "expiry_time",
        "call_sell_px", "put_sell_px", "open_premium",
        "call_expiry", "put_expiry", "call_expiry_pnl", "put_expiry_pnl",
        "close_pnl", "fee", "net_pnl",
    ]

    merged = merge_straddles_with_csv(straddles, filepath)

    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    with open(filepath, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(merged)

    print(f"Saved {len(merged)} straddles to {filepath}")

# Usage
if __name__ == "__main__":
    API_KEY = os.getenv("OKX_K_API_KEY")
    API_SECRET = os.getenv("OKX_K_API_SECRET")
    PASSPHRASE = os.getenv("OKX_K_PASSPHRASE")
    FLAG = os.getenv("OKX_K_FLAG")

    #API_KEY = os.getenv("OKX_API_KEY_DEMO")
    #API_SECRET = os.getenv("OKX_API_SECRET_DEMO")
    #PASSPHRASE = os.getenv("OKX_PASSPHRASE")
    #FLAG = os.getenv("OKX_FLAG")

    raw    = get_trades_history(API_KEY, API_SECRET, PASSPHRASE, FLAG, inst_type="OPTION")
    trades = parse_trades(raw)
    print_trades(trades)

    straddles = combine_straddle_trades(trades)
    print_straddles(straddles)

    save_straddles_to_csv(straddles, filepath="data/straddles_history.csv")

    sheet_id = upload_csv_as_gsheet(
        csv_path="data/straddles_history.csv",
        sheet_name="OKX_straddles_history",
        folder_id="1ItwC1GILxBWcCoheSwQtvJafdlvTnkQ3",  # optional: paste a Drive folder ID here
    )

    add_pnl_waterfall_chart(sheet_id)