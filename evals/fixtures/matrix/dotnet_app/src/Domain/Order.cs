using System.Collections.Generic;

namespace Orders
{
    namespace Domain
    {
        // The order aggregate. Holds its line items and a running Total that is
        // refreshed from the items whenever the order is recalculated.
        public class Order
        {
            public string Id { get; set; }

            public Money Total { get; set; }

            public List<LineItem> Items { get; }

            public Order(string id)
            {
                Id = id;
                Items = new List<LineItem>();
                Total = new Money(0m);
            }

            // Recompute the order total by summing every line item. Called after the
            // basket changes and before pricing rules or settlement run.
            public Money Recalculate()
            {
                var running = new Money(0m);
                foreach (var item in Items)
                {
                    running = running.Add(item.LineTotal());
                }
                Total = running;
                return Total;
            }
        }
    }
}
