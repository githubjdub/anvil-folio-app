"""Measure server startup reads with a five-year, eight-security temporary fixture."""
from datetime import date, datetime, timedelta, timezone
from time import perf_counter
import json
import statistics
import uuid

import anvil.users
from anvil.tables import app_tables
import portfolio_analytics
import portfolios
import sectors

original_user = anvil.users.get_user
user = None
try:
    tag = uuid.uuid4().hex[:12]
    user = app_tables.users.add_row(email='startup-%s@example.invalid' % tag,
        enabled=True, signed_up=datetime.now(timezone.utc), username='startup_' + tag,
        username_key='startup_' + tag, membership_tier='premium', membership_source='creator')
    anvil.users.get_user = lambda: user
    as_of = date.today()
    start = as_of - timedelta(days=5 * 365)
    days = [start + timedelta(days=i) for i in range((as_of-start).days+1)
            if (start + timedelta(days=i)).weekday() < 5]
    symbols = sorted(sectors._CATALOG)[:8]
    transactions = [dict(ticker=symbol, trade_date=days[0].isoformat(), action='Buy',
                         shares=10, trade_price=100) for symbol in symbols]
    history = dict(company_name='Startup benchmark', instrument_type='EQUITY',
                   close={day: 100 + index * .04 for index, day in enumerate(days)}, splits={}, dividends={})
    histories = {symbol: history for symbol in symbols}
    histories.update({'^GSPC': history, '^SP500TR': history})
    clean, _, _ = portfolio_analytics._validate(transactions, as_of, 0)
    analysis = portfolio_analytics._build_analysis(clean, as_of, 0, histories)
    saved = portfolios.save_portfolio(None, 'Startup benchmark', transactions, as_of, 0)
    assert saved['ok'], saved
    portfolios.remember_analysis(transactions, as_of, 0, analysis, saved['portfolio'].get_id())
    timings = {'account_metadata': [], 'saved_portfolio': [], 'analysis_json_encoding': []}
    for _ in range(5):
        before = perf_counter()
        workspace = portfolios.get_portfolio_workspace()
        timings['account_metadata'].append((perf_counter()-before)*1000)
        before = perf_counter()
        restored = portfolios.get_last_portfolio()
        timings['saved_portfolio'].append((perf_counter()-before)*1000)
        assert restored['analysis'] is not None and workspace['usage']['count'] == 1
        before = perf_counter()
        encoded = json.dumps(restored['analysis'], separators=(',', ':')).encode()
        timings['analysis_json_encoding'].append((perf_counter()-before)*1000)
    print(json.dumps({'measurement': 'server execution only; excludes browser, RPC transport and cold runtime startup',
        'fixture': {'securities': len(symbols), 'history_sessions': len(days), 'json_bytes': len(encoded)},
        'timings_ms': {name: {'median': round(statistics.median(values), 1),
                             'min': round(min(values), 1), 'max': round(max(values), 1)}
                       for name, values in timings.items()}}))
finally:
    anvil.users.get_user = original_user
    if user is not None:
        for name in ('investor_profiles', 'portfolio_directory', 'portfolios'):
            for row in getattr(app_tables, name).search(owner=user):
                row.delete()
        user.delete()
    print('Startup benchmark fixtures removed.')
