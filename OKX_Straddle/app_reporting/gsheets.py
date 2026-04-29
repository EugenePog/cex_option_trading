"""Build/refresh the waterfall chart of net PnL by expiry day."""
import logging
from collections import defaultdict

from googleapiclient.discovery import build

from .google_auth import get_credentials

log = logging.getLogger(__name__)


def add_pnl_waterfall_chart(spreadsheet_id: str, tab_title: str = "PnL Chart") -> None:
    """Idempotent: re-running replaces values and the embedded chart in place."""
    sheets = build("sheets", "v4", credentials=get_credentials())

    meta = sheets.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets_meta = meta["sheets"]
    data_sheet_title = sheets_meta[0]["properties"]["title"]

    rows = sheets.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{data_sheet_title}'",
    ).execute().get("values", [])
    if not rows:
        log.warning("No data to chart")
        return

    headers = rows[0]
    expiry_idx = headers.index("expiry_day")
    pnl_idx    = headers.index("net_pnl")

    daily: dict[str, float] = defaultdict(float)
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

    sorted_days   = sorted(daily.keys())
    total         = sum(daily.values())
    chart_data    = ([["expiry_day", "net_pnl"]]
                     + [[d, daily[d]] for d in sorted_days]
                     + [["Total", total]])
    n_rows        = len(chart_data)
    last_data_idx = len(sorted_days)

    chart_tab = next((s for s in sheets_meta
                      if s["properties"]["title"] == tab_title), None)
    if chart_tab is None:
        resp = sheets.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": tab_title}}}]},
        ).execute()
        chart_tab_id = resp["replies"][0]["addSheet"]["properties"]["sheetId"]
    else:
        chart_tab_id = chart_tab["properties"]["sheetId"]
        sheets.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_title}'",
        ).execute()
        del_reqs = [{"deleteEmbeddedObject": {"objectId": ch["chartId"]}}
                    for ch in chart_tab.get("charts", [])]
        if del_reqs:
            sheets.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body={"requests": del_reqs},
            ).execute()

    sheets.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{tab_title}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": chart_data},
    ).execute()

    chart_request = {
        "addChart": {"chart": {
            "spec": {
                "title": "Net PnL by Expiry Day (BTC)",
                "waterfallChart": {
                    "stackedType": "STACKED",
                    "hideConnectorLines": False,
                    "firstValueIsTotal": False,
                    "domain": {"data": {"sourceRange": {"sources": [{
                        "sheetId": chart_tab_id,
                        "startRowIndex": 0, "endRowIndex": n_rows,
                        "startColumnIndex": 0, "endColumnIndex": 1,
                    }]}}},
                    "series": [{
                        "data": {"sourceRange": {"sources": [{
                            "sheetId": chart_tab_id,
                            "startRowIndex": 0, "endRowIndex": n_rows,
                            "startColumnIndex": 1, "endColumnIndex": 2,
                        }]}},
                        "positiveColumnsStyle": {"color": {"red": 0.26, "green": 0.52, "blue": 0.96}},
                        "negativeColumnsStyle": {"color": {"red": 0.92, "green": 0.26, "blue": 0.21}},
                        "subtotalColumnsStyle": {"color": {"red": 0.62, "green": 0.62, "blue": 0.62}},
                        "customSubtotals": [{
                            "subtotalIndex": last_data_idx,
                            "label": "Total",
                            "dataIsSubtotal": True,
                        }],
                    }],
                },
            },
            "position": {"overlayPosition": {
                "anchorCell": {"sheetId": chart_tab_id, "rowIndex": 1, "columnIndex": 3},
                "widthPixels": 900, "heightPixels": 450,
            }},
        }}
    }

    sheets.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [chart_request]},
    ).execute()

    log.info("Waterfall chart refreshed: %d days, total = %.6f",
             len(sorted_days), total)
