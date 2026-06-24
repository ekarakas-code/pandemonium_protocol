namespace Orders
{
    namespace Domain
    {
        // A single purchasable line on an order.
        public class LineItem
        {
            public string Sku { get; set; }

            public int Quantity { get; set; }

            public Money UnitPrice { get; set; }

            public LineItem(string sku, int quantity, Money unitPrice)
            {
                Sku = sku;
                Quantity = quantity;
                UnitPrice = unitPrice;
            }

            public Money LineTotal()
            {
                return UnitPrice.Multiply(Quantity);
            }
        }
    }
}
