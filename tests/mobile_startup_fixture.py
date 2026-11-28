"""Temporary browser benchmark data. Run through the agent's server REPL only.

The browser harness supplies email/password/action in memory, never in this file.
"""
from datetime import date, timedelta
import json

import anvil.users
from anvil.tables import app_tables


user = app_tables.users.get(email=benchmark_email)
if benchmark_action == "create":
    import portfolio_analytics
    import portfolios
    import sectors

    if user is None:
        user = anvil.users.signup_with_email(benchmark_email, benchmark_password)
    username = benchmark_email.split("@")[0].replace("-", "_")
    user.update(username=username, username_key=username,
                membership_tier="premium", membership_source="creator")
    anvil.users.force_login(user)
    as_of = date.today()
    start = as_of - timedelta(days=5 * 365)
    days = [start + timedelta(days=i) for i in range((as_of - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]
    symbols = sorted(sectors._CATALOG)[:8]
    transactions = [dict(ticker=symbol, trade_date=days[0].isoformat(),
                         action="Buy", shares=10, trade_price=100) for symbol in symbols]
    history = dict(company_name="Mobile benchmark", instrument_type="EQUITY",
                   close={day: 100 + index * .04 for index, day in enumerate(days)},
                   splits={}, dividends={})
    histories = {symbol: history for symbol in symbols}
    histories.update({"^GSPC": history, "^SP500TR": history})
    clean, _, _ = portfolio_analytics._validate(transactions, as_of, 0)
    analysis = portfolio_analytics._build_analysis(clean, as_of, 0, histories)
    existing = next(iter(app_tables.portfolios.search(owner=user)), None)
    saved = portfolios.save_portfolio(existing.get_id() if existing else None,
                                     "Mobile startup benchmark", transactions, as_of, 0)
    assert saved["ok"]
    portfolios.remember_analysis(transactions, as_of, 0, analysis, saved["portfolio"].get_id())
    print("FIXTURE " + json.dumps(dict(securities=len(symbols), sessions=len(days),
                                      market_value=analysis["metrics"]["market_value"])))
elif benchmark_action == "cleanup":
    if user is not None:
        for name in ("notification_messages", "notification_deliveries", "notification_settings",
                     "insider_watches", "portfolio_plans", "performance_shares", "net_worth_entries",
                     "net_worth_snapshots", "security_sectors", "investor_profiles", "portfolio_directory",
                     "portfolios", "site_visits"):
            for row in getattr(app_tables, name).search(owner=user):
                row.delete()
        user.delete()
    print("Fixture removed")
else:
    raise ValueError("Unknown benchmark action")
