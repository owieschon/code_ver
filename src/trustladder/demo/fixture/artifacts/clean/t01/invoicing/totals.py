"""Reference (clean) implementation: the answer key passes against this."""


def line_total(quantity, unit_cents):
    """Total price in cents for `quantity` units at `unit_cents` each."""
    return quantity * unit_cents
