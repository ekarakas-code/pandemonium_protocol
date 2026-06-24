using System.Collections.Generic;
using Orders.Domain;
using Orders.Pricing;
using Orders.Services;
using Orders.Diagnostics;

namespace Orders
{
    namespace App
    {
        // Composition root: wires the pricing rules, tax calculator, audit log, and the
        // checkout/processor services together, then kicks off a batch run.
        public class Program
        {
            public static void Main(string[] args)
            {
                var rules = new List<IPricingRule>
                {
                    new BulkDiscountRule(10, 0.05m),
                    new SeasonalRule(true, 0.10m),
                    new LoyaltyRule(2)
                };

                var tax = new TaxCalculator(100m);
                var audit = new AuditLog();
                var checkout = new CheckoutService(rules, tax, audit);
                var processor = new OrderProcessor(checkout, tax);

                var orders = new List<Order> { new Order("A-1"), new Order("A-2") };
                processor.ProcessBatch(orders);
            }
        }
    }
}
