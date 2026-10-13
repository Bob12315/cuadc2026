#!/usr/bin/env python3
"""Report licenses for installed direct/transitive packages; no network needed."""
from __future__ import annotations

import warnings
from importlib.metadata import Distribution, distributions


def _metadata_value(distribution: Distribution, key: str) -> str:
    """Read optional metadata without relying on an untyped ``get`` method."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            value = distribution.metadata[key]
    except KeyError:
        return ""
    return value or ""


def main() -> None:
    unknown: list[str] = []
    for dist in sorted(
        distributions(),
        key=lambda item: _metadata_value(item, "Name").lower(),
    ):
        name = _metadata_value(dist, "Name") or "<unnamed distribution>"
        version = _metadata_value(dist, "Version") or "UNKNOWN"
        license_name = _metadata_value(dist, "License") or "UNKNOWN"
        print(f"{name}=={version}: {license_name}")
        if license_name == "UNKNOWN":
            unknown.append(name)
    if unknown:
        print("WARNING: metadata license missing for: " + ", ".join(unknown))


if __name__ == "__main__":
    main()
