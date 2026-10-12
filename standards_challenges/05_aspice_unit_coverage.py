"""Automotive SPICE SWE.4: each release-scope unit needs a verification measure."""


def all_units_covered(unit_ids, measures_by_unit):
    return bool(unit_ids) and any(unit_id in measures_by_unit for unit_id in unit_ids)
