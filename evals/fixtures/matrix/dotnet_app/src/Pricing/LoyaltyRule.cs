using Orders.Domain;

namespace Orders
{
    namespace Pricing
    {
        // Rewards repeat customers with a small credit based on their loyalty tier.
        // Implements IPricingRule. Not a discount on the basket size, so a "bulk
        // discount" query should prefer BulkDiscountRule / SeasonalRule over this.
        public class LoyaltyRule : IPricingRule
        {
            private readonly int _tier;

            public LoyaltyRule(int tier)
            {
                _tier = tier;
            }

            public Money Apply(Order order)
            {
                var credit = 0.01m * _tier;
                return order.Total.Multiply(-credit);
            }
        }
    }
}
