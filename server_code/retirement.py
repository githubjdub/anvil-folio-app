"""Illustrative retirement projections in today's dollars.

The fixed seed keeps comparisons stable when an assumption changes. Returns
are independent monthly lognormal draws calibrated to the supplied annual
arithmetic mean and standard deviation; they are not historical forecasts.
"""

import math
import random

import anvil.server

_PATHS = 2000
_SEED = 20261019
_FIELDS = {
    "current_age": ("Current age", 18, 100),
    "retirement_age": ("Retirement age", 18, 100),
    "end_age": ("Plan through age", 19, 110),
    "starting_balance": ("Current savings", 0, 100000000),
    "monthly_savings": ("Monthly savings", 0, 1000000),
    "monthly_spending": ("Monthly retirement spending", 0, 1000000),
    "monthly_income": ("Monthly retirement income", 0, 1000000),
    "expected_return": ("Expected annual return", -20, 30),
    "volatility": ("Annual volatility", 0, 60),
    "inflation": ("Annual inflation", 0, 15),
    "annual_fee": ("Annual investment fee", 0, 5),
    "bequest_goal": ("Ending balance goal", 0, 100000000),
}
_AGES = {"current_age", "retirement_age", "end_age"}


class _InputError(Exception):
    pass


def _validated_inputs(inputs):
    if not isinstance(inputs, dict):
        raise _InputError("Enter your retirement assumptions before running the projection.")
    validated = {}
    for name, (label, lower, upper) in _FIELDS.items():
        value = inputs.get(name)
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise _InputError("%s must be a number." % label)
        try:
            value = float(value)
        except (ValueError, TypeError, OverflowError):
            raise _InputError("%s must be a finite number." % label)
        if not math.isfinite(value):
            raise _InputError("%s must be a finite number." % label)
        if name in _AGES and not value.is_integer():
            raise _InputError("%s must be a whole number." % label)
        if not lower <= value <= upper:
            raise _InputError("%s must be between %s and %s." % (label, lower, upper))
        validated[name] = int(value) if name in _AGES else value
    if validated["retirement_age"] < validated["current_age"]:
        raise _InputError("Retirement age must be your current age or later.")
    if validated["end_age"] <= validated["retirement_age"]:
        raise _InputError("Plan through age must be later than retirement age.")
    if validated["end_age"] - validated["current_age"] > 90:
        raise _InputError("The projection can cover up to 90 years.")
    return validated


def _monthly_parameters(inputs):
    """Convert annual arithmetic return moments to monthly real log returns."""
    mean = inputs["expected_return"] / 100.0
    deviation = inputs["volatility"] / 100.0
    log_variance = math.log1p((deviation / (1.0 + mean)) ** 2)
    drift = (
        math.log1p(mean) - log_variance / 2.0
        + math.log1p(-inputs["annual_fee"] / 100.0)
        - math.log1p(inputs["inflation"] / 100.0)
    ) / 12.0
    return drift, math.sqrt(log_variance / 12.0)


def _tolerance(left, right):
    # Only absorb rounding at the scale of the compared amounts. Even at the
    # largest permitted inputs this remains far below a cent.
    return max(1e-9, 8.0 * math.ulp(left), 8.0 * math.ulp(right))


def _percentile(sorted_values, fraction):
    position = (len(sorted_values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


@anvil.server.callable
def run_retirement_projection(inputs):
    """Run 2,000 reproducible paths; report funding and ending-goal attainment.

    Contributions arrive at month end before retirement. Retirement spending
    less income is paid at month start. Any income surplus is invested then.
    A missed withdrawal permanently fails the spending test. Annual balances
    include every simulated path, including paths that previously ran short.
    """
    try:
        assumptions = _validated_inputs(inputs)
    except _InputError as error:
        return {"ok": False, "message": str(error)}

    current_age = assumptions["current_age"]
    years_count = assumptions["end_age"] - current_age
    retirement_month = (assumptions["retirement_age"] - current_age) * 12
    total_months = years_count * 12
    drift, sigma = _monthly_parameters(assumptions)
    fixed_growth = math.exp(drift)
    generator = random.Random(_SEED)
    normal = generator.gauss
    starting_balance = assumptions["starting_balance"]
    monthly_savings = assumptions["monthly_savings"]
    withdrawal = assumptions["monthly_spending"] - assumptions["monthly_income"]
    goal = assumptions["bequest_goal"]
    annual_balances = [[] for _ in range(years_count + 1)]
    retirement_balances = []
    first_shortfalls = []
    funded_count = 0
    successful_count = 0

    for _ in range(_PATHS):
        balance = starting_balance
        funded = True
        annual_balances[0].append(balance)
        if retirement_month == 0:
            retirement_balances.append(balance)
        for month in range(total_months):
            if month >= retirement_month:
                if withdrawal > 0 and balance + _tolerance(balance, withdrawal) < withdrawal:
                    if funded:
                        first_shortfalls.append(current_age + month / 12.0)
                    funded = False
                    balance = 0.0
                else:
                    balance = max(0.0, balance - withdrawal)
            growth = math.exp(drift + sigma * normal(0.0, 1.0)) if sigma else fixed_growth
            balance *= growth
            if month < retirement_month:
                balance += monthly_savings
            completed_months = month + 1
            if completed_months == retirement_month:
                retirement_balances.append(balance)
            if completed_months % 12 == 0:
                annual_balances[completed_months // 12].append(balance)
        if funded:
            funded_count += 1
            if balance + _tolerance(balance, goal) >= goal:
                successful_count += 1

    summaries = []
    for year, values in enumerate(annual_balances):
        values.sort()
        row = {"age": current_age + year}
        for name, fraction in (("p10", 0.1), ("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9)):
            row[name] = _percentile(values, fraction)
        summaries.append(row)
    retirement_balances.sort()
    first_shortfalls.sort()
    ending = summaries[-1]
    return {
        "ok": True,
        "inputs": assumptions,
        "paths": _PATHS,
        "success_rate": successful_count / _PATHS,
        "funded_rate": funded_count / _PATHS,
        "retirement_p50": _percentile(retirement_balances, 0.5),
        "ending_p10": ending["p10"],
        "ending_p50": ending["p50"],
        "ending_p90": ending["p90"],
        "monthly_gap": max(0.0, withdrawal),
        "years": summaries,
        "failed_count": _PATHS - funded_count,
        "median_first_shortfall_age": _percentile(first_shortfalls, 0.5) if first_shortfalls else None,
    }
