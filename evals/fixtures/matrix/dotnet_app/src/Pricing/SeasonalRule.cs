using Orders.Domain;

namespace Orders
{
    namespace Pricing
    {
        // A seasonal promotional discount applied during a sale window. Implements
        // IPricingRule and returns a percentage off the order total when active.
        public class SeasonalRule : IPricingRule
        {
            private readonly bool _active;
            private readonly decimal _rate;

            public SeasonalRule(bool active, decimal rate)
            {
                _active = active;
                _rate = rate;
            }

            public Money Apply(Order order)
            {
                if (!_active)
                {
                    return new Money(0m);
                }
                return order.Total.Multiply(-_rate);
            }
        }
    }
}
