using Orders.Domain;
using Orders.Services;

namespace Orders
{
    namespace Tests
    {
        // Tests for the taxable-total computation. These call ComputeTaxableTotal directly
        // and check the exemption threshold behaviour.
        public class TaxCalculatorTests
        {
            public bool ZeroBelowThreshold()
            {
                var tax = new TaxCalculator(100m);
                var order = new Order("T-2");
                order.Total = new Money(80m);

                var taxable = tax.ComputeTaxableTotal(order);
                return taxable.Amount == 0m;
            }

            public bool TaxesExcessAboveThreshold()
            {
                var tax = new TaxCalculator(100m);
                var order = new Order("T-3");
                order.Total = new Money(150m);

                var taxable = tax.ComputeTaxableTotal(order);
                return taxable.Amount == 50m;
            }
        }
    }
}
