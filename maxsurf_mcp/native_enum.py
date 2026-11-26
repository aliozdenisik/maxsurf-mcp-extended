"""One owner for Maxsurf enum name <-> number conversion.

Enums are keyed by the type name the Maxsurf automation manuals and type
library use (``msSurfaceUse``). Numbers come only from the live session: the
exact enum type in the type library, or, when that cannot be read, the shared
constants discovery filtered to the type's documented member prefix.
"""
from __future__ import annotations

from typing import Any

import pythoncom

from . import constants as comconstants
from .errors import MaxsurfValidationError

# Fallback only. Member prefixes from the manuals' naming; add a type when a
# caller needs it. Types missing here fail closed on the fallback path.
_PREFIXES = {
    "msDirectionType": "msDT",
    "msGridType": "msGT",
    "msLongDatumType": "msLDT",
    "msMarkerType": "msMT",
    "msSurfaceLibrary": "msSL",
    "msSurfaceSkinDirection": "msSSD",
    "msSurfaceUse": "msSU",
    "msVertDatumType": "msVDT",
}

# Type-library members per connected app object; holding the app keeps id() unique.
_cache: dict[int, tuple[Any, dict[str, dict[str, int]]]] = {}


def clear() -> None:
    """Drop cached members; called by the session reset path."""
    _cache.clear()


def _type_library(app: Any) -> Any | None:
    try:
        library = app._oleobj_.GetTypeInfo().GetContainingTypeLib()[0]
        count = int(library.GetTypeInfoCount())
    except (AttributeError, TypeError, pythoncom.com_error):
        return None
    return library, count


def _from_type_library(library: Any, count: int,
                       enum_type: str) -> dict[str, int] | None:
    for i in range(count):
        ti = library.GetTypeInfo(i)
        if ti.GetDocumentation(-1)[0] == enum_type:
            attr = ti.GetTypeAttr()
            if attr.typekind != pythoncom.TKIND_ENUM:
                return None
            return {ti.GetNames(v.memid)[0]: int(v.value)
                    for v in (ti.GetVarDesc(j) for j in range(attr.cVars))}
    return None


def members(app: Any, enum_type: str) -> dict[str, int]:
    """All members of one enum type, from the live session."""
    found = _type_library(app)
    if found is not None:
        entry = _cache.setdefault(id(app), (app, {}))[1]
        if enum_type not in entry:
            result = _from_type_library(*found, enum_type)
            if result is None:
                raise MaxsurfValidationError(
                    "native enum type unavailable", enum_type=enum_type,
                    source="type_library")
            entry[enum_type] = result
        return dict(entry[enum_type])
    prefix = _PREFIXES.get(enum_type)
    discovered = comconstants.discover(app, name_filter=prefix) if prefix else None
    result = {} if discovered is None else {
        name: int(number) for name, number in discovered["constants"].items()
        if name.startswith(prefix) and not isinstance(number, bool)}
    if not result:
        raise MaxsurfValidationError(
            "native enum type unavailable", enum_type=enum_type,
            source=discovered["source"] if discovered else "generated_constants")
    return result


def resolve(app: Any, enum_type: str, requested: Any,
            field: str) -> dict[str, Any]:
    """Case-insensitive, trimmed member name -> {"enum_name", "value"}."""
    available = members(app, enum_type)
    wanted = requested.strip().casefold() if isinstance(requested, str) else ""
    canonical = next((k for k in available if k.casefold() == wanted), None)
    if not wanted or canonical is None:
        raise MaxsurfValidationError(
            f"unknown {field} enum member", field=field, requested=requested,
            enum_type=enum_type, available=sorted(available))
    return {"enum_name": canonical, "value": available[canonical]}


def name_of(app: Any, enum_type: str, value: Any) -> str | None:
    """Canonical member name for a native value, or None when none matches."""
    return next((k for k, v in members(app, enum_type).items() if v == value),
                None)


def constant(app: Any, name: str) -> int:
    """One exactly named constant; never prefix-matched."""
    discovered = comconstants.discover(app, name_filter=name)
    number = discovered["constants"].get(name)
    if number is None or isinstance(number, bool):
        raise MaxsurfValidationError("native constant unavailable",
                                     constant=name, source=discovered["source"])
    return int(number)
