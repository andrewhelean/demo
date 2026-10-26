"""Scrape Amazon PDPs for every ASIN in a prior price sheet and write a same-shaped workbook.

Usage:
    python amazon_pdp_scrape.py INPUT.xlsx [OUTPUT.xlsx] [--delay 4]

Descriptive columns (brand, counts, FPM, ...) are copied from INPUT; the offer columns are
re-scraped. Pages that show a CAPTCHA / robot check are recorded as blocked in
`Amazon Notes`; the script makes no attempt to evade them. Re-run with --retry-blocked to
fill them in later from the partial output.
"""
import argparse
import datetime as dt
import os
import re
import time
from pathlib import Path

import pandas as pd
from playwright.sync_api import Error as PWError
from playwright.sync_api import sync_playwright

OFFER_COLS = [
    "Amazon Title", "Amazon Price", "Amazon Price (num)", "Price vs FPM %", "Coupon",
    "Promotion", "S&S Price", "S&S Discount", "Availability", "Amazon URL", "Amazon Notes",
]
PRICE_RE = re.compile(r"\$\s?([\d,]+(?:\.\d{1,2})?)")


def text(page, selector: str) -> str | None:
    loc = page.locator(selector).first
    if loc.count() == 0:
        return None
    return " ".join((loc.inner_text() or "").split()) or None


def money(s: str | None) -> float | None:
    m = PRICE_RE.search(s or "")
    return float(m.group(1).replace(",", "")) if m else None


def scrape_asin(page, asin: str) -> dict:
    url = f"https://www.amazon.com/dp/{asin}"
    out = dict.fromkeys(OFFER_COLS)
    out["Amazon URL"] = url
    notes: list[str] = []
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        page.wait_for_timeout(1500)
    except PWError as e:
        out["Amazon Price"] = "NO PRICE"
        out["Amazon Notes"] = f"load error: {str(e).splitlines()[0]}"
        return out

    if resp is not None and resp.status == 404:
        out["Amazon Price"] = "NO PRICE"
        out["Amazon Notes"] = "page not found"
        return out
    if page.locator("form[action*='validateCaptcha'], #captchacharacters").count():
        out["Amazon Price"] = "NO PRICE"
        out["Amazon Notes"] = "blocked: robot check"
        return out

    out["Amazon Title"] = text(page, "#productTitle")
    if out["Amazon Title"] is None:
        out["Amazon Price"] = "NO PRICE"
        out["Amazon Notes"] = "page not found" if page.locator("img[alt*='Dogs of Amazon']").count() else "no product title"
        return out

    # Requested ASIN must match the rendered product, else withhold offer fields.
    rendered = page.locator("input#ASIN").first.get_attribute("value") if page.locator("input#ASIN").count() else None
    if rendered and rendered != asin:
        out["Amazon Price"] = "NO PRICE"
        out["Amazon Notes"] = f"redirected to {rendered}; Requested ASIN did not match rendered product; offer fields withheld"
        return out

    # Main buy-box price only (not carousels / other sellers).
    price = text(page, "#corePrice_feature_div .a-price:not(.a-text-price) .a-offscreen") or \
            text(page, "#apex_desktop .a-price:not(.a-text-price) .a-offscreen")
    out["Availability"] = text(page, "#availability")
    if price is None:
        out["Amazon Price"] = "NO PRICE"
        notes.append("no buy box — currently unavailable" if not page.locator("#add-to-cart-button").count()
                     else "no price found; No unambiguous visible main-product OTP price")
    else:
        out["Amazon Price"] = price
        out["Amazon Price (num)"] = money(price)

    coupon = text(page, "#couponBadgeRegularVpc, #vpcButton, [id^='couponText']")
    out["Coupon"] = coupon
    out["Promotion"] = text(page, "#promoPriceBlockMessage_feature_div, #applicable_promotion_list_sec")

    sns = text(page, "#sns-base-price, #snsAccordionRowMiddle .a-price .a-offscreen, #snsDetailPagePrice .a-offscreen")
    if sns and money(sns) is not None:
        out["S&S Price"] = f"${money(sns):.2f}"
        if out["Amazon Price (num)"]:
            disc = round((1 - money(sns) / out["Amazon Price (num)"]) * 100)
            out["S&S Discount"] = f"{max(disc, 0)}%"
    out["Amazon Notes"] = "; ".join(notes) or None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output", nargs="?")
    ap.add_argument("--delay", type=float, default=4.0, help="seconds between requests")
    ap.add_argument("--retry-blocked", action="store_true", help="only re-scrape rows noted as blocked in OUTPUT")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    out_path = Path(args.output or f"amazon_prices_{today}.xlsx")
    df = pd.read_excel(args.input, dtype={"ASIN": str})
    if args.retry_blocked and out_path.exists():
        prev = pd.read_excel(out_path, dtype={"ASIN": str})
        df = prev
        todo = prev["Amazon Notes"].fillna("").str.startswith("blocked")
    else:
        todo = pd.Series(True, index=df.index)

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH"))
        page = browser.new_context(locale="en-US").new_page()
        for i, idx in enumerate(df.index[todo], 1):
            asin = df.at[idx, "ASIN"]
            res = scrape_asin(page, asin)
            for col, val in res.items():
                df.at[idx, col] = val
            print(f"[{i}/{todo.sum()}] {asin} {res['Amazon Price']} {res['Amazon Notes'] or ''}")
            df.to_excel(out_path, index=False, sheet_name="Amazon Prices")  # checkpoint
            time.sleep(args.delay)
        browser.close()

    price, fpm = pd.to_numeric(df["Amazon Price (num)"], errors="coerce"), pd.to_numeric(df["FPM"], errors="coerce")
    df["Price vs FPM %"] = ((price / fpm - 1) * 100).round(2)
    df.to_excel(out_path, index=False, sheet_name="Amazon Prices")
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
