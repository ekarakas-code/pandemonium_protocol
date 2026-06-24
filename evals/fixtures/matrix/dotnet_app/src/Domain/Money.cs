namespace Orders
{
    namespace Domain
    {
        // A tiny value type for currency math. Distractor math family: Add / Multiply
        // exist so a "compute total" query has a plausible-but-wrong neighbour to rank
        // against the real settlement / taxable-total code.
        public readonly struct Money
        {
            public decimal Amount { get; }

            public Money(decimal amount)
            {
                Amount = amount;
            }

            public Money Add(Money other)
            {
                return new Money(Amount + other.Amount);
            }

            public Money Multiply(decimal factor)
            {
                return new Money(Amount * factor);
            }

            public bool IsNegative()
            {
                return Amount < 0m;
            }

            public override string ToString()
            {
                return Amount.ToString("0.00");
            }
        }
    }
}
