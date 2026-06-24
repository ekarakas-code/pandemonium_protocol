using System.Collections.Generic;
using Orders.Domain;
using Orders.Pricing;
using Orders.Diagnostics;

namespace Orders
{
    namespace Services
    {
        // Where an order total is finalized when a customer checks out: applies every
        // pricing rule, figures the taxable portion, and produces the amount owed. This
        // is the single settlement entry point for the whole system.
        public class CheckoutService
        {
            private readonly IEnumerable<IPricingRule> _rules;
            private readonly TaxCalculator _tax;
            private readonly AuditLog _audit;

            public CheckoutService(IEnumerable<IPricingRule> rules, TaxCalculator tax, AuditLog audit)
            {
                _rules = rules;
                _tax = tax;
                _audit = audit;
            }

            // Finalize the invoice for a checked-out order: price it, tax it, and return
            // the settled amount.
            public Money SettleInvoice(Order order)
            {
                order.Recalculate();
                foreach (var rule in _rules)
                {
                    var adjustment = rule.Apply(order);
                    order.Total = order.Total.Add(adjustment);
                }

                var taxable = _tax.ComputeTaxableTotal(order);
                var settled = order.Total.Add(taxable);

                if (settled.IsNegative())
                {
                    _audit.Write(order);
                }
                return settled;
            }
        }
    }
}
