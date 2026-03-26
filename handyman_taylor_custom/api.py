import base64
import io
import re
import math
import frappe
from PIL import Image, ImageDraw

# ---------------------------------------------------
# Helpers
# ---------------------------------------------------
def _country_code(country_name: str) -> str:
    """Return ISO-3166 alpha-2 country code, default CH."""
    if not country_name:
        return "CH"
    try:
        c = frappe.get_cached_doc("Country", country_name)
        code = (getattr(c, "code", None) or "").strip()
        return (code or "CH").upper()
    except Exception:
        return "CH"


def _split_street_house(line: str):
    """
    Best-effort split of a single address line into (street, house_no).
    Examples:
      "Parkweg 1" -> ("Parkweg", "1")
      "Bahnhofstrasse 12a" -> ("Bahnhofstrasse", "12a")
      "Rue de la Gare 5" -> ("Rue de la Gare", "5")
      "Postfach 123" -> ("Postfach 123", "") (leave unsplit)
    """
    s = (line or "").strip()
    if not s:
        return "", ""
    # Avoid splitting PO box style
    if re.search(r"\b(postfach|p\.?\s*o\.?\s*box|case postale)\b", s, flags=re.I):
        return s, ""
    # Match: everything (ending non-digit) + last token that starts with digits (may contain suffix)
    m = re.match(r"^(.*\D)\s+(\d[\w\/\-\.\s]*)$", s)
    if not m:
        return s, ""
    street = (m.group(1) or "").strip()
    house = (m.group(2) or "").strip()
    if not street:
        return s, ""
    return street, house


def _inject_swiss_cross(svg_text: str) -> str:
    """
    Overlay Swiss cross emblem at the center of an SVG QR.
    Purely visual — does not affect payload validity.
    """
    m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg_text)
    if m:
        w = float(m.group(1))
        h = float(m.group(2))
    else:
        m2 = re.search(r'width="([0-9.]+)"[^>]*height="([0-9.]+)"', svg_text)
        if not m2:
            return svg_text
        w = float(m2.group(1))
        h = float(m2.group(2))
    size = min(w, h)
    cx = w / 2.0
    cy = h / 2.0
    keep = size * 0.26
    red = size * 0.20
    t = size * 0.045
    arm = red * 0.62
    keep_x = cx - keep / 2
    keep_y = cy - keep / 2
    red_x = cx - red / 2
    red_y = cy - red / 2
    vx = cx - t / 2
    vy = cy - arm / 2
    hx = cx - arm / 2
    hy = cy - t / 2
    overlay = f"""
<g id="swiss-cross" shape-rendering="crispEdges">
  <rect x="{keep_x:.3f}" y="{keep_y:.3f}" width="{keep:.3f}" height="{keep:.3f}" fill="#ffffff"/>
  <rect x="{red_x:.3f}" y="{red_y:.3f}" width="{red:.3f}" height="{red:.3f}" fill="#d0021b"/>
  <rect x="{vx:.3f}" y="{vy:.3f}" width="{t:.3f}" height="{arm:.3f}" fill="#ffffff"/>
  <rect x="{hx:.3f}" y="{hy:.3f}" width="{arm:.3f}" height="{t:.3f}" fill="#ffffff"/>
</g>
""".strip()
    return re.sub(r"</svg>\s*$", overlay + "\n</svg>", svg_text, flags=re.I)


def _arc_path(cx: float, cy: float, r: float, a0: float, a1: float) -> str:
    """
    SVG arc path from angle a0 to a1 (radians), using 'A' command.
    Assumes a1-a0 <= pi (small arc), which we will keep.
    """
    x0 = cx + r * math.cos(a0)
    y0 = cy + r * math.sin(a0)
    x1 = cx + r * math.cos(a1)
    y1 = cy + r * math.sin(a1)
    # large-arc-flag=0 (since <=180deg), sweep-flag=1
    return f"M {x0:.3f} {y0:.3f} A {r:.3f} {r:.3f} 0 0 1 {x1:.3f} {y1:.3f}"


def _load_asset_text(*path_parts: str) -> str:
    """
    Load a text asset bundled in the app.
    """
    path = frappe.get_app_path("handyman_taylor_custom", *path_parts)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _inject_google_badge(svg_text: str) -> str:
    """
    Center badge: Google 'G' SVG.
    """
    m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg_text)
    if m:
        w = float(m.group(1)); h = float(m.group(2))
    else:
        m2 = re.search(r'width="([0-9.]+)"[^>]*height="([0-9.]+)"', svg_text)
        if not m2:
            return svg_text
        w = float(m2.group(1)); h = float(m2.group(2))
    size = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    keep = size * 0.24
    logo = size * 0.16
    keep_x = cx - keep / 2
    keep_y = cy - keep / 2
    logo_x = cx - logo / 2
    logo_y = cy - logo / 2
    try:
        google_svg_full = _load_asset_text("assets", "google_g.svg").strip()
        google_svg_full = re.sub(r"<\?xml.*?\?>", "", google_svg_full, flags=re.S).strip()
        vb = re.search(
            r'viewBox="([\-0-9.]+)\s+([\-0-9.]+)\s+([0-9.]+)\s+([0-9.]+)"',
            google_svg_full,
            flags=re.I
        )
        if vb:
            vb_x, vb_y, vb_w, vb_h = map(float, vb.groups())
        else:
            vb_x, vb_y, vb_w, vb_h = 0.0, 0.0, 24.0, 24.0
        inner = google_svg_full
        if "<svg" in inner.lower():
            inner = re.sub(r"^.*?<svg[^>]*>", "", inner, flags=re.S | re.I).strip()
            inner = re.sub(r"</svg>\s*$", "", inner, flags=re.S | re.I).strip()
        inner = re.sub(r'<path[^>]+fill="none"[^>]*/>\s*', "", inner, flags=re.I)
    except Exception:
        return svg_text
    s = min(logo / vb_w, logo / vb_h)
    dx = (logo - (vb_w * s)) / 2.0
    dy = (logo - (vb_h * s)) / 2.0
    overlay = f"""
<g id="google-badge" shape-rendering="geometricPrecision">
  <rect x="{keep_x:.3f}" y="{keep_y:.3f}" width="{keep:.3f}" height="{keep:.3f}" fill="#ffffff"/>
  <g transform="translate({(logo_x+dx):.3f},{(logo_y+dy):.3f}) scale({s:.6f}) translate({(-vb_x):.3f},{(-vb_y):.3f})">
    {inner}
  </g>
</g>
""".strip()
    return re.sub(r"</svg>\s*$", overlay + "\n</svg>", svg_text, flags=re.I)


def _inject_trustpilot_badge(svg_text: str) -> str:
    """Center badge: Trustpilot-ish green star in a white keep-out area."""
    m = re.search(r'viewBox="0 0 ([0-9.]+) ([0-9.]+)"', svg_text)
    if m:
        w = float(m.group(1)); h = float(m.group(2))
    else:
        m2 = re.search(r'width="([0-9.]+)"[^>]*height="([0-9.]+)"', svg_text)
        if not m2:
            return svg_text
        w = float(m2.group(1)); h = float(m2.group(2))
    size = min(w, h)
    cx, cy = w / 2.0, h / 2.0
    keep = size * 0.22
    star = size * 0.085
    keep_x = cx - keep / 2
    keep_y = cy - keep / 2
    pts = [
        (cx, cy - star),
        (cx + star * 0.235, cy - star * 0.325),
        (cx + star * 0.951, cy - star * 0.309),
        (cx + star * 0.380, cy + star * 0.124),
        (cx + star * 0.588, cy + star * 0.809),
        (cx, cy + star * 0.450),
        (cx - star * 0.588, cy + star * 0.809),
        (cx - star * 0.380, cy + star * 0.124),
        (cx - star * 0.951, cy - star * 0.309),
        (cx - star * 0.235, cy - star * 0.325),
    ]
    pts_str = " ".join([f"{x:.3f},{y:.3f}" for x, y in pts])
    overlay = f"""
<g id="trustpilot-badge" shape-rendering="geometricPrecision">
  <rect x="{keep_x:.3f}" y="{keep_y:.3f}" width="{keep:.3f}" height="{keep:.3f}" fill="#ffffff"/>
  <polygon points="{pts_str}" fill="#00B67A"/>
</g>
""".strip()
    return re.sub(r"</svg>\s*$", overlay + "\n</svg>", svg_text, flags=re.I)


def _get_company_address_fields(doc, company):
    """
    Return creditor fields for structured address (S):
      name, street, building_no, pincode, city, country_code
    """
    creditor_name = (
        getattr(company, "custom_qr_payee_name", None)
        or company.company_name
        or doc.company
        or ""
    ).strip()[:70]
    street = ""
    building = ""
    pincode = ""
    city = ""
    cc = "CH"
    if getattr(doc, "company_address", None):
        try:
            a = frappe.get_doc("Address", doc.company_address)
            line1 = (a.address_line1 or "").strip()
            street, building = _split_street_house(line1)
            street = street.strip()[:70]
            building = building.strip()[:16]
            pincode = (a.pincode or "").strip()[:16]
            city = (a.city or "").strip()[:35]
            cc = _country_code(a.country)
        except Exception:
            pass
    return creditor_name, street, building, pincode, city, (cc or "CH").upper()


# ---------------------------------------------------
# Build Swiss QR SPC payload (NON reference)
# ---------------------------------------------------
def _build_spc_payload_non_reference(doc, iban: str, amount: float, currency: str, message: str) -> str:
    company = frappe.get_doc("Company", doc.company)
    iban_clean = re.sub(r"[^A-Za-z0-9]", "", (iban or "")).upper()
    amt = ""
    if amount is not None and str(amount) != "":
        amt = f"{float(amount):.2f}"
    cur = (currency or "CHF").upper()
    add_info = (message or "").strip()[:140]
    creditor_name, street, building, pincode, city, cc = _get_company_address_fields(doc, company)
    # For NON-reference, debtor is optional and can be empty.
    # This is represented by 7 empty lines.
    debtor_lines = ["", "", "", "", "", "", ""]
    lines = [
        "SPC",
        "0203",  # Version 2.3 of the spec
        "1",
        iban_clean,
        "S",  # Structured address for creditor
        creditor_name,
        street,
        building,
        pincode,
        city,
        cc,
        # Ultimate creditor was removed in v2.3
        amt,
        cur,
        *debtor_lines,
        "NON",
        "",
        add_info,
        "EPD",
    ]
    return "\n".join(lines)


# ---------------------------------------------------
# QR generators
# ---------------------------------------------------
def _require_segno():
    try:
        import segno  # noqa: F401
    except Exception:
        frappe.throw("Missing dependency: segno")


def _svg_to_data_uri(svg_text: str) -> str:
    b = svg_text.encode("utf-8")
    return "data:image/svg+xml;base64," + base64.b64encode(b).decode("ascii")


def _qr_png_data_uri_from_text(text: str, *, scale: int = 10, border: int = 4) -> str:
    """
    Generates a plain Swiss payment QR code as a PNG data URI.
    The Swiss cross icon is intended to be overlaid separately in the frontend
    (e.g., in an HTML Print Format).
    """
    _require_segno()
    import segno
    import io
    import base64

    # Generate a QR code with High error correction, but no embedded icon.
    qr = segno.make(text or "", micro=False, error='h')
    
    buf = io.BytesIO()
    qr.save(buf, kind='png', scale=scale, border=border)
    buf.seek(0)

    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _qr_svg_data_uri_from_text(
    text: str,
    *,
    scale: int = 2,
    border: int = 3,
    css_class: str = "qr-generic",
    swiss_cross: bool = False,
    badge: str = None,
) -> str:
    """
    Generate a QR code SVG data URI from arbitrary text.
    swiss_cross=True overlays Swiss cross (SVG version – used only for non-payment QRs if needed).
    badge can be: "google", "trustpilot" (for review QRs).
    """
    _require_segno()
    import segno
    qr = segno.make(text or "", micro=False)
    buf = io.BytesIO()
    qr.save(buf, kind="svg", scale=scale, border=border, xmldecl=False, svgclass=css_class)
    svg_text = buf.getvalue().decode("utf-8")

    if swiss_cross:
        svg_text = _inject_swiss_cross(svg_text)

    if badge == "google":
        svg_text = _inject_google_badge(svg_text)
    elif badge == "trustpilot":
        svg_text = _inject_trustpilot_badge(svg_text)

    return _svg_to_data_uri(svg_text)


def _get_default_company_bank_iban(company) -> str:
    """
    Best-effort fetch of the IBAN used for Swiss QR.
    Prefers Company.default_bank_account -> Account.account_number.
    """
    iban = ""
    if getattr(company, "default_bank_account", None):
        try:
            acc = frappe.get_doc("Account", company.default_bank_account)
            iban = (getattr(acc, "account_number", None) or "").strip()
        except Exception:
            pass
    if not iban and company.meta.has_field("custom_iban"):
        iban = (getattr(company, "custom_iban", None) or "").strip()
    return iban


# ---------------------------------------------------
# Whitelisted API for Print Formats
# ---------------------------------------------------
@frappe.whitelist()
def get_swiss_qr_data_uri(sales_invoice: str, amount=None, message: str = None) -> str:
    """
    Return Swiss QR-bill (SPC) as PNG data URI with embedded Swiss cross.
    NON reference; uses invoice name in 'Additional info' unless overridden.
    """
    if not sales_invoice:
        frappe.throw("sales_invoice is required")
    doc = frappe.get_doc("Sales Invoice", sales_invoice)
    if not frappe.has_permission("Sales Invoice", "read", doc=doc):
        frappe.throw("Not permitted")
    company = frappe.get_doc("Company", doc.company)
    iban = _get_default_company_bank_iban(company)
    if not iban:
        frappe.throw("No IBAN found: set Company Default Bank Account (Account.account_number) or provide a custom IBAN.")
    amt = float(amount) if amount is not None and str(amount) != "" else float(doc.grand_total or 0)
    msg = message or f"Invoice {doc.name}"
    payload = _build_spc_payload_non_reference(
        doc=doc,
        iban=iban,
        amount=amt,
        currency=(doc.currency or "CHF"),
        message=msg,
    )
    return _qr_png_data_uri_from_text(payload, scale=10, border=4)


@frappe.whitelist()
def get_url_qr_data_uri(url: str, label: str = None, scale: int = 2, border: int = 3, badge: str = None) -> str:
    """
    Return a generic URL QR as SVG data URI.
    badge: None | "google" | "trustpilot"
    """
    if not url:
        frappe.throw("url is required")
    _ = label
    try:
        scale = int(scale)
    except Exception:
        scale = 2
    try:
        border = int(border)
    except Exception:
        border = 3
    scale = max(1, min(scale, 12))
    border = max(0, min(border, 10))
    return _qr_svg_data_uri_from_text(
        url,
        scale=scale,
        border=border,
        css_class="qr-url",
        swiss_cross=False,
        badge=badge,
    )


@frappe.whitelist()
def get_review_links_for_invoice(sales_invoice: str):
    """
    Return review links for the invoice's company from Company.custom_review_links (child table).
    """
    if not sales_invoice:
        frappe.throw("sales_invoice is required")
    doc = frappe.get_doc("Sales Invoice", sales_invoice)
    if not frappe.has_permission("Sales Invoice", "read", doc=doc):
        frappe.throw("Not permitted")
    company = frappe.get_doc("Company", doc.company)
    if not company.meta.has_field("custom_review_links"):
        frappe.throw("Company is missing custom_review_links table field")
    rows = company.custom_review_links or []
    if not rows:
        frappe.throw("No review links configured in Company (Review Links table is empty)")
    out = []
    for r in rows:
        label = (getattr(r, "label", None) or "").strip()
        url = (getattr(r, "url", None) or "").strip()
        if not label or not url:
            continue
        l = label.lower()
        u = url.lower()
        badge = None
        if "google" in l or "google." in u or "g.co/" in u or "maps.app.goo.gl" in u:
            badge = "google"
        elif "trust" in l or "trustpilot." in u:
            badge = "trustpilot"
        out.append({
            "label": label,
            "url": url,
            "qr_data_uri": get_url_qr_data_uri(url, scale=2, border=3, badge=badge),
        })
    return out
