"""Dump IBKR market scanner parameters so we can design the YAML schema.

This is a one-off exploration script. It calls reqScannerParameters() on
a connected IB Gateway / TWS, saves the raw XML, and writes a focused
markdown summary highlighting the scan codes and filter tags relevant to
small-cap momentum / gapper scans.

Usage:
    .venv/bin/python scripts/explore_scanner_params.py
    .venv/bin/python scripts/explore_scanner_params.py --port 4002 --client-id 99

Outputs (in current directory):
    scanner_params.xml — full raw XML returned by reqScannerParameters
    scanner_params.md  — focused summary (scan codes + filters by category)
"""

from __future__ import annotations

import argparse
import asyncio
import xml.etree.ElementTree as ET
from pathlib import Path

from ib_async import IB


async def fetch_params(host: str, port: int, client_id: int) -> str:
    ib = IB()
    await ib.connectAsync(host, port, clientId=client_id)
    try:
        return await ib.reqScannerParametersAsync()
    finally:
        if ib.isConnected():
            ib.disconnect()


def text(el: ET.Element, tag: str, default: str = "") -> str:
    found = el.find(tag)
    if found is None or found.text is None:
        return default
    return found.text.strip()


def write_summary(xml_text: str, out: Path) -> None:
    root = ET.fromstring(xml_text)

    scan_types = list(root.iter("ScanType"))

    # Filter elements: RangeFilter, SimpleFilter, AbstractField etc. all
    # share a <code> child. ScanType uses <scanCode>, so this is unambiguous.
    filters = [el for el in root.iter() if el.find("code") is not None]

    instruments = list(root.iter("Instrument"))
    location_codes = sorted(
        {el.text.strip() for el in root.iter("locationCode") if el.text}
    )

    lines: list[str] = []
    lines.append("# IBKR Scanner Parameters Summary\n")
    lines.append(f"- Raw XML size: **{len(xml_text):,} bytes**")
    lines.append(f"- Scan codes (`<ScanType>`): **{len(scan_types)}**")
    lines.append(f"- Filter parameters (have `<code>`): **{len(filters)}**")
    lines.append(f"- Instrument types: **{len(instruments)}**")
    lines.append(f"- Distinct location codes: **{len(location_codes)}**\n")

    # ---------------------------------------------------------------- scan codes
    relevant_keywords = (
        "GAIN", "LOSS", "VOLUME", "ACTIVE", "HOT", "PERC",
        "MOST", "GAP", "TOP", "RANGE", "RATIO", "FLOAT",
    )
    relevant_scans = [
        st for st in scan_types
        if any(k in text(st, "scanCode").upper() for k in relevant_keywords)
    ]
    lines.append(f"## Relevant scan codes ({len(relevant_scans)} of {len(scan_types)})\n")
    lines.append("| scanCode | displayName |")
    lines.append("|---|---|")
    for st in relevant_scans:
        lines.append(f"| `{text(st, 'scanCode')}` | {text(st, 'displayName')} |")
    lines.append("")

    lines.append(f"<details><summary>All {len(scan_types)} scan codes</summary>\n")
    lines.append("| scanCode | displayName |")
    lines.append("|---|---|")
    for st in scan_types:
        lines.append(f"| `{text(st, 'scanCode')}` | {text(st, 'displayName')} |")
    lines.append("\n</details>\n")

    # ---------------------------------------------------------------- filters
    def filter_section(title: str, keywords: tuple[str, ...]) -> None:
        matching = []
        for f in filters:
            code = text(f, "code").lower()
            display = text(f, "displayName").lower()
            if any(k in code for k in keywords) or any(k in display for k in keywords):
                matching.append(f)
        lines.append(f"## {title} ({len(matching)})\n")
        if not matching:
            lines.append("_No matching filter tags found._\n")
            return
        lines.append("| code | displayName | category | tag |")
        lines.append("|---|---|---|---|")
        for f in matching:
            lines.append(
                f"| `{text(f, 'code')}` | {text(f, 'displayName')} | "
                f"{text(f, 'category')} | `{f.tag}` |"
            )
        lines.append("")

    filter_section("Price filters", ("price",))
    filter_section("Change / percent filters", ("change", "perc"))
    filter_section("Volume filters", ("volume",))
    filter_section("Float / shares filters", ("float", "shares", "outstand"))
    filter_section("Market cap filters", ("market", "cap"))
    filter_section("Relative-volume / ratio filters", ("rel", "ratio", "rvol"))

    # ---------------------------------------------------------------- instruments
    lines.append(f"## Instrument types ({len(instruments)})\n")
    lines.append("| type | name | filters |")
    lines.append("|---|---|---|")
    for inst in instruments:
        lines.append(
            f"| `{text(inst, 'type')}` | {text(inst, 'name')} | "
            f"{text(inst, 'filters')} |"
        )
    lines.append("")

    # ---------------------------------------------------------------- locations
    us_stk = [c for c in location_codes if c.startswith("STK.US")]
    lines.append(f"## US stock location codes ({len(us_stk)})\n")
    for code in us_stk:
        lines.append(f"- `{code}`")
    lines.append("")
    lines.append(f"<details><summary>All {len(location_codes)} location codes</summary>\n")
    for code in location_codes:
        lines.append(f"- `{code}`")
    lines.append("\n</details>\n")

    out.write_text("\n".join(lines))


async def main() -> None:
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
    parser.add_argument("--xml-out", type=Path, default=Path("scanner_params.xml"))
    parser.add_argument("--summary-out", type=Path, default=Path("scanner_params.md"))
    args = parser.parse_args()

    print(f"connecting to {args.host}:{args.port} (client_id={args.client_id})…")
    xml_text = await fetch_params(args.host, args.port, args.client_id)
    args.xml_out.write_text(xml_text)
    print(f"wrote raw XML → {args.xml_out} ({len(xml_text):,} bytes)")

    write_summary(xml_text, args.summary_out)
    print(f"wrote summary → {args.summary_out}")

    root = ET.fromstring(xml_text)
    n_scans = len(list(root.iter("ScanType")))
    n_filters = len([el for el in root.iter() if el.find("code") is not None])
    print(f"\nfound {n_scans} scan codes and {n_filters} filter parameters")
    print(f"open {args.summary_out} for the focused summary")


if __name__ == "__main__":
    asyncio.run(main())
