"""Billing service — the real computations the CLI dispatches to.

These functions are deliberately named so the BURIED queries (which use domain words like
"billing amount computed" / "late fee") do NOT keyword-match the symbol name — exactly the
case where prose docs out-rank the implementation under the embedding model.
"""


def settle_account(usage_records, plan):
    units = sum(r.units for r in usage_records)
    base = units * plan.unit_rate
    return round(base - base * plan.discount_fraction, 2)


def assess_penalty(amount, days_late, plan):
    return round(amount + amount * plan.late_fee_rate * days_late, 2)


def render_invoice(amount, customer):
    return f"Invoice for {customer}: ${amount}"
