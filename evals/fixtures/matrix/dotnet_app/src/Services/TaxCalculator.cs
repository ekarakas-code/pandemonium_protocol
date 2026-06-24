using Orders.Domain;

namespace Orders
{
    namespace Services
    {
        // Computes the portion of an order that is subject to tax. Shared by the
        // checkout settlement path and the batch processor, and exercised directly by
        // its own unit tests — so this method is a multi-call-site refactor target.
        public class TaxCalculator
        {
            private readonly decimal _exemptThreshold;

            public TaxCalculator(decimal exemptThreshold)
            {
                _exemptThreshold = exemptThreshold;
            }

            public Money ComputeTaxableTotal(Order order)
            {
                var total = order.Total;
                if (total.Amount <= _exemptThreshold)
                {
                    return new Money(0m);
                }
                return new Money(total.Amount - _exemptThreshold);
            }
        }
    }
}
