# Billing computation notes

This note explains how the monthly billing amount is computed for each customer. The billing
amount is computed by multiplying metered usage units by the plan unit rate and then subtracting
the plan discount. It is important to understand where the billing amount is computed and how
the computed billing amount flows through the rest of the billing pipeline.
