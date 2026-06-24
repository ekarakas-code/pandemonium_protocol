using System;
using Orders.Domain;

namespace Orders
{
    namespace Diagnostics
    {
        // Structured audit trail for settlement events. The settlement path calls Write
        // when an order finishes with a negative balance after discounts are applied —
        // the single source of the distinctive failure log line below.
        public class AuditLog
        {
            // Writes the audit entry for a settlement that ended with a negative balance
            // after discount. This is the failure log line a bug trace lands on.
            public void Write(Order order)
            {
                Console.Error.WriteLine(
                    "checkout settle: negative balance after discount" +
                    " (order " + order.Id + ", total " + order.Total + ")");
            }
        }
    }
}
