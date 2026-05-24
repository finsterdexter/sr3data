"""Parser for firearm + vehicle standard-accessory data.

The NSRCG `cyber.dat`/`vehicles.dat` files carry "standard accessories" as
free-text strings on each item — comma-separated lists like
"Smartlink, RC:(1)" for firearms or "Turbocharging 3(factored in),
Electronics Port w/Radio(Rating 3)" for vehicles. This module:

* tokenizes those strings paren-aware (so "Foo(1,2)" isn't split on the inner
  comma),
* normalizes name variants ("Gas Vent (2)" → "Gas Vent II"; "Smartlink-2" →
  "Smartlink II"),
* skips pure mechanical-stat tags (RC:(1), (1RC), etc.) and rules text
  (BF = Complex Action, Caseless, "uses HPist ranges", ...),
* resolves the remaining tokens against the gear / vehicle-modification
  catalog by name, and
* returns three buckets per source row: resolved (id matches), notes (free
  text we couldn't and shouldn't try to resolve), unresolved (looked like
  an accessory but no catalog match — these surface for the user to verify).

Design choices encoded here that came from project conversations:

* Smartlink standard accessories on firearms map to Internal mount (the SR3
  catalog only has External Smartlink entries; user wants those treated as
  the internal variant when bundled with a gun).
* Anything pre-listed on a vehicle is "standard and uncharged" regardless of
  whether the source text says "(factored in)".
* Recoil-compensation tags are skipped — they're derived stats, not catalog
  accessories.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable

# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

def split_paren_aware(text: str) -> list[str]:
    """Split a comma-separated list, ignoring commas inside parentheses.
    "Foo(1, 2), Bar" → ["Foo(1, 2)", "Bar"]."""
    if not text:
        return []
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in text:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            current.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


# ---------------------------------------------------------------------------
# Skip rules
# ---------------------------------------------------------------------------

_RC_PATTERNS = [
    re.compile(r"^RC\s*[:\(]\s*\(?\d+\)?\s*\)?$", re.I),     # RC:(1), RC(2), RC:1
    re.compile(r"^\(\s*\d+\s*RC\s*\)$", re.I),               # (1RC), (2 RC)
    re.compile(r"^\d+\s*RC$", re.I),                          # 1RC
]

# Rules-note tokens we explicitly keep as free-text notes rather than trying
# to resolve. Substrings (case-insensitive) — full token will be kept.
_NOTE_PATTERNS = [
    re.compile(r"=", re.I),                          # "BF = Complex Action"
    re.compile(r"\buses\b.*\branges\b", re.I),       # "LPist uses HPist ranges"
    re.compile(r"^complex action$", re.I),
    re.compile(r"^caseless$", re.I),
    re.compile(r"fires .* only", re.I),
    re.compile(r"flechettes", re.I),
]


def is_rc_tag(token: str) -> bool:
    return any(p.match(token) for p in _RC_PATTERNS)


def looks_like_rule_note(token: str) -> bool:
    return any(p.search(token) for p in _NOTE_PATTERNS)


# ---------------------------------------------------------------------------
# Roman numeral conversion (1↔I)
# ---------------------------------------------------------------------------

_ARABIC_TO_ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]


def arabic_to_roman(n: int) -> str:
    if 1 <= n < len(_ARABIC_TO_ROMAN):
        return _ARABIC_TO_ROMAN[n]
    return str(n)


# ---------------------------------------------------------------------------
# Parsed token shape
# ---------------------------------------------------------------------------

@dataclass
class ParsedToken:
    """One parsed accessory token (or note)."""
    raw: str
    # base_name with any (params) stripped + any -N suffix normalized
    canonical: str = ""
    # rating extracted from "(N)" / "Rating N" / "-N" / trailing Roman numeral
    rating: int | None = None
    # everything in parens that wasn't a Rating value
    paren_payload: str | None = None
    # "factored in" tag on the original token (vehicles)
    factored_in: bool = False


# ---------------------------------------------------------------------------
# Token normalization
# ---------------------------------------------------------------------------

# Match a trailing Roman numeral I-X at the end of a base name
_TRAILING_ROMAN = re.compile(r"\s+(IX|IV|V?I{1,3}|X)$", re.I)
# Match trailing "-N" (e.g., "Smartlink-2")
_TRAILING_DASH_N = re.compile(r"-(\d+)$")
# Match trailing " N" arabic (e.g., "Gas Vent 2"; rare after normalization)
_TRAILING_SPACE_N = re.compile(r"\s+(\d+)$")
# Match trailing "N" with no separator (e.g., "Smartlink2", "smartlink2") —
# only fires when the preceding character is a letter and the base name is
# long enough that a stray digit wouldn't be a model number ("M4", "AK47").
_TRAILING_GLUED_N = re.compile(r"([a-zA-Z]{4,})(\d+)$")
# Capture parens at the end of the token (handles nested parens via greedy)
_TRAILING_PAREN = re.compile(r"\s*\((.*)\)\s*$")


def _strip_factored_in(text: str) -> tuple[str, bool]:
    """Strip a "(factored in)" tag, returning (cleaned, was_factored_in)."""
    pattern = re.compile(r"\s*\(factored in\)\s*$", re.I)
    m = pattern.search(text)
    if m:
        return (text[: m.start()].strip(), True)
    return (text, False)


def _extract_rating_from_paren(payload: str) -> tuple[int | None, str | None]:
    """Try to pull a rating out of parenthetical text. Returns (rating, leftover)."""
    if payload is None:
        return (None, None)
    s = payload.strip()
    # "Rating N" / "rating N"
    m = re.match(r"^Rating\s+(\d+)\s*$", s, re.I)
    if m:
        return (int(m.group(1)), None)
    # Bare integer like "(2)"
    m = re.match(r"^(\d+)\s*$", s)
    if m:
        return (int(m.group(1)), None)
    return (None, s)


# Strips trailing recoil-compensation tags in any of the shapes the data uses:
#   "Forgrip -1 RC"   (bare),
#   "Gas Vent I(1RC)" (parenthesized, no space),
#   "Stock(1 RC)"     (parenthesized, space inside),
#   "Foo (+2 RC)"     (signed),
# Greedy enough to consume the wrapping parens when they're present.
_RC_TAIL = re.compile(r"\s*\(?\s*[-+]?\d+\s*RC\s*\)?\s*$", re.I)


def normalize_token(raw_token: str) -> ParsedToken:
    """Strip parens, dash-N, trailing Roman, "(factored in)" — return ParsedToken
    with `canonical` ready for catalog matching plus extracted rating/payload."""
    cleaned, factored = _strip_factored_in(raw_token)
    pt = ParsedToken(raw=raw_token, factored_in=factored)

    # Strip trailing recoil-compensation tags ("Forgrip -1 RC", "Stock(1RC)").
    # These are mechanical effects, not part of the canonical accessory name.
    cleaned = _RC_TAIL.sub("", cleaned).strip()

    # Pull trailing parens off (could carry a rating or a payload).
    m = _TRAILING_PAREN.search(cleaned)
    if m:
        payload = m.group(1)
        cleaned = cleaned[: m.start()].strip()
        rating, leftover = _extract_rating_from_paren(payload)
        if rating is not None:
            pt.rating = rating
        if leftover is not None:
            pt.paren_payload = leftover

    # "Smartlink-2" → "Smartlink II" + rating 2
    m = _TRAILING_DASH_N.search(cleaned)
    if m:
        n = int(m.group(1))
        if pt.rating is None:
            pt.rating = n
        cleaned = cleaned[: m.start()].strip()

    # "Gas Vent 2" — same idea
    m = _TRAILING_SPACE_N.search(cleaned)
    if m:
        n = int(m.group(1))
        if pt.rating is None:
            pt.rating = n
        cleaned = cleaned[: m.start()].strip()

    # "Smartlink2" — no separator. Conservative pattern (≥4 trailing letters)
    # avoids eating model numbers like "M4" or "AK47".
    m = _TRAILING_GLUED_N.search(cleaned)
    if m:
        base, n_str = m.group(1), m.group(2)
        n = int(n_str)
        if pt.rating is None:
            pt.rating = n
        cleaned = base.strip()

    # Capture trailing Roman if present, treat as rating
    m = _TRAILING_ROMAN.search(cleaned)
    if m:
        roman = m.group(1).upper()
        if roman in _ARABIC_TO_ROMAN:
            n = _ARABIC_TO_ROMAN.index(roman)
            if pt.rating is None:
                pt.rating = n
            cleaned = cleaned[: m.start()].strip()

    pt.canonical = cleaned.strip()
    return pt


# ---------------------------------------------------------------------------
# Catalog matching
# ---------------------------------------------------------------------------

@dataclass
class CatalogIndex:
    """Lookup index over the gear catalog. Lazily case-insensitive matching."""
    by_normalized_name: dict[str, int] = field(default_factory=dict)
    by_id: dict[int, dict] = field(default_factory=dict)

    @staticmethod
    def _norm(name: str) -> str:
        return re.sub(r"\s+", " ", name or "").strip().lower()

    def add(self, gear_id: int, name: str, mount: str | None, category: str | None):
        n = self._norm(name)
        # First-wins so the lowest-id duplicate (older book) doesn't get clobbered
        # by a later one. Pragmatic, matches how the GearDatabase loads.
        if n not in self.by_normalized_name:
            self.by_normalized_name[n] = gear_id
        self.by_id[gear_id] = {"name": name, "mount": mount, "category": category}

    def lookup(self, canonical: str, rating: int | None) -> int | None:
        """Try several name shapes to find a catalog match.

        SR3 catalog data is annoyingly inconsistent about how rating-N variants
        are spelled — firearms use Roman ("Gas Vent II"), vehicle modifications
        use bracketed Arabic ("ECCM [2]"), and a few use ", Level N" or just
        bare " N". This walks each shape until something hits."""
        if not canonical:
            return None
        # Direct hit
        gid = self.by_normalized_name.get(self._norm(canonical))
        if gid is not None and rating is None:
            return gid
        if rating is not None:
            roman = arabic_to_roman(rating)
            # Try every common rating-suffix shape, narrowest first.
            shapes = [
                f"{canonical} [{rating}]",        # Vehicle mods: "ECCM [2]"
                f"{canonical} {roman}",           # Roman: "Gas Vent II"
                f"{canonical} {rating}",          # Bare arabic: "Mini-Turret 2"
                f"{canonical}, Level [{rating}]", # "Amphibious Package, Level [2]"
                f"{canonical}, Level {roman}",
            ]
            for shape in shapes:
                gid = self.by_normalized_name.get(self._norm(shape))
                if gid:
                    return gid
            # Prefix fallback: any catalog name whose normalized form starts
            # with "<canonical>" AND somewhere contains the rating value.
            # Catches "Smartlink level II, External" matching "Smartlink"+2.
            target = self._norm(canonical)
            rating_token = f"[{rating}]"
            for needle, found in self.by_normalized_name.items():
                if needle.startswith(target) and (
                    rating_token in needle or
                    f" {roman.lower()}" in needle or
                    needle.endswith(f" {rating}") or
                    needle.startswith(target + " level " + roman.lower())
                ):
                    return found
        # No-rating substring fallback (only when caller didn't pin a rating).
        if rating is None:
            target = self._norm(canonical)
            candidates = [(name, gid) for name, gid in self.by_normalized_name.items()
                          if name.startswith(target)]
            if len(candidates) == 1:
                return candidates[0][1]
            for name, gid in candidates:
                if name == target:
                    return gid
            # Multiple candidates, no exact name match — prefer the lowest-rated
            # variant. The data uses "[N]" or " N" for rating tokens; pick the
            # one whose trailing rating sorts first.
            def _rating_token(n: str) -> tuple:
                m = re.search(r"\[(\d+)\]\s*$", n) or re.search(r"\b(\d+)\s*$", n)
                return (0, int(m.group(1))) if m else (1, 0)
            ranked = sorted(candidates, key=lambda c: _rating_token(c[0]))
            if ranked:
                return ranked[0][1]
        return None


# ---------------------------------------------------------------------------
# Name-specific overrides
# ---------------------------------------------------------------------------

# Names that need a specific catalog target or special mount placement that
# isn't visible from the catalog row alone. Source names verified against:
#   * SR3 BBB Street Gear, p.282 (firearm accessories table)
#   * Cannon Companion p.34–35 (recoil + imaging + target-designator tables)
#   * Cannon Companion p.80–83 (Customization & Weapon Modifications)
# Keys are lowercase canonical (after normalize_token); values are
# (catalog_name, override_mount_or_None).
_NAME_OVERRIDES = {
    # Bare "Smartlink" on a firearm = the gun-side internal smartgun hardware
    # (SR3 BBB p.282: "Smartgun, internal"). The internal rows are supplied by
    # data/mm_extra_firearm_accessories.json since cyber.dat / GEAR.DAT only
    # carry the External variants.
    "smartlink":          ("Smartlink level I, Internal",  "int"),
    "smartlink ii":       ("Smartlink level II, Internal", "int"),

    # Sound suppressor — the SR3 BBB catalog row has a typo ("Suppresser"),
    # but it's the canonical SR3 BBB p.282 item.
    "sound suppressor":   ("Sound Suppresser", None),
    "sound suppresser":   ("Sound Suppresser", None),

    # Cannon Companion p.34–35 recoil accessories
    "foregrip":           ("Fore Grip", "Under"),
    "forgrip":            ("Fore Grip", "Under"),
    "fore grip":          ("Fore Grip", "Under"),
    "underbarrel weight": ("Under Barrel Weight", "Under"),
    "under barrel weight": ("Under Barrel Weight", "Under"),
    "max-gyro":           ("Gyro Mount, Max-Gyro", "Under"),
    "max gyro":           ("Gyro Mount, Max-Gyro", "Under"),
    "hip pad bracing":    ("Hip Pad Bracing System", None),
    "hip pad bracing system": ("Hip Pad Bracing System", None),

    # Stock variants — CC p.35 has a generic Stock (Rigid/Folding) item that
    # covers gun-mounted folding/retractable/fixed-rigid stocks. "Detachable
    # stock" is something different (a stock that comes off entirely) and is
    # NOT this catalog row — those land in firearm_notes (see _FEATURE_NOTE_NAMES).
    "folding stock":      ("Stock (Rigid/Folding)", None),
    "retractable stock":  ("Stock (Rigid/Folding)", None),
    "fold stock":         ("Stock (Rigid/Folding)", None),
    "fold. stock":        ("Stock (Rigid/Folding)", None),
    "folding pistol grip stock": ("Stock (Rigid/Folding)", None),
    "stock":              ("Stock (Rigid/Folding)", None),

    # Bipods — SR3 BBB p.282 has a generic Bipod entry. CC has the same.
    "bipod":              ("Bipod", "Under"),
    "folding bipod":      ("Bipod", "Under"),
    "removable bipod":    ("Bipod", "Under"),

    # Short form: bare "suppressor" → SR3 BBB Sound Suppresser (typo'd in
    # catalog). Distinct from Silencer (different stat block in BBB p.282).
    "suppressor":         ("Sound Suppresser", "Barrel"),
    "supressor":          ("Sound Suppresser", "Barrel"),

    # Additional stock spellings
    "collapsible stock":  ("Stock (Rigid/Folding)", None),

    # Standalone scope features
    "thermographic":      ("Imaging Scope: Thermographic", "Top"),

    # Generic grenade-launcher attachment — catalog row is the underbarrel one.
    "grenade launcher":   ("Generic Under-Barrel    (GrLn)", "Under"),

    # Scopes — Cannon Companion Imaging Systems (p.35) catalogues these
    # explicitly as Imaging Scope: Mag:N / Low-Light / Thermographic.
    "scope":              ("Imaging Scope: Mag:1", "Top"),
    "scope 1":            ("Imaging Scope: Mag:1", "Top"),
    "scope 2":            ("Imaging Scope: Mag:2", "Top"),
    "scope 3":            ("Imaging Scope: Mag:3", "Top"),
    "level 1 scope":      ("Imaging Scope: Mag:1", "Top"),
    "level 2 scope":      ("Imaging Scope: Mag:2", "Top"),
    "level 3 scope":      ("Imaging Scope: Mag:3", "Top"),
    "low-light scope":    ("Imaging Scope: Low-Light", "Top"),
    "thermographic scope": ("Imaging Scope: Thermographic", "Top"),
}

# Names that are descriptive features rather than catalog accessories — the
# parser routes these to firearm_notes instead of leaving them in
# mm_parse_unresolved. Confirmed not present as standalone items in any of
# the four SR3 sourcebook tables; the data uses them as feature labels on
# specific weapons (e.g., "Desert Eagle: heavy barrel").
_FEATURE_NOTE_NAMES = {
    "barrel porting", "heavy barrel", "threaded barrel",
    "picatinny rail", "underbarrel",
    "ambidextrous safety", "ambidextrous safety & slide-stop levers",
    "ambidextrous navy trigger group", "ghost ring sights",
    "extra clip held in slot", "holds extra clip in stock",
    "no internal acc. allowed", "no internal acc allowed",
    "flash supressor", "flash suppressor",
    "x.75armor mod", "0.75armor mod",
    "rifle ammo", "drum feed",
    "dwarf moded", "parkerized", "titanium alloy",
    "emboss/engrave",
    "fixed stock",  # not a separate item — describes a stock that doesn't fold
    "commander slide", "stainless steel slide", "stainless frame/blued slide",
    "stainless steel", "all stainless", "stainless steel finish",
    "wood grips", "two-tone", "bobtail mod",
    "many finishes at cost", "2 finishes at cost",
    "nickel finish", "bright nickel finish", "blued finish",
    "chrome finish", "gold finish", "gold plating",
    "duo-tone (stainless & black)",
    "revolver loading", "double recoil", "double uncompensated recoil",
    "special recoil",
    # Sights / accessories that aren't separate catalog rows
    "adjustable rear sight", "detachable 1\" scope ring",
    "3-round burst trigger group", "navy trigger group", "trigger group",
    "personalized navy trigger group",
    # Detachable stock — distinct from the "Stock (Rigid/Folding)" catalog
    # row. SR3 doesn't carry a separate catalog item for a removable stock;
    # treat as a free-text note on the firearm.
    "detachable stock", "detach. stock", "detach stock",
    # Placeholder / parse-artifact tokens
    "accessory", "ray.000", "or black",
    "foregrip and stock", "stock and forgrip each give 1 recoil comp",
}

# Token patterns (substring, case-insensitive) the parser should treat as
# pure rules-text and route to notes. Catches things like
# "can fire .38 Mag at 5M and (LPist)" without listing every variant.
_NOTE_SUBSTRINGS = (
    "can fire", "can use", "uses ", "can convert", "can not",
    "+1 recoil", "+2 recoil", "+3 recoil", "+4 recoil", "+5 recoil",
    "uncompensated", "complex to chamber",
    "simple each fire", "simple to switch", "pump action",
    "can't take", "cannot take", "cant take",
    "any range over short", "no recoil penalties",
    "mag:(",  # magazine size descriptions
    "1RC)", "2RC)", "3RC)", "4RC)",  # bracketed RC tags
)


def apply_name_override(canonical: str, rating: int | None) -> tuple[str, str | None] | None:
    """Returns (catalog_name, override_mount) if a name has a project-specific
    redirect, otherwise None."""
    if not canonical:
        return None
    # Try canonical + rating-Roman
    key_with_rating = canonical.lower()
    if rating is not None:
        key_with_rating = f"{canonical} {arabic_to_roman(rating)}".strip().lower()
    if key_with_rating in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[key_with_rating]
    # Try canonical only
    if canonical.lower() in _NAME_OVERRIDES:
        return _NAME_OVERRIDES[canonical.lower()]
    return None


# ---------------------------------------------------------------------------
# High-level: parse an accessories string
# ---------------------------------------------------------------------------

@dataclass
class ParsedAccessories:
    """Per-firearm/vehicle output: matched catalog ids + free-text notes
    + unresolved tokens for human verification."""
    resolved: list[tuple[int, str | None, str, int | None, str | None]] = field(default_factory=list)
    # (catalog_id, mount_location, raw_text, rating, paren_payload)
    notes: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)


def parse_firearm_accessories(text: str | None, catalog: CatalogIndex) -> ParsedAccessories:
    out = ParsedAccessories()
    if not text or text.strip().lower() == "none":
        return out
    for raw in split_paren_aware(text):
        if is_rc_tag(raw):
            continue
        if looks_like_rule_note(raw):
            out.notes.append(raw)
            continue
        # Substring patterns that clearly mark the token as rules-text rather
        # than a catalog item.
        lower = raw.lower()
        if any(s in lower for s in _NOTE_SUBSTRINGS):
            out.notes.append(raw)
            continue
        pt = normalize_token(raw)
        if not pt.canonical:
            out.notes.append(raw)
            continue

        # Project-specific name overrides take precedence over catalog lookup.
        override = apply_name_override(pt.canonical, pt.rating)
        if override is not None:
            catalog_name, mount = override
            gid = catalog.by_normalized_name.get(CatalogIndex._norm(catalog_name))
            if gid is not None:
                out.resolved.append((gid, mount, raw, pt.rating, pt.paren_payload))
                continue

        gid = catalog.lookup(pt.canonical, pt.rating)
        if gid is not None:
            mount = catalog.by_id[gid].get("mount")
            if mount in ("-", "NA"):
                mount = None
            out.resolved.append((gid, mount, raw, pt.rating, pt.paren_payload))
            continue

        # Recognized feature/cosmetic strings → notes (not unresolved).
        if pt.canonical.lower() in _FEATURE_NOTE_NAMES:
            out.notes.append(raw)
            continue
        # Single-char garbage placeholders.
        if len(pt.canonical) <= 1:
            out.notes.append(raw)
            continue
        out.unresolved.append(raw)
    return out


# ---------------------------------------------------------------------------
# Vehicle parsing — same core, with structured-payload parsing on top
# ---------------------------------------------------------------------------

# Match the "External Fixed Firmpoint" / "Internal Retractable Hardpoint"
# family of vehicle weapon-mount descriptors. Capture group order: placement,
# configuration, mount_type.
_VEHICLE_MOUNT_RE = re.compile(
    r"^(External|Internal|Concealed)?\s*"
    r"(Fixed|Flexible|Retractable|Visible|Heavy)?\s*"
    r"(Firmpoint|Hardpoint|Mount)\b(.*)$",
    re.I,
)


def parse_vehicle_mount_descriptor(canonical: str, paren_payload: str | None) -> dict | None:
    """Try to parse vehicle weapon-mount entries like
    "External Fixed Firmpoint(1 CF Ammo Bin)" into structured fields."""
    m = _VEHICLE_MOUNT_RE.match(canonical)
    if not m:
        return None
    placement, configuration, mount_type, trailing = m.groups()
    info: dict = {
        "placement": (placement or "").capitalize() or None,
        "configuration": (configuration or "").capitalize() or None,
        "mount_type": mount_type.capitalize() if mount_type else None,
    }
    if paren_payload:
        info["payload"] = paren_payload.strip()
    if trailing and trailing.strip():
        info["trailing"] = trailing.strip()
    return info


def parse_vehicle_standard_mods(text: str | None, catalog: CatalogIndex) -> ParsedAccessories:
    out = ParsedAccessories()
    if not text:
        return out
    for raw in split_paren_aware(text):
        if is_rc_tag(raw):
            continue
        if looks_like_rule_note(raw):
            out.notes.append(raw)
            continue
        pt = normalize_token(raw)
        if not pt.canonical:
            out.notes.append(raw)
            continue

        # Try vehicle-mount structured parsing first.
        mount_info = parse_vehicle_mount_descriptor(pt.canonical, pt.paren_payload)
        if mount_info is not None:
            # Vehicle weapon-mount mods don't always have a clean catalog name;
            # we still try the lookup, but store the structured payload so the
            # consumer can render the configuration even without a catalog id.
            gid = catalog.lookup(pt.canonical, pt.rating)
            payload = json.dumps(mount_info, sort_keys=True)
            if gid is not None:
                out.resolved.append((gid, None, raw, pt.rating, payload))
            else:
                # Structured mount data but no exact name match — keep as
                # unresolved so the user can inspect, but the structured
                # payload is recoverable from the raw text.
                out.unresolved.append(raw)
            continue

        gid = catalog.lookup(pt.canonical, pt.rating)
        if gid is not None:
            out.resolved.append((gid, None, raw, pt.rating, pt.paren_payload))
        else:
            out.unresolved.append(raw)
    return out


# ---------------------------------------------------------------------------
# Helpers for building the catalog index from a sqlite cursor
# ---------------------------------------------------------------------------

def build_firearm_accessory_catalog(cursor) -> CatalogIndex:
    """Index every "Firearm and weapon accessories" gear row by name."""
    idx = CatalogIndex()
    rows = cursor.execute("""
        SELECT g.id, g.name, a.mount, g.category_tree
        FROM gear g LEFT JOIN gear_accessories a ON a.gear_id = g.id
        WHERE g.category_tree LIKE 'Firearm and weapon accessories%'
    """).fetchall()
    for gid, name, mount, category in rows:
        idx.add(gid, name, mount, category)
    return idx


def build_vehicle_mod_catalog(cursor) -> CatalogIndex:
    """Index every vehicle modification row by name (type 23). type_id is
    stored as TEXT in the schema (legacy quirk of the dat parser), so the
    filter compares the string form."""
    idx = CatalogIndex()
    rows = cursor.execute("""
        SELECT id, name, NULL, category_tree
        FROM vehicles
        WHERE type_id = '23'
    """).fetchall()
    for vid, name, _, category in rows:
        idx.add(vid, name, None, category)
    return idx
