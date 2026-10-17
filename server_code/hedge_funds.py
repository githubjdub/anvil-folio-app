import anvil.secrets
"""Public, bounded 13F library and comparisons on matching daily samples.

User portfolio paths are transient inputs and are never saved in the shared
fund tables. Only SEC documents and reproducible hypothetical fund models persist.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any

import anvil.server
import anvil.tables
from anvil.tables import app_tables
from . import hedge_fund_data, hedge_fund_engine

_data: Any = hedge_fund_data
_engine: Any = hedge_fund_engine
_MODEL_VERSION = "optimistic-13f-v1"
_TABLES = ("hedge_funds", "hedge_fund_snapshots", "hedge_fund_models")
_SCOPE = "Ranked by model Sharpe minus S&P 500 Sharpe among cached, covered profiles over these exact dates; not a ranking of actual hedge fund returns."


def _now():
  return datetime.now(timezone.utc)


def _utc(value):
  return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _ready():
  for name in _TABLES:
    try:
      getattr(app_tables, name)
    except AttributeError as error:
      if str(error) != "No such app table: '%s'" % name:
        raise
      return False
  return True


def _failure(message, **extra) -> dict[str, Any]:
  return dict(ok=False, message=message, **extra)


def _fund(fund_key):
  if not isinstance(fund_key, str) or len(fund_key) > 100:
    return None
  _data.get_catalog(refresh=True)
  if fund_key not in {entry["fund_key"] for entry in _data.get_catalog()}:
    return None
  return app_tables.hedge_funds.get(fund_key=fund_key)


def _document(value):
  return json.loads(json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, (date, datetime)) else item))


def _fingerprint(snapshots):
  material = [{key: snapshot.get(key) for key in ("quarter", "period_end", "accession", "holdings", "coverage", "source_filings")}
              for snapshot in snapshots]
  return hashlib.sha256((_MODEL_VERSION + json.dumps(_document(material), sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


@anvil.tables.in_transaction
def _store_model(fund_key, fingerprint, model):
  row = app_tables.hedge_fund_models.get(fund_key=fund_key)
  values: dict[str, Any] = dict(fund_key=fund_key, as_of_date=date.today(), fingerprint=fingerprint,
                model=_document(model), updated_at=_now())
  if row is None:
    app_tables.hedge_fund_models.add_row(**values)
  else:
    row.update(**values)


def _model(fund_key) -> dict[str, Any]:
  snapshots = _data.get_snapshots(fund_key)
  if not snapshots:
    return _failure("No filings are saved for this profile yet. Update filings to collect its quarterly holdings.")
  fingerprint = _fingerprint(snapshots)
  cached = app_tables.hedge_fund_models.get(fund_key=fund_key)
  if cached is not None and cached["as_of_date"] == date.today() and cached["fingerprint"] == fingerprint:
    return cached["model"]
  model = _engine.get_series(snapshots, date.today())
  if model["ok"]:
    _store_model(fund_key, fingerprint, model)
  return model


@anvil.server.callable
def list_hedge_funds():
  if not _ready():
    return _failure("The hedge fund library tables are not available in this environment yet.")
  items = _data.get_catalog(refresh=True)
  for item in items:
    snapshots = _data.get_snapshots(item["fund_key"])
    latest = snapshots[-1] if snapshots else None
    item["latest_quarter"] = latest["quarter"] if latest else None
    item["holdings_count"] = len(latest["holdings"]) if latest else 0
    item["quarters_count"] = len(snapshots)
  return {"ok": True, "funds": items, "message": "SEC 13F holdings · HedgeFollow profile references · 11 tracked manager choices"}


@anvil.server.callable
def get_hedge_fund_profile(fund_key):
  """Read saved public holdings without needing price history or a portfolio."""
  if not _ready():
    return _failure("The hedge fund library tables are not available in this environment yet.")
  fund = _fund(fund_key)
  if fund is None:
    return _failure("Choose a supported hedge fund profile.")
  snapshots = _data.get_snapshots(fund_key)
  return {"ok": True,
          "fund": {key: fund[key] for key in ("fund_key", "name", "manager", "source_url", "hedgefollow_url")},
          "holdings": snapshots, "last_checked_at": fund["last_checked_at"],
          "message": "Saved quarterly SEC holdings loaded." if snapshots else "No filings are saved yet. Check latest SEC filings to collect this profile."}


@anvil.tables.in_transaction
def _claim_update(fund_key, cooldown_hours=6):
  now = _now()
  row = app_tables.hedge_funds.get(fund_key=fund_key)
  if row is None:
    raise RuntimeError("A supported fund profile was removed during its update.")
  for active in app_tables.hedge_funds.search():
    started = active["update_started_at"]
    if started is not None and now - _utc(started) < timedelta(minutes=45):
      if active["fund_key"] == fund_key and active["update_job_id"]:
        return {"ok": True, "state": "running", "job_id": active["update_job_id"], "message": "This profile's filing update is already running."}
      return _failure("A filing update is already running. Try again after it finishes.")
  checked = row["last_checked_at"]
  if checked is not None and now - _utc(checked) < timedelta(hours=cooldown_hours):
    return {"ok": True, "state": "completed", "message": "Filings were checked recently; using the saved quarterly history."}
  claim_values: dict[str, Any] = {"update_started_at": now, "update_job_id": None, "tracked": True}
  row.update(**claim_values)
  return {"ok": True, "state": "claimed"}


def _queue_update(fund_key):
  claim = _claim_update(fund_key)
  if claim.get("state") != "claimed":
    return claim
  launched = False
  row = app_tables.hedge_funds.get(fund_key=fund_key)
  if row is None:
    raise RuntimeError("A supported fund profile was removed during its update.")
  try:
    task = anvil.server.launch_background_task("refresh_hedge_fund_profile", fund_key)
    row.update(update_job_id=task.get_id())
    launched = True
    return {"ok": True, "state": "running", "job_id": task.get_id(), "message": "Fetching SEC filings and building the comparison. This can take a few minutes."}
  finally:
    if not launched:
      reset_values: dict[str, Any] = {"update_started_at": None, "update_job_id": None}
      row.update(**reset_values)


@anvil.server.callable
def update_hedge_fund_filings(fund_key):
  if not _ready():
    return _failure("The hedge fund library tables are not available in this environment yet.")
  if _fund(fund_key) is None:
    return _failure("Choose a supported hedge fund profile.")
  return _queue_update(fund_key)


def _refresh_profile(fund_key):
  row = app_tables.hedge_funds.get(fund_key=fund_key)
  if row is None:
    raise RuntimeError("A supported fund profile was removed during its update.")
  try:
    anvil.server.task_state["message"] = "Checking SEC filings and resolving disclosed holdings…"
    result = _data.refresh_fund(fund_key, max_filings=5)
    if not result["ok"]:
      return result
    anvil.server.task_state["message"] = "Building the daily holdings model…"
    model = _model(fund_key)
    return {"ok": model["ok"], "message": "Filings and model updated." if model["ok"] else model["message"]}
  finally:
    finished_values: dict[str, Any] = {"update_started_at": None}
    row.update(**finished_values)


@anvil.server.background_task
def refresh_hedge_fund_profile(fund_key):
  if _fund(fund_key) is None:
    return _failure("Choose a supported hedge fund profile.")
  return _refresh_profile(fund_key)


@anvil.server.background_task
def check_hedge_fund_filings():
  """Hourly rotation checks each active profile at least once a day when idle."""
  if not _ready():
    return _failure("The hedge fund library tables are not available yet.")
  _data.get_catalog(refresh=True)
  now = _now()
  candidates = [row for row in app_tables.hedge_funds.search(tracked=True)
                if row["last_checked_at"] is None or now - _utc(row["last_checked_at"]) >= timedelta(hours=24)]
  if not candidates:
    return {"ok": True, "message": "No tracked profiles need a filing check."}
  candidates.sort(key=lambda row: _utc(row["last_checked_at"]) if row["last_checked_at"] else datetime.min.replace(tzinfo=timezone.utc))
  row = candidates[0]
  claim = _claim_update(row["fund_key"], cooldown_hours=24)
  if claim.get("state") != "claimed":
    return claim
  task_id = anvil.server.context.background_task_id
  if task_id is None:
    raise RuntimeError("Filing checks must run as a background task.")
  row.update(update_job_id=task_id)
  return _refresh_profile(row["fund_key"])


@anvil.server.callable
def get_hedge_fund_update_status(job_id):
  if not _ready() or not isinstance(job_id, str) or len(job_id) > 100:
    return _failure("Choose a valid filing update.")
  row = app_tables.hedge_funds.get(update_job_id=job_id)
  if row is None:
    return _failure("This update is no longer current. Refresh the profile.")
  task = anvil.server.get_background_task(job_id)
  error = task.get_error()
  if error is not None:
    raise error
  if task.is_running():
    return {"ok": True, "state": "running", "message": task.get_state().get("message", "Updating filings…")}
  result = task.get_return_value()
  if result is None:
    return {"ok": True, "state": "running", "message": "The update is starting…"}
  return dict(result, state="completed")


def _date(value):
  if type(value) is date:
    return value
  if isinstance(value, str):
    return date.fromisoformat(value)
  raise ValueError("Choose valid comparison dates.")


def _rate(value):
  if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value <= -1 or value > 5:
    raise ValueError("Choose a finite annual risk-free rate greater than -100% and no more than 500%.")
  return float(value)


def _points(points):
  if not isinstance(points, list) or not 2 <= len(points) <= 4000:
    raise ValueError("Sync a portfolio and choose a period containing at least two price marks (up to 4,000).")
  clean, previous = [], None
  for point in points:
    if not isinstance(point, dict):
      raise ValueError("Sync valid portfolio history before comparing.")
    day = _date(point.get("date"))
    value = point.get("index")
    if day > date.today() or day < date(1990, 1, 1) or (previous is not None and day <= previous):
      raise ValueError("Portfolio dates must be increasing, unique, and not in the future.")
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 < value <= 1e12:
      raise ValueError("Portfolio price indices must be finite positive values.")
    if not isinstance(point.get("return_observed", True), bool) or not isinstance(point.get("session_complete", True), bool):
      raise ValueError("Portfolio session flags must be true or false.")
    clean.append(dict(date=day.isoformat(), index=float(value), return_observed=point.get("return_observed", True), session_complete=point.get("session_complete", True)))
    previous = day
  return clean


def _risk(values, observed, rate):
  returns = [values[i] / values[i-1] - 1 for i in range(1, len(values)) if observed[i]]
  count = len(returns)
  volatility = sharpe = None
  if count >= 20:
    mean = sum(returns) / count
    deviation = math.sqrt(sum((value-mean)**2 for value in returns) / (count-1))
    volatility = deviation * math.sqrt(252)
    if deviation > 1e-12:
      daily_rate = math.expm1(math.log1p(rate) / 252)
      sharpe = (mean-daily_rate) / deviation * math.sqrt(252)
  return {"total_return": values[-1]/values[0]-1, "volatility": volatility, "sharpe": sharpe, "observations": count}


def _covered(model, start, end):
  coverage = sorted(model.get("coverage", []), key=lambda item: item["period_end"])
  if not coverage:
    return False
  prior = [i for i, item in enumerate(coverage) if item["period_end"] <= start]
  first = prior[-1] if prior else 0
  relevant = [coverage[first]]
  # A quarter-end report can affect earlier dates inside that quarter because
  # this model deliberately infers trades with hindsight. Include that target,
  # then stop before transitions whose prior report is already past the window.
  for i in range(first+1, len(coverage)):
    if coverage[i-1]["period_end"] >= end:
      break
    relevant.append(coverage[i])
  for item in relevant:
    fraction = item.get("eligible_value_fraction", item.get("value_fraction"))
    if not isinstance(fraction, (int, float)) or not math.isfinite(fraction) or fraction < .9:
      return False
  return True


def _leaderboard(start, end, rate, benchmark_symbol=None, benchmark_values=None, portfolio_points=None):
  candidates, excluded = [], []
  for cached in app_tables.hedge_fund_models.search():
    model = cached["model"]
    key = cached["fund_key"]
    row = app_tables.hedge_funds.get(fund_key=key)
    if row is None or not model.get("ok"):
      continue
    if cached["fingerprint"] != _fingerprint(_data.get_snapshots(key)):
      excluded.append(row["name"] + " (model needs updating)")
      continue
    if benchmark_symbol is not None and model["benchmark_symbol"] != benchmark_symbol:
      excluded.append(row["name"] + " (different benchmark series)")
      continue
    samples = [point for point in model["series"] if start <= point["date"] <= end and point.get("session_complete", True)]
    benchmarks = {point["date"]: point for point in model["benchmark"] if point.get("session_complete", True)}
    if not samples or samples[0]["date"] != start or samples[-1]["date"] != end or not _covered(model, start, end):
      excluded.append(row["name"] + " (insufficient dates or holdings coverage)")
      continue
    candidates.append((row, model, samples, benchmarks))
  # One shared daily calendar and benchmark for every ranked profile. Profiles
  # with a hole or inactive session are excluded rather than interpolated.
  reference: Any = benchmark_values
  if reference is None and candidates:
    reference = candidates[0][3]
    benchmark_symbol = candidates[0][1]["benchmark_symbol"]
  if reference is None:
    reference = {}
  grid = sorted(day for day in (reference or {}) if start <= day <= end)
  own = {point["date"]: point for point in (portfolio_points or [])}
  own_positions = {point["date"]: i for i, point in enumerate(portfolio_points or [])}
  mask = [False]
  for i in range(1, len(grid)):
    day, previous = grid[i], grid[i-1]
    mask.append(not own or (day in own and previous in own and own[day]["return_observed"]
      and own[day]["session_complete"] and own[previous]["session_complete"]
      and own_positions[day] == own_positions[previous]+1))
  rows = []
  for row, model, samples, benchmarks in candidates:
    by_day = {point["date"]: point for point in samples}
    if (model["benchmark_symbol"] != benchmark_symbol or len(grid) < 21
        or any(day not in by_day or day not in benchmarks for day in grid)
        or any(mask[i] and not by_day[day].get("return_observed", True) for i, day in enumerate(grid) if i)):
      excluded.append(row["name"] + " (incomplete common daily sample)")
      continue
    values = [by_day[day]["index"] for day in grid]
    baseline = [reference[day]["index"] for day in grid]
    metrics, benchmark = _risk(values, mask, rate), _risk(baseline, mask, rate)
    if metrics["sharpe"] is None or benchmark["sharpe"] is None:
      excluded.append(row["name"] + " (undefined Sharpe)")
      continue
    rows.append({"fund_key": row["fund_key"], "name": row["name"], "sharpe": metrics["sharpe"],
                 "benchmark_sharpe": benchmark["sharpe"], "delta_sharpe": metrics["sharpe"]-benchmark["sharpe"],
                 "observations": metrics["observations"], "total_return": metrics["total_return"], "status": "Covered holdings model"})
  rows.sort(key=lambda item: (-item["delta_sharpe"], item["name"]))
  for index, item in enumerate(rows):
    item["rank"] = index+1
  return {"ok": True, "rows": rows, "scope": _SCOPE, "excluded": excluded, "period": {"start": start, "end": end}, "benchmark_symbol": benchmark_symbol}


@anvil.server.callable
def get_hedge_fund_leaderboard(start_date, end_date, risk_free_rate, portfolio_points=None):
  if not _ready():
    return _failure("The hedge fund library tables are not available in this environment yet.")
  try:
    start, end, rate = _date(start_date), _date(end_date), _rate(risk_free_rate)
    points = _points(portfolio_points) if portfolio_points is not None else None
    if start > end or end > date.today():
      raise ValueError("Choose increasing comparison dates ending today or earlier.")
  except (ValueError, TypeError) as error:
    return _failure(str(error))
  return _leaderboard(start.isoformat(), end.isoformat(), rate, portfolio_points=points)


@anvil.server.callable
def get_hedge_fund_comparison(fund_key, portfolio_points, risk_free_rate):
  if not _ready():
    return _failure("The hedge fund library tables are not available in this environment yet.")
  try:
    points, rate = _points(portfolio_points), _rate(risk_free_rate)
  except (ValueError, TypeError) as error:
    return _failure(str(error))
  fund = _fund(fund_key)
  if fund is None:
    return _failure("Choose a supported hedge fund profile.")
  if not _data.get_snapshots(fund_key):
    queued = _queue_update(fund_key)
    return _failure(queued["message"], job_id=queued.get("job_id"), pending=queued.get("state") == "running")
  model = _model(fund_key)
  if not model["ok"]:
    return model
  fund_days = {point["date"]: point for point in model["series"]}
  benchmark_days = {point["date"]: point for point in model["benchmark"]}
  aligned = [point for point in points if point["date"] in fund_days and point["date"] in benchmark_days
             and point["session_complete"] and fund_days[point["date"]].get("session_complete", True)
             and benchmark_days[point["date"]].get("session_complete", True)]
  if len(aligned) < 2:
    return _failure("This period does not overlap saved fund history. Choose another timeframe or update filings.")
  dates = [point["date"] for point in aligned]
  values = [point["index"] for point in aligned]
  fund_values = [fund_days[day]["index"] for day in dates]
  baseline = [benchmark_days[day]["index"] for day in dates]
  positions = {point["date"]: i for i, point in enumerate(points)}
  fund_positions = {point["date"]: i for i, point in enumerate(model["series"])}
  observed = [False]+[aligned[i]["return_observed"] and fund_days[dates[i]].get("return_observed", True)
              and positions[dates[i]] == positions[dates[i-1]]+1 and fund_positions[dates[i]] == fund_positions[dates[i-1]]+1
              for i in range(1, len(dates))]
  metrics = {"portfolio": _risk(values, observed, rate), "fund": _risk(fund_values, observed, rate), "benchmark": _risk(baseline, observed, rate)}
  board = _leaderboard(dates[0], dates[-1], rate, model["benchmark_symbol"], benchmark_days, points)
  rank = next((item for item in board["rows"] if item["fund_key"] == fund_key), None)
  warnings = list(model.get("warnings", []))
  if fund_key == "Pershing+Square+Capital+Management":
    warnings.append("Pershing history joins the prior manager filings with the public parent filings from 2026 Q2 onward; the reporting entity changed. This remains a disclosed-holdings model.")
  if dates[0] != points[0]["date"] or dates[-1] != points[-1]["date"]:
    warnings.append("Comparison uses only overlapping completed sessions; the selected portfolio window is clipped to available fund history.")
  if metrics["fund"]["observations"] < 20:
    warnings.append("At least 20 shared completed daily returns are needed for Sharpe and volatility.")
  elif metrics["fund"]["observations"] < 252:
    warnings.append("Short sample: fewer than one trading year of daily risk observations.")
  if not _covered(model, dates[0], dates[-1]):
    warnings.append("This model is unranked: less than 90% of eligible reported equity value has usable mapped price history.")
  if metrics["fund"]["observations"] < len(dates)-1:
    warnings.append("All three risk estimates and these ranks exclude the same inactive or missing portfolio intervals.")
  checked = fund["last_checked_at"]
  if checked is None or _now()-_utc(checked) >= timedelta(hours=24):
    refresh = _queue_update(fund_key)
    warnings.append(refresh["message"])
  return {"ok": True, "series": [{"date": day, "portfolio_index": values[i]/values[0]*100,
           "fund_index": fund_values[i]/fund_values[0]*100, "benchmark_index": baseline[i]/baseline[0]*100} for i, day in enumerate(dates)],
          "metrics": metrics, "benchmark_label": model["benchmark_label"], "period": {"start": dates[0], "end": dates[-1], "observations": metrics["fund"]["observations"]},
          "fund": {"fund_key": fund_key, "name": fund["name"], "manager": fund["manager"], "source_url": fund["source_url"], "hedgefollow_url": fund["hedgefollow_url"]},
          "rank": {"position": rank["rank"] if rank else None, "total": len(board["rows"]), "delta_sharpe": rank["delta_sharpe"] if rank else None, "scope": _SCOPE},
          "holdings": _data.get_snapshots(fund_key), "trades": [trade for trade in model["trades"] if dates[0] <= trade["date"] <= dates[-1]],
          "warnings": warnings, "model_note": model["method"], "last_checked_at": fund["last_checked_at"]}
