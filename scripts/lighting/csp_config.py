"""Slim port of prodrive-ac-builder's scripts/lighting/csp_config.py.

Only :func:`resolve_true_north` is carried over — it's what the network mesh builder calls at the end
of every build. The full module there also computes solar positions / emits per-preset sun JSON; this
repo drives CSP lighting through scripts/ac/ext_config.py instead, so that half stays behind.
"""

from __future__ import annotations

from scripts.config import TrackConfig


def resolve_true_north(config: TrackConfig) -> float:
    """Model yaw (deg, clockwise about vertical) that aligns the exported track with AC's sun.

    mirror_x tracks are spun 180 deg vs AC's world-fixed sun, so they need a 180 deg counter-yaw.
    Non-mirrored tracks need none. Override with ``lighting.model_yaw_deg``.
    """
    raw = config.raw
    override = (raw.get("lighting", {}) or {}).get("model_yaw_deg")
    if override is not None:
        return float(override) % 360.0
    return 180.0 if bool(raw.get("mirror_x", True)) else 0.0
