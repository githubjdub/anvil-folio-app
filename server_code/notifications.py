"""Owner-scoped insider alert rules, live-row inbox and scheduled polling."""
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anvil.server
import anvil.users
from anvil.tables import app_tables, in_transaction, order_by
from anvil.tables import query as q

from . import insider_source, notification_delivery

_MAX_WATCHES = 25
_PAGE_SIZE = 25
_ACTIVITIES = ("both", "buys", "sells")


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _user():
    user = anvil.users.get_user()
    if user is None or user["enabled"] is not True:
        raise anvil.users.AuthenticationFailed("Sign in to an enabled account to use notifications.")
    return user


def _owned(table, row_id, user):
    if not isinstance(row_id, str) or not 1 <= len(row_id) <= 150:
        return None
    try:
        row = table.get_by_id(row_id)
    except ValueError:
        return None
    return row if row is not None and row["owner"] == user else None


@in_transaction
def _settings(user, now):
    settings = app_tables.notification_settings.get(owner=user)
    if settings is None:
        settings = app_tables.notification_settings.add_row(
            owner=user, enabled=True, channel="in_app", delivery_time="18:00", timezone="UTC",
            phone="", created_at=now, updated_at=now, delivery_since_at=now,
            delivery_status="", delivery_error="")
    return settings


@anvil.server.callable(require_user=True)
def get_notification_unread_count():
    """Small owner-scoped response for the navigation badge on any tab."""
    return len(app_tables.notification_messages.search(owner=_user(), read_at=None))


@anvil.server.callable(require_user=True)
def get_notification_workspace(before=None):
    user = _user()
    settings = _settings(user, _now())
    query = []
    if before is not None:
        cursor = _owned(app_tables.notification_messages, before, user)
        if cursor is None:
            raise anvil.users.AuthenticationFailed("That inbox page is unavailable. Refresh your inbox.")
        query.append(q.any_of(
            q.all_of(created_at=q.less_than(cursor["created_at"])),
            q.all_of(created_at=cursor["created_at"], event_id=q.less_than(cursor["event_id"]))))
    messages = list(app_tables.notification_messages.search(
        *query, order_by("created_at", ascending=False), order_by("event_id", ascending=False), owner=user)[:_PAGE_SIZE + 1])
    next_cursor = messages[_PAGE_SIZE - 1].get_id() if len(messages) > _PAGE_SIZE else None
    return {"settings": settings, "capabilities": notification_delivery.capabilities(user),
            "watches": list(app_tables.insider_watches.search(order_by("symbol"), owner=user, deleted=False)),
            "messages": messages[:_PAGE_SIZE], "next_cursor": next_cursor,
            "unread_count": len(app_tables.notification_messages.search(owner=user, read_at=None))}


@in_transaction
def _add_watch(user, symbol, activity, now):
    _settings(user, now)
    existing = app_tables.insider_watches.get(owner=user, symbol=symbol)
    if existing is not None and not existing["deleted"]:
        return {"ok": False, "message": "%s is already on your watchlist." % symbol}
    if len(app_tables.insider_watches.search(owner=user, deleted=False)) >= _MAX_WATCHES:
        return {"ok": False, "message": "You can watch up to 25 securities. Remove one before adding another."}
    if existing is not None:
        existing.update(activity=activity, active=True, deleted=False, since_at=now, last_error="")
        existing["last_checked_at"] = None
        existing["poll_next_since"] = None
    else:
        app_tables.insider_watches.add_row(owner=user, symbol=symbol, created_at=now,
                                         activity=activity, active=True, deleted=False,
                                         since_at=now, last_error="")
    return {"ok": True, "message": "Watching future %s filings. Existing trades are not backfilled." % symbol}


@anvil.server.callable(require_user=True)
def add_insider_watch(symbol, activity="both"):
    user = _user()
    if activity not in _ACTIVITIES:
        return {"ok": False, "message": "Choose buying, selling, or both."}
    try:
        issuer = insider_source.resolve_insider_symbol(symbol)
    except insider_source.InsiderSourceError as failure:
        return {"ok": False, "message": str(failure)[:300]}
    return _add_watch(user, issuer["symbol"], activity, _now())


@anvil.server.callable(require_user=True)
@in_transaction
def update_insider_watch(watch_id, activity, active):
    user = _user()
    if activity not in _ACTIVITIES or type(active) is not bool:
        return {"ok": False, "message": "Choose a valid activity and watch status."}
    watch = _owned(app_tables.insider_watches, watch_id, user)
    if watch is None or watch["deleted"]:
        return {"ok": False, "message": "This watch is unavailable. Refresh the watchlist."}
    if (active and not watch["active"]) or activity != watch["activity"]:
        watch.update(since_at=_now(), last_error="")
        watch["last_checked_at"] = None
        watch["poll_next_since"] = None
    watch.update(activity=activity, active=active)
    return {"ok": True, "message": "Watch updated. New matching filings will appear after scheduled checks." if active else "Watch paused."}


@anvil.server.callable(require_user=True)
@in_transaction
def remove_insider_watch(watch_id):
    watch = _owned(app_tables.insider_watches, watch_id, _user())
    if watch is None:
        return {"ok": False, "message": "This watch is unavailable. Refresh the watchlist."}
    watch.update(active=False, deleted=True)
    return {"ok": True, "message": "Security removed. Previous inbox messages are kept."}


@anvil.server.callable(require_user=True)
def save_notification_preferences(enabled, channel, delivery_time, timezone_name, phone, sms_consent):
    user = _user()
    if type(enabled) is not bool or type(sms_consent) is not bool or channel not in ("in_app", "email", "sms"):
        return {"ok": False, "message": "Choose valid alert and delivery preferences."}
    if not isinstance(delivery_time, str) or not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", delivery_time.strip()):
        return {"ok": False, "message": "Enter a daily delivery time in 24-hour HH:MM format, such as 18:00."}
    if not isinstance(timezone_name, str) or len(timezone_name) > 80:
        return {"ok": False, "message": "Enter a timezone such as America/New_York or Europe/London."}
    timezone_name = timezone_name.strip()
    try:
        ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, ValueError):
        return {"ok": False, "message": "Enter a valid timezone such as America/New_York or Europe/London."}
    if not isinstance(phone, str) or len(phone) > 50:
        return {"ok": False, "message": "Enter a valid mobile number including its country code."}
    phone = phone.strip()
    if channel == "sms" and enabled:
        if not notification_delivery._PHONE.fullmatch(phone):
            return {"ok": False, "message": "Use international phone format with a + and country code, such as +14155552671."}
        if not sms_consent:
            return {"ok": False, "message": "Confirm that you consent to receive text alerts at this number."}
        if not notification_delivery.capabilities(user)["sms"]:
            return {"ok": False, "message": notification_delivery._SMS_SETUP}
    if channel == "email" and enabled and not notification_delivery.capabilities(user)["email"]:
        return {"ok": False, "message": "A valid account email is required for email digests."}
    return _save_preferences(user, enabled, channel, delivery_time.strip(), timezone_name, phone, sms_consent, _now())


@in_transaction
def _save_preferences(user, enabled, channel, delivery_time, timezone_name, phone, sms_consent, now):
    settings = _settings(user, now)
    resumed = enabled and not settings["enabled"]
    changed_destination = channel != settings["channel"] or phone != settings["phone"]
    if resumed:
        for watch in app_tables.insider_watches.search(owner=user, deleted=False):
            watch.update(since_at=now, last_error="")
            watch["last_checked_at"] = None
            watch["poll_next_since"] = None
    consent_at = settings["sms_consent_at"]
    if not sms_consent or channel != "sms":
        consent_at = None
    elif phone != settings["phone"] or consent_at is None:
        consent_at = now
    if resumed or changed_destination:
        settings.update(delivery_since_at=now, delivery_status="", delivery_error="")
    settings.update(enabled=enabled, channel=channel, delivery_time=delivery_time,
                    timezone=timezone_name, phone=phone, updated_at=now)
    settings["sms_consent_at"] = consent_at
    return {"ok": True, "message": "Notification preferences saved." if enabled else "All insider alerts are paused."}


@anvil.server.callable(require_user=True)
@in_transaction
def mark_notification_read(message_id, read=True):
    if type(read) is not bool:
        return {"ok": False, "message": "Choose read or unread."}
    row = _owned(app_tables.notification_messages, message_id, _user())
    if row is None:
        return {"ok": False, "message": "This message is unavailable. Refresh your inbox."}
    row["read_at"] = _now() if read else None
    return {"ok": True, "message": "Message marked as read." if read else "Message marked as unread."}


@anvil.server.callable(require_user=True)
@in_transaction
def mark_all_notifications_read():
    user, now = _user(), _now()
    for row in app_tables.notification_messages.search(owner=user, read_at=None):
        row["read_at"] = now
    return {"ok": True, "message": "All current messages marked as read."}


def _watch_snapshot(watch):
    return (watch["since_at"], watch["activity"], watch["last_checked_at"], watch["poll_next_since"])


@in_transaction
def _record_poll(watch, snapshot, result, now, failure=None):
    # Source requests can outlive a pause, removal or account change. Read the
    # current rows inside the transaction rather than the pre-request cache.
    watch = app_tables.insider_watches.get_by_id(watch.get_id())
    if watch is None or watch["owner"] is None:
        return 0
    user = app_tables.users.get_by_id(watch["owner"].get_id())
    if user is None:
        return 0
    settings = app_tables.notification_settings.get(owner=user)
    if (not user["enabled"] or not watch["active"] or watch["deleted"]
            or settings is None or not settings["enabled"] or _watch_snapshot(watch) != snapshot):
        return 0
    if failure is not None:
        watch["last_error"] = str(failure)[:300]
        return 0
    count = 0
    for event in result["events"]:
        if (_utc(event["filed_at"]) < _utc(watch["since_at"])
                or event["action"] not in ("buy", "sell")
                or (watch["activity"] == "buys" and event["action"] != "buy")
                or (watch["activity"] == "sells" and event["action"] != "sell")):
            continue
        if app_tables.notification_messages.get(owner=user, event_id=event["id"]) is not None:
            continue
        action = "bought" if event["action"] == "buy" else "sold"
        title = "%s: %s %s shares" % (watch["symbol"], event["insider_name"], action)
        body = "%s disclosed a %s of %s on %s (filed %s UTC)." % (
            event["insider_name"], "purchase" if event["action"] == "buy" else "sale",
            event["security_title"], event["transaction_date"], _utc(event["filed_at"]).strftime("%Y-%m-%d %H:%M"))
        app_tables.notification_messages.add_row(
            owner=user, watch=watch, event_id=event["id"], symbol=watch["symbol"], action=event["action"],
            title=title, body=body, transaction_date=event["transaction_date"], filed_at=event["filed_at"],
            created_at=now, shares=event["shares"], price=event["price"], value=event["value"],
            filing_url=event["filing_url"])
        count += 1
    watch.update(last_checked_at=result["checked_at"], last_error="")
    watch["poll_next_since"] = result["next_since"] if result["has_more"] else None
    return count


def _process_user(user, now, fetcher=None, sender=None):
    """Private test seam; production task always uses the actual source/transport."""
    settings = app_tables.notification_settings.get(owner=user)
    if user["enabled"] is not True or settings is None or not settings["enabled"]:
        return {"messages": 0, "delivery_attempts": 0, "source_errors": 0}
    fetcher = fetcher or insider_source.fetch_insider_transactions
    count, errors = 0, 0
    for watch in app_tables.insider_watches.search(owner=user, active=True, deleted=False):
        snapshot = _watch_snapshot(watch)
        since = watch["poll_next_since"]
        if since is None:
            since = max(_utc(watch["since_at"]), _utc(watch["last_checked_at"] or watch["since_at"]) - timedelta(days=1))
        if _utc(since) > now:
            continue
        try:
            result = fetcher(watch["symbol"], since)
        except insider_source.InsiderSourceError as failure:
            _record_poll(watch, snapshot, None, now, failure=failure)
            errors += 1
        else:
            count += _record_poll(watch, snapshot, result, now)
    return {"messages": count, "source_errors": errors,
            "delivery_attempts": notification_delivery.deliver_due(user, now, sender=sender)}


@anvil.server.background_task
def process_insider_notifications():
    totals = {"messages": 0, "delivery_attempts": 0, "source_errors": 0}
    for settings in app_tables.notification_settings.search(enabled=True):
        result = _process_user(settings["owner"], _now())
        for key in totals:
            totals[key] += result[key]
    return totals
