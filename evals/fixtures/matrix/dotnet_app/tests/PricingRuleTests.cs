using Orders.Domain;
using Orders.Pricing;

namespace Orders
{
    namespace Tests
    {
        // Tests for the pricing rule implementations: BulkDiscountRule discounts large
        // baskets and SeasonalRule discounts during a sale window.
        public class PricingRuleTests
        {
            public bool BulkDiscountAppliesAboveThreshold()
            {
                var order = new Order("T-4");
                order.Items.Add(new LineItem("sku-1", 1, new Money(10m)));
                order.Total = new Money(100m);

                var rule = new BulkDiscountRule(1, 0.10m);
                var adjustment = rule.Apply(order);
                return adjustment.Amount == -10m;
            }

            public bool SeasonalDiscountAppliesWhenActive()
            {
                var order = new Order("T-5");
                order.Total = new Money(100m);

                var rule = new SeasonalRule(true, 0.20m);
                var adjustment = rule.Apply(order);
                return adjustment.Amount == -20m;
            }
        }
    }
}
