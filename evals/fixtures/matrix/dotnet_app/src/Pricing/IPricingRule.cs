using Orders.Domain;

namespace Orders
{
    namespace Pricing
    {
        // Strategy interface for order-level price adjustments. The checkout pipeline asks
        // every registered rule for the adjustment it contributes; concrete rules
        // (BulkDiscountRule, SeasonalRule, LoyaltyRule) live alongside this file.
        public interface IPricingRule
        {
            // Returns this rule's price adjustment for the given order.
            Money Apply(Order order);
        }
    }
}
