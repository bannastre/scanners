"""Probe whether reqFundamentalData works on this account.

Connects to TWS / IB Gateway, qualifies a symbol, and calls
``reqFundamentalDataAsync`` for a few report types. Dumps the raw XML
to disk for inspection and prints the nodes that look like share-count
/ float so you can see at a glance whether the subscription is live.

Usage:
    .venv/bin/python scripts/probe_fundamentals.py
    .venv/bin/python scripts/probe_fundamentals.py --symbol TSLA
    .venv/bin/python scripts/probe_fundamentals.py --port 4002 --client-id 99

Interpreting the output:
    - Multi-KB of XML written + shares-outstanding node printed →
      subscription is live; we can wire this into the TUI.
    - Empty string returned / "no fundamental data available" error →
      subscription hasn't propagated or isn't the right SKU. Restart
      TWS and retry; if still empty, open IBKR chat.
    - Error 430 / 10192 / 10194 → permission denied by entitlement.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from ib_async import IB, Stock

# Default to unbuffered output so a hang on connect doesn't swallow
# progress lines. Without this, stdout is line-buffered when redirected
# and the "connecting to…" print never reaches the terminal.
sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

CONNECT_TIMEOUT_S = 10.0

# ReportSnapshot and ReportsOwnership are the two report types that
# carry the share-count data we care about. Others (Financials, RESC,
# CalendarReport) are orthogonal and would just add noise; skip them
# unless you want to see what the account has access to.
REPORT_TYPES = ("ReportSnapshot", "ReportsOwnership")

# XPaths that commonly hold shares-outstanding / float in Reuters
# fundamentals payloads. We search case-insensitively across all text
# nodes and element attributes, so this list doesn't need to be
# exhaustive — it's only the set we explicitly callout in the summary.
SHARE_TAG_HINTS = (
    "sharesoutstanding",
    "sharesout",
    "floatshares",
    "sharesfloat",
    "freefloat",
    "nshrfl",
    "nshrfq",
    "totalfloat",
)


async def probe(host: str, port: int, client_id: int, symbol: str) -> dict[str, str]:
    ib = IB()
    print(f"  → opening socket to {host}:{port} (timeout {CONNECT_TIMEOUT_S}s)…")
    try:
        await asyncio.wait_for(
            ib.connectAsync(host, port, clientId=client_id),
            timeout=CONNECT_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        print(
            f"  ✗ timed out. Nothing is listening on {host}:{port}, or it's "
            f"firewalled. Check that TWS/Gateway is running and that the "
            f"port matches (TWS paper=7497, TWS live=7496, GW paper=4002, "
            f"GW live=4001), and that the API is enabled in Global Config → API → Settings."
        )
        return {}
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ connect failed: {type(exc).__name__}: {exc}")
        return {}
    print(f"  ✓ connected (serverVersion={ib.client.serverVersion()})")
    try:
        contract = Stock(symbol, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        print(f"qualified {symbol}: conId={contract.conId}")

        results: dict[str, str] = {}
        for report_type in REPORT_TYPES:
            print(f"\n→ reqFundamentalData({symbol}, {report_type!r})")
            try:
                xml = await ib.reqFundamentalDataAsync(contract, report_type)
            except Exception as exc:  # noqa: BLE001
                print(f"  error: {type(exc).__name__}: {exc}")
                results[report_type] = ""
                continue
            if not xml:
                print("  (empty response — likely no entitlement for this report)")
                results[report_type] = ""
                continue
            print(f"  {len(xml):,} bytes")
            results[report_type] = xml
        return results
    finally:
        if ib.isConnected():
            ib.disconnect()


def find_share_nodes(xml_text: str) -> list[tuple[str, str]]:
    """Walk the XML and return (path, value) for any element whose tag,
    attribute name, or attribute value looks like a share-count field.
    Not rigorous — just enough to eyeball whether the data we want is
    in there.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"  XML parse error: {exc}")
        return []

    hits: list[tuple[str, str]] = []

    def walk(el: ET.Element, path: str) -> None:
        tag_l = el.tag.lower()
        if any(h in tag_l for h in SHARE_TAG_HINTS):
            hits.append((f"{path}/{el.tag}", (el.text or "").strip()))
        for k, v in el.attrib.items():
            k_l, v_l = k.lower(), v.lower()
            if any(h in k_l or h in v_l for h in SHARE_TAG_HINTS):
                text = (el.text or "").strip()
                hits.append((f"{path}/{el.tag}[@{k}={v!r}]", text))
        for child in el:
            walk(child, f"{path}/{el.tag}")

    walk(root, "")
    return hits


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=4002,
        help="IB Gateway paper=4002, live=4001; TWS paper=7497, live=7496",
    )
    parser.add_argument(
        "--client-id", type=int, default=99,
        help="Use a different client_id than your scanner to avoid conflicts",
    )
    parser.add_argument(
        "--symbol", default="AAPL",
        help="Symbol to probe. Any US-listed stock works; large-caps are "
             "a safer test since they're covered by every fundamentals tier.",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=Path("."),
        help="Directory to write fundamentals_<symbol>_<report>.xml files",
    )
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    print(f"probe_fundamentals: python={sys.executable}")
    print(
        f"probe_fundamentals: host={args.host} port={args.port} "
        f"client_id={args.client_id} symbol={args.symbol}"
    )
    results = asyncio.run(
        probe(args.host, args.port, args.client_id, args.symbol)
    )
    if not results:
        print("\nno results — see errors above.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for report_type, xml in results.items():
        if not xml:
            print(f"\n{report_type}: ✗ no data")
            continue
        path = args.out_dir / f"fundamentals_{args.symbol}_{report_type}.xml"
        path.write_text(xml)
        print(f"\n{report_type}: ✓ {len(xml):,} bytes → {path}")
        hits = find_share_nodes(xml)
        if not hits:
            print("  (no share-count-looking nodes found — inspect the XML)")
            continue
        print(f"  share-count hits ({len(hits)}):")
        # Dedupe by path, keep first value
        seen: set[str] = set()
        for p, v in hits:
            if p in seen:
                continue
            seen.add(p)
            print(f"    {p} = {v!r}")


if __name__ == "__main__":
    main()
