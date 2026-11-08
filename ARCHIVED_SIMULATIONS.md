# Archived dividend simulations

The former **Simulations** tab has been replaced by **Tax Estimator** in Form1.
The original `Components.Simulations` Form and the `drip_simulations`,
`drip_engine`, and `dividend_payment_sources` Server Modules remain in the app
for reference and a possible future DRIP feature. The old Form is no longer
embedded in the portfolio workspace or reachable from its navigation.

The new `Components.TaxEstimator` Form and `tax_estimator` / `tax_rules` Server
Modules run hypothetical taxable trades. Scenarios do not mutate portfolio
transactions or execute brokerage orders.
