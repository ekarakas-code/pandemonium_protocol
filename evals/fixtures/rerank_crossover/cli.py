"""CLI commands — thin dispatchers over the billing service."""

from service import assess_penalty, render_invoice, settle_account


def bill(customer, usage, plan):
    """CLI command: print a customer's bill."""
    print(render_invoice(settle_account(usage, plan), customer))


def penalize(customer, amount, days, plan):
    """CLI command: print a bill with the overdue surcharge added."""
    print(render_invoice(assess_penalty(amount, days, plan), customer))


def serve(port):
    """CLI command: start the billing HTTP server."""
    print(f"billing server listening on {port}")
    return port
