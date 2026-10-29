"""Schwab reference mixes and deterministic, cent-balanced scenario calculations."""

SOURCE_URL = "https://advisorservices.schwab.com/node/112271"
MAX_AMOUNT = 1000000000000

ASSET_CLASSES = (
  {"key": "us_large", "name": "US large-cap stocks", "hint": "Larger US companies"},
  {"key": "us_small", "name": "US small-cap stocks", "hint": "Smaller US companies"},
  {"key": "international", "name": "International stocks", "hint": "Companies outside the US"},
  {"key": "bonds", "name": "Bonds / fixed income", "hint": "Fixed-income investments"},
  {"key": "cash", "name": "Cash investments", "hint": "Cash and cash equivalents"},
)

# Source: Schwab Advisor Services, "What's your mix?", checked October 29, 2026.
# Weights follow ASSET_CLASSES order. These are reference models, not a risk assessment.
MODELS = (
  {"key": "conservative", "name": "Conservative", "horizon": "3–5 years",
   "goal": "Emphasizes income and capital preservation, with a smaller stock allocation.",
   "weights": (15, 0, 5, 50, 30)},
  {"key": "moderately_conservative", "name": "Moderately conservative", "horizon": "5–7 years",
   "goal": "Balances income and stability with some long-term growth potential.",
   "weights": (25, 5, 10, 50, 10)},
  {"key": "moderate", "name": "Moderate", "horizon": "7–10 years",
   "goal": "Blends growth potential with income and relative stability.",
   "weights": (35, 10, 15, 35, 5)},
  {"key": "moderately_aggressive", "name": "Moderately aggressive", "horizon": "10–15 years",
   "goal": "Prioritizes growth with a smaller bond allocation and more market fluctuation.",
   "weights": (45, 15, 20, 15, 5)},
  {"key": "aggressive", "name": "Aggressive", "horizon": "15+ years",
   "goal": "Focuses on long-term growth with substantial exposure to stock-market swings.",
   "weights": (50, 20, 25, 0, 5)},
)


def get_model(key):
  for model in MODELS:
    if model["key"] == key:
      return model
  raise ValueError("Choose an allocation model.")


def amount_to_cents(value, label="Amount", allow_blank=False):
  if isinstance(value, bool):
    raise ValueError("%s must be a dollar amount." % label)
  if value is None or (isinstance(value, str) and not value.strip()):
    if allow_blank:
      return 0
    raise ValueError("Enter %s in dollars." % label.lower())
  if isinstance(value, str):
    value = value.strip().replace(",", "")
  try:
    number = float(value)
  except (ValueError, TypeError, OverflowError):
    raise ValueError("%s must be a dollar amount." % label)
  # This also rejects NaN and infinities without client-runtime math extensions.
  if not 0 <= number <= MAX_AMOUNT:
    raise ValueError("%s must be between $0 and $1 trillion." % label)
  return int(round(number * 100))


def money(cents):
  return "$%s" % format(cents / 100.0, ",.2f")


def calculate_allocation(model_key, amount=None, current_balances=None):
  """Return target amounts; optional balances supply the comparison's entire total.

  Integer cents plus largest-remainder rounding keep targets equal to the total
  and comparison differences equal to zero, including very small portfolios.
  """
  model = get_model(model_key)
  comparing = current_balances is not None
  current = []
  if comparing:
    for asset in ASSET_CLASSES:
      current.append(amount_to_cents(current_balances.get(asset["key"]), asset["name"], True))
    total = sum(current)
  else:
    total = amount_to_cents(amount, "Portfolio amount")
  if total < 1:
    raise ValueError("Enter at least $0.01 across your current balances." if comparing
                     else "Enter a portfolio amount of at least $0.01.")
  if total > MAX_AMOUNT * 100:
    raise ValueError("The combined portfolio amount must be $1 trillion or less.")

  weights = model["weights"]
  targets = [(total * weight) // 100 for weight in weights]
  remainder_order = sorted(range(len(weights)), key=lambda i: -(total * weights[i] % 100))
  for index in remainder_order[:total - sum(targets)]:
    targets[index] += 1

  rows = []
  for index, asset in enumerate(ASSET_CLASSES):
    row = dict(asset)
    row.update(target_percent=weights[index], target_cents=targets[index])
    if comparing:
      row.update(current_cents=current[index], current_percent=current[index] * 100.0 / total,
                 difference_cents=targets[index] - current[index])
    rows.append(row)
  return {"model": model, "total_cents": total, "comparing": comparing, "rows": rows}
