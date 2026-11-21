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
from analysis_selection import analyze_selection
from portfolio_summary import summarize_portfolio
from holdings_dashboard import build_dashboard

original_user = anvil.users.get_user
original_directory = portfolios._directory_for
original_fetch = portfolio_analytics._fetch_history
users = []
directory_calls = []


def counted_directory(user):
    directory_calls.append(user.get_id())
    return original_directory(user)


def forbidden_fetch(*args, **kwargs):
    raise AssertionError('Restoring saved values unexpectedly fetched market history')


try:
    tag = uuid.uuid4().hex[:12]
    for number in range(2):
        users.append(app_tables.users.add_row(email='startup-%s-%s@example.invalid' % (tag, number),
            enabled=True, signed_up=datetime.now(timezone.utc), username='startup_' + tag + str(number),
            username_key='startup_' + tag + str(number), membership_tier='premium', membership_source='creator'))
    user = users[0]
    anvil.users.get_user = lambda: user
    portfolio_analytics._fetch_history = forbidden_fetch
    empty = portfolios.get_portfolio_startup()
    assert empty['portfolio'] is None and empty['overview'] is None
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
    identifier = saved['portfolio'].get_id()
    assert portfolios.get_portfolio_startup()['overview'] is None
    portfolios.remember_analysis(transactions, as_of, 0, analysis, identifier)
    portfolios._directory_for = counted_directory
    timings = {name: [] for name in ('account_metadata', 'saved_portfolio', 'analysis_json_encoding',
                                     'startup_values', 'values_json_encoding', 'deferred_history')}
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
        directory_calls.clear()
        before = perf_counter()
        startup = portfolios.get_portfolio_startup()
        timings['startup_values'].append((perf_counter()-before)*1000)
        assert len(directory_calls) == 1, directory_calls
        assert startup['portfolio'].get_id() == identifier
        overview = startup['overview']
        before = perf_counter()
        values_encoded = json.dumps(overview, separators=(',', ':')).encode()
        timings['values_json_encoding'].append((perf_counter()-before)*1000)
        before = perf_counter()
        history_result = portfolios.get_portfolio_history(identifier)
        timings['deferred_history'].append((perf_counter()-before)*1000)
        assert history_result['analysis'] == restored['analysis']
        selected = analyze_selection(history_result['analysis'], symbols)
        assert overview['summary'] == summarize_portfolio(selected)
        assert overview['metrics'] == selected['metrics']
        assert overview['holdings'] == build_dashboard(history_result['analysis'], symbols,
                                                      transactions=saved['portfolio']['transactions'])
        assert set(overview) == {'summary', 'holdings', 'metrics', 'metadata'}
    print('PASS: initial saved values equal the full analysis; one directory query; history available separately; no market fetches')
    print(json.dumps({'measurement': 'server execution only; excludes browser, RPC transport and cold runtime startup',
        'fixture': {'securities': len(symbols), 'history_sessions': len(days),
                    'decoded_analysis_json_bytes': len(encoded), 'decoded_overview_json_bytes': len(values_encoded),
                    'initial_analysis_payload_reduction_percent': round((1-len(values_encoded)/len(encoded))*100, 2)},
        'payload_note': 'Analysis data only; excludes account metadata, live rows, RPC framing and transport compression.',
        'timings_ms': {name: {'median': round(statistics.median(values), 1),
                             'min': round(min(values), 1), 'max': round(max(values), 1)}
                       for name, values in timings.items()}}))

    sectors.set_security_sector(symbols[0], 'Industrials')
    updated = portfolios.get_portfolio_startup()['overview']
    assert next(row for row in updated['holdings']['rows'] if row['symbol'] == symbols[0])['sector'] == 'Industrials'
    assert updated['summary'] == overview['summary']

    # Explicit links choose that portfolio; reading its deferred history must
    # not make it most-recent after another portfolio has been opened.
    second = portfolios.save_portfolio(None, 'Second startup portfolio', [], as_of, 0)['portfolio']
    linked = portfolios.get_portfolio_startup(identifier)
    assert linked['portfolio'].get_id() == identifier and linked['overview'] is not None
    portfolios.load_portfolio(second.get_id())
    portfolios.get_portfolio_history(identifier)
    assert portfolios.get_last_portfolio()['portfolio'].get_id() == second.get_id()
    assert not portfolios.get_portfolio_startup('not-a-portfolio-id')['ok']
    assert not portfolios.get_portfolio_history('not-a-portfolio-id')['ok']

    user = users[1]
    other = portfolios.get_portfolio_startup()
    assert other['portfolio'] is None and other['overview'] is None
    assert other['workspace']['usage']['count'] == 0
    assert not portfolios.get_portfolio_startup(identifier)['ok']
    assert not portfolios.get_portfolio_history(identifier)['ok']
    user = None
    for callable_, args in ((portfolios.get_portfolio_startup, ()), (portfolios.get_portfolio_history, (identifier,))):
        try:
            callable_(*args)
            raise AssertionError('Anonymous startup access succeeded')
        except anvil.users.AuthenticationFailed:
            pass
    user = users[0]
    changed = [dict(trade, shares=11) for trade in transactions]
    assert portfolios.save_portfolio(identifier, 'Edited after snapshot', changed, as_of, 0)['ok']
    assert portfolios.get_portfolio_history(identifier)['analysis'] is None
    assert portfolios.get_portfolio_startup(identifier)['overview'] is None
    saved['portfolio'].delete()
    assert not portfolios.get_portfolio_history(identifier)['ok']
    print('PASS: current classifications, explicit links, empty/unsynced portfolios, navigation order, edit invalidation, deletion and account isolation')
finally:
    anvil.users.get_user = original_user
    portfolios._directory_for = original_directory
    portfolio_analytics._fetch_history = original_fetch
    for user in users:
        for name in ('security_sectors', 'investor_profiles', 'portfolio_directory', 'portfolios'):
            for row in getattr(app_tables, name).search(owner=user):
                row.delete()
        user.delete()
    print('Startup benchmark fixtures removed.')
