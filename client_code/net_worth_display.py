"""Display helpers for the account balance sheet."""

ASSET_CATEGORIES = [("Cash & savings", "cash"), ("Investments", "investments"),
                    ("Retirement accounts", "retirement"), ("Property", "property"),
                    ("Other assets", "other_asset")]
DEBT_CATEGORIES = [("Mortgage", "mortgage"), ("Loans", "loan"),
                   ("Credit cards", "credit_card"), ("Other debts", "other_debt")]
CATEGORY_LABELS = {value: label for label, value in ASSET_CATEGORIES + DEBT_CATEGORIES}


def money(value, signed=False):
  prefix = "−" if value < 0 else "+" if signed and value > 0 else ""
  return prefix + "${:,.2f}".format(abs(value))


def category_label(value):
  return CATEGORY_LABELS.get(value, value)


def entry_detail(row):
  detail = category_label(row["category"])
  if row["source_key"] == "selected_portfolio":
    detail += " · Portfolio import"
  if row["as_of"] is not None:
    detail += " · Imported value as of " + str(row["as_of"])
  else:
    detail += " · Updated " + str(row["updated_at"].date())
  return detail
