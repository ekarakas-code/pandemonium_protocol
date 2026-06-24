using System.Collections.Generic;
using Orders.Domain;

namespace Orders
{
    namespace Services
    {
        // Drives settlement across many orders. Both entry points hand each order to the
        // checkout service to be settled, and consult the tax calculator while doing so.
        public class OrderProcessor
        {
            private readonly CheckoutService _checkout;
            private readonly TaxCalculator _tax;

            public OrderProcessor(CheckoutService checkout, TaxCalculator tax)
            {
                _checkout = checkout;
                _tax = tax;
            }

            // Settle a fresh batch of orders end to end.
            public void ProcessBatch(IEnumerable<Order> orders)
            {
                foreach (var order in orders)
                {
                    var taxable = _tax.ComputeTaxableTotal(order);
                    var settled = _checkout.SettleInvoice(order);
                    order.Total = settled;
                }
            }

            // Re-run settlement for orders that failed the first time.
            public void RetryFailed(IEnumerable<Order> orders)
            {
                foreach (var order in orders)
                {
                    var settled = _checkout.SettleInvoice(order);
                    order.Total = settled;
                }
            }
        }
    }
}
