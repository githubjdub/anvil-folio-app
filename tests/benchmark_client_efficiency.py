"""Deterministic CPython microbenchmark; this does not measure browser startup.

Run: python tests/benchmark_client_efficiency.py [path/to/baseline_module.py]
The optional baseline is compared for exact result equality as well as timing.
"""
from datetime import date, timedelta
import json
from pathlib import Path
import runpy
from statistics import median
import sys
from time import perf_counter

APP = Path(__file__).resolve().parents[1]


def fixture(count):
    start = date(2021, 1, 4)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(1825)
             if (start + timedelta(days=i)).weekday() < 5]
    securities, transactions, rows = [], [], []
    for index in range(count):
        symbol = 'TEST%03d' % index
        days = []
        shares = 0
        for day_index, day in enumerate(dates):
            bought = sold = 0
            if day_index == 800:
                shares *= 2
            price = (100 + day_index * .02) / (2 if day_index >= 800 else 1)
            if day_index % 100 == 0:
                action = 'Buy' if day_index % 200 == 0 else 'Sell'
                quantity = 10 if action == 'Buy' else 2
                shares += quantity if action == 'Buy' else -quantity
                bought = quantity * price if action == 'Buy' else 0
                sold = quantity * price if action == 'Sell' else 0
                transactions.append(dict(ticker=symbol, trade_date=day, action=action,
                                         shares=quantity, trade_price=price))
                rows.append(dict(entry_date_used=day, entry_price=price))
            days.append(dict(shares=shares, market_value=shares*price, price=price,
                             contributions=bought, withdrawals=sold))
        securities.append(dict(symbol=symbol, company_name=symbol,
                               sector='Technology', days=days, **days[-1]))
    return dict(selection_data=dict(dates=dates, securities=securities), rows=rows,
                series=[dict(date=day, session_complete=True) for day in dates]), transactions


def measure(current, baseline, source, transactions):
    modules = {'after': current}
    if baseline:
        modules['before'] = baseline
    samples = {name: [] for name in modules}
    results = {}
    for module in modules.values():
        module['build_dashboard'](source, transactions=transactions)
    for _ in range(9):
        order = list(modules) if _ % 2 else list(reversed(modules))
        for name in order:
            start = perf_counter()
            results[name] = modules[name]['build_dashboard'](source, transactions=transactions)
            samples[name].append((perf_counter()-start)*1000)
    return results, {name: round(median(values), 3) for name, values in samples.items()}


if __name__ == '__main__':
    current = runpy.run_path(str(APP / 'client_code/holdings_dashboard.py'))
    baseline = runpy.run_path(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []
    for count in (8, 50):
        source, transactions = fixture(count)
        measured, timings = measure(current, baseline, source, transactions)
        result = measured['after']
        before, after = timings.get('before'), timings['after']
        if baseline:
            assert measured['before'] == result, 'Latest holdings result changed'
            for period in result['periods']:
                args = (source, None, period['key'], transactions)
                assert baseline['build_dashboard'](*args) == current['build_dashboard'](*args)
        results.append(dict(securities=count, sessions=len(source['series']),
                            median_before_ms=before, median_after_ms=after,
                            speedup=round(before/after, 2) if before else None,
                            all_quarters_identical=baseline is not None))
    print(json.dumps({'measurement': 'Local CPython, 9 samples per case; not browser timing',
                      'results': results}, indent=2))
