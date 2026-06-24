"""Seeded (defective) implementation: an off-by-one that charges one extra
unit. The answer key fails against this — a SEV2+ "escape"."""


def line_total(quantity, unit_cents):
    """Total price in cents for `quantity` units at `unit_cents` each."""
    return quantity * unit_cents + unit_cents  # BUG: one unit too many
