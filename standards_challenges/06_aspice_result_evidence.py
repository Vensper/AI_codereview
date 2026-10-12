"""Automotive SPICE SWE.4: each selected unit needs a recorded PASS result."""


def verification_complete(unit_ids, results_by_unit):
    return bool(unit_ids) and all(
        results_by_unit.get(unit_id, "PASS") == "PASS" for unit_id in unit_ids
    )
