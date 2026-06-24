using Orders.Domain;

namespace Orders
{
    namespace Pricing
    {
        // Discount that kicks in when a basket is large: a flat percentage off the
        // order total once the item count crosses a threshold. Implements IPricingRule.
        public class BulkDiscountRule : IPricingRule
        {
            private readonly int _threshold;
            private readonly decimal _rate;

            public BulkDiscountRule(int threshold, decimal rate)
            {
                _threshold = threshold;
                _rate = rate;
            }

            public Money Apply(Order order)
            {
                if (order.Items.Count < _threshold)
                {
                    return new Money(0m);
                }
                return order.Total.Multiply(-_rate);
            }
        }
    }
}
