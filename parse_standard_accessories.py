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
# V?I{1,3} catches I-III and VI-VIII but misses V alone; added |V explicitly.
_TRAILING_ROMAN = re.compile(r"\s+(IX|IV|V?I{1,3}|V|X)$", re.I)
# Match trailing "-N" (e.g., "Smartlink-2")
_TRAILING_DASH_N = re.compile(r"-(\d+)$")
# Match trailing " N" arabic (e.g., "Gas Vent 2"; rare after normalization)
_TRAILING_SPACE_N = re.compile(r"\s+(\d+)$")
# Match trailing "N" with no separator (e.g., "Smartlink2", "smartlink2") —
# only fires when the preceding character is a letter and the base name is
# long enough that a stray digit wouldn't be a model number ("M4", "AK47").
# Lowered to 3 so 3-letter acronyms like ECD, ECM, ED get their ratings
# extracted correctly.
_TRAILING_GLUED_N = re.compile(r"([a-zA-Z]{3,})(\d+)$")
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
        # Singular fallback: source data often pluralizes ("External Hardpoints")
        # where the catalog stores the singular ("External Hardpoint"). Retry
        # after stripping a single trailing 's'/'es' from the canonical.
        if canonical.endswith("s") and len(canonical) > 2:
            singular = canonical[:-2] if canonical.endswith("es") else canonical[:-1]
            gid = self.by_normalized_name.get(self._norm(singular))
            if gid is not None:
                return gid
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
    "integral smartlink":   ("Smartlink level I, Internal",  "int"),
    "integral smartlink ii": ("Smartlink level II, Internal", "int"),

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
    # covers gun-mounted folding/retractable/fixed-rigid/telescoping stocks.
    # "Detachable stock" is something different (a stock that comes off
    # entirely) and is NOT this catalog row — those land in firearm_notes
    # (see _FEATURE_NOTE_NAMES).
    "folding stock":      ("Stock (Rigid/Folding)", None),
    "retractable stock":  ("Stock (Rigid/Folding)", None),
    "retr. stock":        ("Stock (Rigid/Folding)", None),
    "retr stock":         ("Stock (Rigid/Folding)", None),
    "telescoping stock":  ("Stock (Rigid/Folding)", None),
    "tele. stock":        ("Stock (Rigid/Folding)", None),
    "tele stock":         ("Stock (Rigid/Folding)", None),
    "fold stock":         ("Stock (Rigid/Folding)", None),
    "fold. stock":        ("Stock (Rigid/Folding)", None),
    "folding pistol grip stock": ("Stock (Rigid/Folding)", None),
    "pistol grip stock":  ("Stock (Rigid/Folding)", None),
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

    # Scopes — Cannon Companion Imaging Systems (p.35) catalogues these
    # explicitly as Imaging Scope: Mag:N / Low-Light / Thermographic.
    "scope":              ("Imaging Scope: Mag:1", "Top"),
    "scope 1":            ("Imaging Scope: Mag:1", "Top"),
    "scope 2":            ("Imaging Scope: Mag:2", "Top"),
    "scope 3":            ("Imaging Scope: Mag:3", "Top"),
    "level 1 scope":      ("Imaging Scope: Mag:1", "Top"),
    "level 2 scope":      ("Imaging Scope: Mag:2", "Top"),
    "level 3 scope":      ("Imaging Scope: Mag:3", "Top"),
    "imaging scope":      ("Imaging Scope: Mag:1", "Top"),
    "optical imaging scope": ("Imaging Scope: Mag:1", "Top"),
    "magnification":      ("Imaging Scope: Mag:1", "Top"),
    "magnifcation":       ("Imaging Scope: Mag:1", "Top"),
    "low-light scope":    ("Imaging Scope: Low-Light", "Top"),
    "low-light":          ("Imaging Scope: Low-Light", "Top"),
    "thermographic scope": ("Imaging Scope: Thermographic", "Top"),
    "high-power laser sight": ("High Power Laser Sight", "Top"),
    "high power laser sight": ("High Power Laser Sight", "Top"),

    # Recoil compensators
    "hip brace":          ("Hip Pad Bracing System", None),
    "hip pad brace":      ("Hip Pad Bracing System", None),

    # Single-word / typo aliases for catalog items
    "lasersight":         ("Laser Sight", "Top"),
    "gun camera":         ("Gun Cam", "Top"),
    "flashlight":         ("Flash Light (Standard)", "Top"),
    "gasvent":            ("Gas Vent I", "Barrel"),  # rating-less variant -> Gas Vent I default

    # "Integral" prefix — the catalog item is the same, just installed
    # internally on the gun rather than mounted externally.
    "integral silencer":  ("Silencer", "Barrel"),
    "integral gas vent":  ("Gas Vent I", "Barrel"),
    "removable gas vent": ("Gas Vent I", "Barrel"),

    # Tripod variants
    "fold-out tripod":    ("Tripod", "Under"),
    "folding tripod":     ("Tripod", "Under"),

    # Cannon Companion safeties (cc.33)
    "biometric safety":   ("Biometric Safety", None),
    "advanced biometric safety": ("Biometric Safety", None),
}

# Vehicle-specific name overrides. Keys are lowercase canonical (after
# normalize_token + _VEHICLE_TYPO_FIXUPS); values are (catalog_name, None)
# because vehicle mods don't have mount positions like firearms do.
_VEHICLE_NAME_OVERRIDES = {
    # Improved Signature aliases — source data abbreviates as "Improved Sig"
    "improved sig": "Improved Signature",
    "improved signature": "Improved Signature",

    # Customized Engine aliases
    "customised engine": "Engine Customization",
    "customized engine": "Engine Customization",

    # Amphibious aliases
    "amphibious ops package": "Amphibious Operation",
    "amphibious operation package": "Amphibious Operation",

    # Power Amplifier alias (source data pluralizes)
    "power amplifiers": "Power Amplifier",

    # Contingency Maneuver Controls alias (source data expands abbreviation)
    "contingency maneuver controls": "Cont. Manu. Contr.",

    # Remote-Control / Rigger aliases
    "remote control encryption unit": "Remote-Control Encryption",
    "remote-control gear": "Remote-Control Interface",
    "rigger control": "Rigger Adaptation",
    "rigger controls": "Rigger Adaptation",
    "rigger interface": "Rigger Adaptation",

    # Launch Control aliases
    "medium launch system": "Launch Control System",
    "missile launch system": "Launch Control System",

    # Anti-Theft alias
    "antitheft": "Anti-Theft System",

    # Environmental Adaptation alias
    "environmental adaptation": "Artic/Desert Adaptation Kit",

    # Life Support typo
    "life suport": "Life Support",

    # Flotation / Floatation
    "floatation package": "Flotation Package",

    # Satellite Link aliases
    "satt uplink": "Satellite Link",
    "sat uplink": "Satellite Link",
    "satellite uplink": "Satellite Link",

    # Missile typo
    "external missle mount": "External Missile Mount",
    "internal missle mount": "Internal Missile Mount",

    # Smoke Generator — source data uses this name; .dat has "Vehicle Smoke Projector"
    "smoke generator": "Smoke Generator",

    # Drone Rack aliases
    "external drone racks": "Drone Rack",

    # Electronics Bay typo
    "electroncs bay": "Electronics Bay",
    "electronics bay": "Electronics Bay",
}


def apply_vehicle_name_override(canonical: str, rating: int | None) -> str | None:
    """Returns catalog_name if a vehicle-mod name has a project-specific
    redirect, otherwise None."""
    if not canonical:
        return None
    key_with_rating = canonical.lower()
    if rating is not None:
        key_with_rating = f"{canonical} {arabic_to_roman(rating)}".strip().lower()
    if key_with_rating in _VEHICLE_NAME_OVERRIDES:
        return _VEHICLE_NAME_OVERRIDES[key_with_rating]
    if canonical.lower() in _VEHICLE_NAME_OVERRIDES:
        return _VEHICLE_NAME_OVERRIDES[canonical.lower()]
    return None


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
    # Grenade launchers etc. — these are weapons, not accessories; they
    # don't have catalog rows under "Firearm and weapon accessories".
    "grenade launcher", "grenade launcher 6", "grenade launcher 8",
    "20mm atg grenade launcher", "hand-held grln",
    "anti air missile launcher", "anti-air missile launcher",
    # No-stock / receiver-cap variants (gun has no stock)
    "no stock", "no stock (receiver cap)",
    # Picatinny / M1913 rails (top mount accessory rail, not a separate item)
    "m1913 rail", "picatinny rail", "integral m1913 picatinny rail",
    "integral picatinny rail",
    # Cosmetic colors / engravings
    "gray", "green", "black", "ingraving", "engraving",
    "fine walnut grips", "pistol grips",
    # Generic feature descriptions
    "optional heavy barrel", "breakdown", "concealed hammer",
    "double action", "drum fed", "drum feed",
    "flash hider", "level 0 scope",
    "internal comp 2", "internal comp", "comp 1", "comp 2",
    "1 point recoil comp",
    # Mag-related (clip capacity descriptions, not separate items)
    "holds extra clip(4) in stock", "holds extra clip(5) in stock",
    "holds extra clip in stock",
    # Scope-mount feature notes — these aren't separate items; the gun has
    # an integrated mount that other scopes can attach to.
    "scope mount", "includes scope mount", "forward scope mount",
    "m1a scope mount",
    # Ambient feature notes
    "includes bipod", "breaks to 2 parts",
    "heavy pistol ranges", "half ranges", "half taser range",
    "heavy weapon recoil applies", "use taser range",
    "no top mount", "no mounts for top", "no barrel mount",
    "9m if supersonic", "5l when using subsonic ammunition",
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
    # -----------------------------------------------------------------------
    # Vehicle body-type / configuration descriptors (not separately-installable)
    # -----------------------------------------------------------------------
    "pickup", "flatbed", "closed bed", "covered bed", "camper",
    "minibus", "minivan", "rv", "rally car",
    # Fuel / power-plant descriptors
    "methane", "gasoline", "electric", "diesel engine", "tdi engine",
    # Vehicle capability descriptors
    "vtol", "this model is a dirigible", "streamlined design",
    "alternate stats while in ground mode",
    "no offroad speed change",
    # Interior / cargo descriptors
    "side door", "back ramp", "side doors and back ramp",
    "bar", "kitchen", "shop",
    "weapon rack", "tac comm",
    "no toys", "base model",
    # Seat / living-amenity descriptors
    "basic living amenities", "improved living amenities",
    "high living amenities", "partial basic living amenities",
    "partial high living amenities",
    "oversized bucket seats", "comfy bucket seats",
    "mahogany desks", "person couch",
    "comm/entertainment suite", "satt matrix uplink",
    # Cargo / payload descriptors (not separate catalog items)
    "cargo space for 12 men and a manned apc",
    "internal storage for 2 cars or 4 steel lynx-sized vehicles or drones",
    "drone rearmament and recharge facility",
    "load hydraulic flatbed and winch designed to cary up to rating 6 bod vehicle",
    "provisions for 2 drone racks or 4cf aa missiles",
    "level unknown",
    # Speed / performance descriptors
    "speed 250 when loaded", "speed and accel depend on windspeed",
    "3km/l economy", "500l fuel capacity",
    # Module descriptors
    "transport module adds 10cf ad costs 750",
    "modula design comes with hardtop module",
    "any module adds 1/1 to handling",
    "optional crash cage and datajack port",
    "optional rigger adaptation", "optional rigger control gear",
    "optional suncell and roof rack",
    "optional underwater package includes enviroseal",
    "optional missile pods", "optional pintle mount for pilot",
    "optional roof rack and roll bars",
    "optional monofilament reels and multicore fibre controls",
    "datajack link",
    # Weapon / payload on mounts (weapon itself, not the mount)
    "vindicator minigun",
    "4 automatic grenade launchers", "20 naga ap mines",
    "2 torpedo tubes", "4 torpedo tubes", "6 torpedo tubes",
    "24 torpedoes", "36 torpedoes",
    "medium remote turret(rotary autocannon)",
    "light naval gun", "victory autocannon",
    # Camera / sensor descriptors
    "camera", "micro-camcorder", "trideo recorder",
    "chem sniffer 6", "mads 6", "universal receiver 4",
    # Lone Star descriptor
    "lone star",
    # Carrier / ship descriptors
    "aircraft facilities", "flight deck",
    # Capacity descriptors
    "6cf reserved for more electronics and 4cf for ammo",
    "4cf aa missiles", "4cf ag missiles", "4cf air-drop ap mines",
    "8cf ag rockets",
    # Misc descriptors
    "carries 1 patient 1 medtech and a pilot",
    "actual cost: 9 billion nuyen",
    # Life raft
    "10-man life raft",
    # Autosofts
    "autosoft: demolitions 3", "autosoft: electronic warfare 5",
    # Smoke-projector charges
    "fog oil(5 cf)", "graphite smoke(1 cf)",
    # Electronics-port contents (descriptive, not separate catalog items)
    "electronics port w/radio",
    "electronics port w/personal com unit",
    "electronics port w/sat uplink",
    "electronics port w/satelite uplink",
    "elctronics port w/radio",
    # Handholds
    "6 handholds",
    # Folding seats quantity
    "3 folding seats",
    # Aisle/seat layout descriptors
    "4x2-aisle-2 bucket seats",
    # Amenities quantity
    "500 improved amenities",
    # Missile mount with ordnance weight
    "6 missile mounts (total ordinance weight 1800 kg)",
    # Andrews system
    "4 remote medium turrets w/andrews system",
    # Turret + weapon combos
    "medium remote turret (light naval gun w/500 rds in 16 cf ammo bin)",
    "medium remote turret (victory autocannon w/ 500 rnds in 2 cf ammo bin)",
    "medium remote turret (victory autocannon w/2000 rds in 13 cf ammo bin)",
    "medium remote turret(10 cf ammo bin)",
    "remote mini-turret w/ultimax mmg",
    "remote micro-turret(1 cf ammo bin)",
    "remote mini-turret(1 cf ammo bin)",
    "small remote turret (2 cf ammo bin)",
    "small remote turret(2 cf ammo bin)",
    "external fixed hardpoint w/mmg(w/gasvent-iii and 500 rds. ammo)",
    "external fixed hardpoint w/mmg",
    # ECM/ECCM military variant
    "ecm/eccm military",
    # Additional electronics ports
    "4 additional electronics ports", "additional electronics ports",
    # Rocket mount / firmpoint combos with wing placement
    "4 external rocket mounts and 1 fixed firmpoint on each wing",
    "2 medium remote turrets (8 medium internal missle mounts)",
    "2 small remote turrets",
    "4 remote micro-turrets(2xtwin lmg)",
    "16 heavy internal missile mounts", "24 heavy internal missile mounts",
}

# Token patterns (substring, case-insensitive) the parser should treat as
# pure rules-text and route to notes. Catches things like
# "can fire .38 Mag at 5M and (LPist)" without listing every variant.
_NOTE_SUBSTRINGS = (
    "can fire", "can use", "uses ", "use ", "using ", "used ",
    "can convert", "can not", "cannot fire", "cant fire",
    "fires ", "fired ",
    "+1 recoil", "+2 recoil", "+3 recoil", "+4 recoil", "+5 recoil",
    "uncompensated", "complex to chamber",
    "simple each fire", "simple to switch", "simple to ", "simple action",
    "pump action",
    "can't take", "cannot take", "cant take",
    "any range over short", "no recoil penalties",
    "if subsonic", "if supersonic", "if steel shot",
    "if used", "if a smart",
    "suppression modifier", "suppression mod",
    "stun wears off", "power decrease", "x0.5 armor",
    "see rules", "see p.", "notes -",
    "+1 conceal", "+2 conceal", "-1 conceal", "-2 conceal", "-3 conceal",
    "for needles", "for darts",
    "double recoil", "double uncompensated",
    "tn-1", "tn +1",  # red-dot/reflex TN modifiers that travel without a separate catalog row
    "mag:(",
    "1rc)", "2rc)", "3rc)", "4rc)",
    "rnd mag", "rnd magazine",
    "armor piercing bullet", "armor mod",
    "telcom unit", "battery case",
    "underbarrel", "ub gr",
    # Vehicle descriptor substrings (not separate catalog items)
    " uses ", " can fire ", " can use ", " can convert ", " can not ",
    "cannot fire", "cant fire", " fires ", " fired ",
    "if subsonic", "if supersonic", "if steel shot", "if used", "if a smart",
    "see rules", "see p.", "notes -",
    "holds extra clip", "holds extra clip in stock",
    "speed 250 when loaded", "speed and accel depend on windspeed",
    "km/l economy", "l fuel capacity",
    "cf reserved for", "cf for ammo",
    "cf aa missiles", "cf ag missiles", "cf air-drop ap mines", "cf ag rockets",
    "partial basic living", "partial high living",
    "basic living amenities", "improved living amenities", "high living amenities",
    "man-hours", "man-hrs", "man hours",
    "patient", "medtech", "medical treatment",
    "drone rack", "drone racks",
    "torpedo tubes", "torpedoes",
    "naga ap mines",
    "automatic grenade launchers",
    "rotary autocannon",
    "level unknown",
    "actual cost:",
    "transport module adds", "modula design comes with",
    "any module adds",
    "optional crash cage", "optional rigger", "optional suncell",
    "optional underwater package", "optional missile pods",
    "optional pintle mount", "optional roof rack",
    "optional monofilament reels",
    "datajack link",
    "cargo space for", "internal storage for",
    "load hydraulic flatbed",
    "provisions for",
    "alternate stats while in ground mode",
    "no offroad speed change",
    "this model is a dirigible",
    "streamlined design",
    "rally car",
    "aircraft facilities", "flight deck",
    "lone star",
    "camera", "micro-camcorder", "trideo recorder",
    "chem sniffer", "mads ", "universal receiver",
    "vindicator minigun",
    "satt matrix uplink", "comm/entertainment suite",
    "bar", "kitchen", "weapon rack", "tac comm",
    "shop", "no toys", "base model",
    "side door", "back ramp", "side doors and back ramp",
    "mahogany desks", "person couch", "oversized bucket seats", "comfy bucket seats",
    "closed bed", "covered bed", "camper", "minibus", "minivan", "rv",
    "pickup", "flatbed",
    "methane", "gasoline", "diesel engine", "tdi engine",
    "vtol",
    "packages i-iii", "packages i-iii",
    "life raft",
    "fog oil", "graphite smoke",
    "autosoft:", "demolitions 3", "electronic warfare 5",
    "w/radio", "w/personal com unit", "w/sat uplink", "w/satelite uplink",
    "handholds", "folding seats",
    "aisle-2 bucket seats", "improved amenities",
    "missile mounts (total ordinance weight",
    "w/andrews system",
    "w/500 rds", "w/ 500 rnds", "w/2000 rds", "w/gasvent-iii",
    "heavy internal missile mounts",
    "external rocket mounts and 1 fixed firmpoint",
    "2xtwin lmg",
    "4 remote micro-turrets",
    "2 small remote turrets",
    "2 medium remote turrets",
    "medium remote turret (light naval gun", "medium remote turret (victory autocannon",
    "remote mini-turret w/ultimax",
    "remote micro-turret(1 cf ammo bin)", "remote mini-turret(1 cf ammo bin)",
    "small remote turret (2 cf ammo bin)", "small remote turret(2 cf ammo bin)",
    "external fixed hardpoint w/mmg",
    "ecm/eccm military",
    "additional electronics ports",
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
    """Resolve a comma-separated accessories string into catalog ids, notes,
    and unresolved tokens.

    Order of checks: pure mechanical-stat tags (RC) are skipped silently;
    obvious rules-text (BF = …, "uses … ranges", …) becomes a note. Everything
    else gets normalized then run through alias map → catalog lookup. Only
    when both lookups miss do we consult the broader feature-name + rules-
    substring catch-alls — those are last resort so we don't accidentally
    route a token like "Underbarrel Weight" to notes just because it contains
    the substring "underbarrel".
    """
    out = ParsedAccessories()
    if not text or text.strip().lower() == "none":
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

        # No catalog hit. Recognized feature-note name → notes.
        if pt.canonical.lower() in _FEATURE_NOTE_NAMES:
            out.notes.append(raw)
            continue
        # Looks like rules text — also notes.
        lower = raw.lower()
        if any(s in lower for s in _NOTE_SUBSTRINGS):
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


_VEHICLE_LEADING_COUNT = re.compile(r"^(\d+)\s+(.+)$")
_VEHICLE_TYPO_FIXUPS = {
    "externeal": "External",
    "satt uplink": "Satellite Uplink",
    "sat uplink": "Satellite Uplink",
    "satelite uplink": "Satellite Uplink",
    "rigger adaption": "Rigger Adaptation",
    "remote control interface": "Remote-Control Interface",  # alternate spelling
    "autosoft interpretation system": "Autosoft Interpreter",
    "ram": "Radar Absorbent Materials",
    # Improved Signature abbreviation
    "improved sig": "Improved Signature",
    # Missile typo
    "missle": "Missile",
    # EnviroSeal typo
    "enviroseal": "EnviroSeal",
    # Floatation → Flotation
    "floatation": "Flotation",
    # Abosorbent → Absorbent
    "abosorbent": "Absorbent",
    # Customised → Customized
    "customised": "Customized",
    # Life Suport → Life Support
    "life suport": "Life Support",
    # Electroncs → Electronics
    "electroncs": "Electronics",
    # Contingency Maneuver Controls expansion
    "contingency maneuver controls": "Cont. Manu. Contr.",
    # Power Amplifiers plural → singular
    "power amplifiers": "Power Amplifier",
    # Remote Control Encryption Unit
    "remote control encryption unit": "Remote-Control Encryption",
    # Rigger Control / Interface → Rigger Adaptation
    "rigger control": "Rigger Adaptation",
    "rigger controls": "Rigger Adaptation",
    "rigger interface": "Rigger Adaptation",
    # Remote-Control Gear → Remote-Control Interface
    "remote-control gear": "Remote-Control Interface",
    # Antitheft → Anti-Theft System
    "antitheft": "Anti-Theft System",
    # Environmental Adaptation → Artic/Desert Adaptation Kit
    "environmental adaptation": "Artic/Desert Adaptation Kit",
    # Smoke Generator alias
    "smoke generator": "Smoke Generator",
    # Electronics Bay
    "electronics bay": "Electronics Bay",
    # Amphibious OPs Package → Amphibious Operation
    "amphibious ops package": "Amphibious Operation",
    "amphibious operation package": "Amphibious Operation",
    # Drone Rack aliases
    "external drone racks": "Drone Rack",
    # Hyphenated turret variants → space-separated catalog forms
    "remote micro-turret": "Remote Micro Turret",
    "remote mini-turret": "Remote Mini Turret",
    "remote small-turret": "Remote Small Turret",
    "remote medium-turret": "Remote Medium Turret",
    "remote large-turret": "Remote Large Turret",
    "remote ex-large-turret": "Remote Ex-Large Turret",
    "remote popup micro-turret": "Remote Micro Turret (Pop-Up)",
    "remote micro-turret(pop-up)": "Remote Micro Turret (Pop-Up)",
    "remote mini-popup": "Remote Mini Turret (Pop-Up)",
    "mini-popup": "Mini-Turret (Pop-Up)",
    # 2xN rocket-mount patterns
    "2x4 external rocket mounts": "External Rocket Mount",
    "2x3 external rocket mounts": "External Rocket Mount",
    "2x3 rocket mounts": "External Rocket Mount",
    # Retrans Mission Unit → Retransmission Unit
    "retrans mission unit": "Retransmission Unit",
    # Satellite Link rating variants (strip back to base entry)
    "satellite link 3": "Satellite Link",
    "satellite link 5": "Satellite Link",
    # Large Smoke Projector → Smoke Generator (type-23 alias)
    "large smoke projector": "Smoke Generator",
    "vehicle smoke projector(large)": "Smoke Generator",
    "vehicle smoke projector(small)": "Smoke Generator",
    # 2-letter acronym + digit that _TRAILING_GLUED_N misses (threshold is 3)
    "ed5": "ED [5]",
    # Adjective-order swap: source data says "Medium Remote Turret",
    # catalog stores "Remote Medium Turret".
    "medium remote turret": "Remote Medium Turret",
}


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
        # Strip a leading quantity prefix ("2 External Hardpoints" → quantity=2,
        # base="External Hardpoint"). Stripping plural-s on the trailing word
        # gives the singular catalog form for common cases like Bench Seats,
        # Hardpoints, Living Amenities (treated below per-token).
        working = raw
        leading_count = None
        m = _VEHICLE_LEADING_COUNT.match(working)
        if m:
            leading_count = int(m.group(1))
            working = m.group(2)

        # Light typo-correction pass before normalization.
        for bad, good in _VEHICLE_TYPO_FIXUPS.items():
            # Use negative lookbehind/lookahead so short tokens like "ram"
            # don't get replaced inside longer words (e.g. "programming").
            pattern = r"(?<![a-zA-Z])" + re.escape(bad) + r"(?![a-zA-Z])"
            if re.search(pattern, working, re.I):
                working = re.sub(pattern, good, working, flags=re.I)

        pt = normalize_token(working)
        # Preserve the raw text rather than the corrected/stripped version so
        # downstream consumers can still see what the source actually said.
        if not pt.canonical:
            out.notes.append(raw)
            continue

        # Try vehicle-mount structured parsing first.
        mount_info = parse_vehicle_mount_descriptor(pt.canonical, pt.paren_payload)
        if mount_info is not None and not mount_info.get("trailing"):
            # Only treat as a mount descriptor when there's no extra trailing
            # text (e.g. "External Fixed Hardpoint w/MMG" is a compound note,
            # not a pure mount descriptor, so it falls through to regular
            # checks). We still try the lookup and store structured payload.
            gid = catalog.lookup(pt.canonical, pt.rating)
            payload = json.dumps(mount_info, sort_keys=True)
            if gid is not None:
                out.resolved.append((gid, None, raw, pt.rating, payload))
                continue
            # No catalog match for a clean mount descriptor — keep unresolved
            out.unresolved.append(raw)
            continue

        # Project-specific vehicle-mod name overrides take precedence over
        # catalog lookup.
        override_name = apply_vehicle_name_override(pt.canonical, pt.rating)
        if override_name is not None:
            gid = catalog.lookup(override_name, pt.rating)
            if gid is not None:
                out.resolved.append((gid, None, raw, pt.rating, pt.paren_payload))
                continue

        gid = catalog.lookup(pt.canonical, pt.rating)
        if gid is not None:
            out.resolved.append((gid, None, raw, pt.rating, pt.paren_payload))
            continue

        # No catalog hit. Recognized feature-note name → notes.
        if pt.canonical.lower() in _FEATURE_NOTE_NAMES:
            out.notes.append(raw)
            continue
        # Looks like rules text — also notes.
        lower = raw.lower()
        if any(s in lower for s in _NOTE_SUBSTRINGS):
            out.notes.append(raw)
            continue
        # Single-char garbage placeholders.
        if len(pt.canonical) <= 1:
            out.notes.append(raw)
            continue
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
