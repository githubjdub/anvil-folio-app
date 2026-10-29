"""Transparent conversion and unlevered scenario arithmetic for the Forex workspace."""

import math


CURRENCIES = [("EUR · Euro", "EUR"), ("USD · US dollar", "USD"),
              ("GBP · British pound", "GBP"), ("JPY · Japanese yen", "JPY"),
              ("CHF · Swiss franc", "CHF"), ("CAD · Canadian dollar", "CAD"),
              ("AUD · Australian dollar", "AUD"), ("NZD · New Zealand dollar", "NZD")]
CODES = [item[1] for item in CURRENCIES]


def number(value, label, allow_zero=False, maximum=1000000000000):
  if isinstance(value, bool):
    raise ValueError(label + " must be a number.")
  try:
    result = float(value)
  except (TypeError, ValueError):
    raise ValueError(label + " must be a number.")
  if not math.isfinite(result) or result < 0 or (not allow_zero and result == 0) or result > maximum:
    raise ValueError(label + (" must be zero or greater" if allow_zero else " must be greater than zero")
                     + " and no more than {:,.0f}.".format(maximum))
  return result


def cross_rate(rates, base, quote):
  if base not in CODES or quote not in CODES:
    raise ValueError("Choose supported currencies.")
  return number(rates[quote], "Quote rate") / number(rates[base], "Base rate")


def convert(amount, rate):
  return number(amount, "Amount", allow_zero=True) * number(rate, "Reference rate")


def calculate_scenario(base, quote, direction, entry, exit_rate, units, stop, target, costs, budget):
  if base not in CODES or quote not in CODES or base == quote:
    raise ValueError("Choose two different currencies for a trade scenario.")
  if direction not in ("long", "short"):
    raise ValueError("Choose Long or Short.")
  entry = number(entry, "Entry rate", maximum=1000000)
  exit_rate = number(exit_rate, "Scenario exit rate", maximum=1000000)
  units = number(units, "Base currency units", maximum=1000000000)
  stop = number(stop, "Stop rate", maximum=1000000)
  target = number(target, "Target rate", maximum=1000000)
  if min(entry, exit_rate, stop, target) < .00000001:
    raise ValueError("Scenario rates must be at least 0.00000001.")
  costs = number(costs, "Fixed costs", allow_zero=True)
  budget = number(budget, "Risk budget")
  sign = 1 if direction == "long" else -1
  distance = (entry - stop) * sign
  reward = (target - entry) * sign
  if distance <= 0:
    raise ValueError("For a long scenario, put the stop below entry." if direction == "long"
                     else "For a short scenario, put the stop above entry.")
  if reward <= 0:
    raise ValueError("For a long scenario, put the target above entry." if direction == "long"
                     else "For a short scenario, put the target below entry.")
  gross = units * (exit_rate - entry) * sign
  risk = units * distance + costs
  target_net = units * reward - costs
  pip = .01 if quote == "JPY" else .0001
  max_units = math.floor(max(0, budget - costs) / distance)
  # Keep an exact integer boundary from losing a unit to binary float subtraction.
  if costs < budget and (max_units + 1) * distance + costs <= budget + .000000001:
    max_units += 1
  return {"gross": gross, "net": gross - costs, "stop_loss": risk,
          "target_net": target_net, "reward_risk": target_net / risk,
          "pip_size": pip, "pip_value": units * pip,
          "pips": (exit_rate - entry) * sign / pip,
          "notional": units * entry, "max_units": max_units,
          "budget": budget, "over_budget": risk > budget + .0000001,
          "quote": quote, "base": base, "costs": costs}


def cash(value, currency):
  return "{:,.2f} {}".format(value, currency)


def rate_text(value, quote):
  return ("{:.3f}" if quote == "JPY" else "{:.5f}").format(value)
