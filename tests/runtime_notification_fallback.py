"""Temporary-database checks: anvil repl - < tests/runtime_notification_fallback.py.

All SEC requests and outbound transports are replaced; this never sends messages.
"""
from datetime import datetime, timedelta, timezone
import uuid

import anvil.users
from anvil.tables import app_tables
import notifications
import notification_delivery
import insider_source

original_user = anvil.users.get_user
original_fetch = insider_source.fetch_insider_transactions_step
original_deliver = notification_delivery.deliver_due
original_caps = notification_delivery.capabilities
original_now = notifications._now
users = []
tag = uuid.uuid4().hex[:12]
now = datetime.now(timezone.utc)
clock = [now]
calls = []


def forbidden(*args, **kwargs):
    raise AssertionError("Inbox fallback attempted outbound delivery")


def source(symbol, since, progress=None):
    calls.append((symbol, since))
    if symbol == "BBB":
        raise insider_source.InsiderSourceError("Fixture source temporarily unavailable")
    event = dict(id=tag + "-" + symbol, filed_at=now - timedelta(days=1),
                 action="buy", insider_name="Fixture Insider", security_title="Common Stock",
                 transaction_date=(now - timedelta(days=2)).date(), shares=10, price=5, value=50,
                 filing_url="https://www.sec.gov/Archives/fixture")
    return dict(events=[event], checked_at=clock[0], has_more=False, next_since=None)


try:
    for number in range(2):
        users.append(app_tables.users.add_row(email="notification-fixture-%s-%s@example.invalid" % (tag, number),
                                             enabled=True, signed_up=now))
    notification_delivery.deliver_due = forbidden
    notification_delivery.capabilities = lambda user: dict(email=True, email_address=user["email"], sms=False)
    insider_source.fetch_insider_transactions_step = source
    notifications._now = lambda: clock[0]
    anvil.users.get_user = lambda: users[0]
    for symbol in ("AAA", "BBB", "CCC"):
        assert notifications._add_watch(users[0], symbol, "both", now - timedelta(days=5))["ok"]
    notifications._settings(users[0], now).update(channel="email")
    assert notifications._add_watch(users[1], "ZZZ", "both", now - timedelta(days=5))["ok"]

    workspace = notifications.get_notification_workspace(check_for_updates=True)
    assert workspace["polling"]["checked"] == 1, workspace["polling"]
    assert workspace["polling"]["messages"] == 1 and workspace["polling"]["source_errors"] == 0
    assert [item[0] for item in calls] == ["AAA"]
    assert len(workspace["messages"]) == 1 and workspace["unread_count"] == 1
    assert "paused" in workspace["delivery_limit"]
    assert not len(app_tables.notification_messages.search(owner=users[1]))
    assert not len(app_tables.notification_deliveries.search(owner=users[0]))
    print("PASS: owner-only one-step batch and inbox creation with no outbound delivery")

    before = len(calls)
    assert notifications.get_notification_workspace(check_for_updates=True)["polling"]["status"] == "cooldown"
    notifications.get_notification_workspace()
    assert len(calls) == before
    clock[0] += timedelta(seconds=61)
    second = notifications.get_notification_workspace(check_for_updates=True)
    assert second["polling"]["source_errors"] == 1
    assert app_tables.insider_watches.get(owner=users[0], symbol="BBB")["last_error"]
    clock[0] += timedelta(seconds=61)
    third = notifications.get_notification_workspace(check_for_updates=True)
    assert [item[0] for item in calls] == ["AAA", "BBB", "CCC"]
    assert third["polling"]["messages"] == 1
    assert third["unread_count"] == 2
    clock[0] += timedelta(minutes=16)
    repeated = notifications.get_notification_workspace(check_for_updates=True)
    assert repeated["polling"]["messages"] == 0
    assert len(app_tables.notification_messages.search(owner=users[0])) == 2
    assert not len(app_tables.notification_deliveries.search(owner=users[0]))
    print("PASS: durable cooldown, fair rotation to remaining watch and overlapping-source event deduplication")

    clock[0] += timedelta(minutes=16)
    claim = notifications._claim_visit(users[0], clock[0])
    assert claim["status"] == "claimed"
    assert notifications._claim_visit(users[0], clock[0])["status"] == "busy"
    notifications._release_visit(users[0], "stale-token")
    assert notifications._claim_visit(users[0], clock[0])["status"] == "busy"
    notifications._release_visit(users[0], claim["token"])
    assert not (app_tables.notification_settings.get(owner=users[0])["poll_state"] or {}).get("token")
    # A server interruption leaves a lease that another ordinary request recovers.
    clock[0] += timedelta(minutes=16)
    abandoned = notifications._claim_visit(users[0], clock[0])
    assert abandoned["status"] == "claimed"
    clock[0] += timedelta(minutes=16)
    replacement = notifications._claim_visit(users[0], clock[0])
    assert replacement["status"] == "claimed" and replacement["token"] != abandoned["token"]
    notifications._release_visit(users[0], replacement["token"])
    print("PASS: concurrent-tab lease, stale completion protection and abandoned-lease recovery")

    # Pauses/removals during a source request must invalidate its write.
    clock[0] += timedelta(minutes=16)
    def pause_during_source(symbol, since, progress=None):
        result = source(symbol, since)
        app_tables.insider_watches.get(owner=users[0], symbol=symbol).update(active=False)
        return result
    count_before = len(app_tables.notification_messages.search(owner=users[0]))
    notifications._poll_on_visit(users[0], clock[0], fetcher=pause_during_source)
    assert len(app_tables.notification_messages.search(owner=users[0])) == count_before
    settings = app_tables.notification_settings.get(owner=users[0])
    settings["enabled"] = False
    before = len(calls)
    assert notifications.get_notification_workspace(check_for_updates=True)["polling"]["status"] == "paused"
    assert len(calls) == before
    print("PASS: source results ignored after a concurrent pause; disabled alerts perform no source requests")

    # A source timeout must preserve continuation and never advance past unread filings.
    settings["enabled"] = True
    for watch in app_tables.insider_watches.search(owner=users[0]):
        watch["active"] = watch["symbol"] == "AAA"
    watch = app_tables.insider_watches.get(owner=users[0], symbol="AAA")
    stable = watch["last_checked_at"]
    def partial(symbol, since, progress=None):
        return dict(events=[], progress=dict(stage="filing", fixture=tag))
    clock[0] += timedelta(minutes=16)
    notifications._poll_on_visit(users[0], clock[0], fetcher=partial)
    assert watch["last_checked_at"] == stable
    assert watch["poll_progress"] == dict(stage="filing", fixture=tag)
    def timeout(symbol, since, progress=None):
        assert progress == dict(stage="filing", fixture=tag)
        raise insider_source.InsiderSourceError("Fixture bounded request timeout")
    clock[0] += timedelta(seconds=61)
    interrupted = notifications._poll_on_visit(users[0], clock[0], fetcher=timeout)
    assert interrupted["source_errors"] == 1
    assert watch["last_checked_at"] == stable and watch["poll_progress"] == dict(stage="filing", fixture=tag)
    clock[0] += timedelta(minutes=16)
    resumed = notifications._poll_on_visit(users[0], clock[0], fetcher=source)
    watch = app_tables.insider_watches.get(owner=users[0], symbol="AAA")
    assert watch["poll_progress"] is None and watch["last_checked_at"] > stable, (resumed, watch["poll_progress"], watch["last_checked_at"], stable)
    assert len(app_tables.notification_messages.search(owner=users[0])) == count_before
    print("PASS: partial source progress persists across calls and timeout; stable cursor advances only after completion")

    message = list(app_tables.notification_messages.search(owner=users[0]))[0]
    anvil.users.get_user = lambda: users[1]
    assert not notifications.mark_notification_read(message.get_id())["ok"]
    assert not notifications.update_insider_watch(app_tables.insider_watches.get(owner=users[0], symbol="AAA").get_id(), "both", True)["ok"]
    other = notifications.get_notification_workspace(check_for_updates=True)
    assert len(other["messages"]) == 1 and other["messages"][0]["symbol"] == "ZZZ"
    assert len(app_tables.notification_messages.search(owner=users[0])) == count_before
    assert not len(app_tables.notification_deliveries.search(owner=users[1]))
    users[1]["enabled"] = False
    try:
        notifications.get_notification_workspace(check_for_updates=True)
        raise AssertionError("Disabled account was accepted")
    except anvil.users.AuthenticationFailed:
        pass
    print("PASS: cross-account read/watch mutation rejected, own checks isolated and disabled-account access rejected")
finally:
    anvil.users.get_user = original_user
    insider_source.fetch_insider_transactions_step = original_fetch
    notification_delivery.deliver_due = original_deliver
    notification_delivery.capabilities = original_caps
    notifications._now = original_now
    for user in users:
        for table in (app_tables.notification_messages, app_tables.notification_deliveries,
                      app_tables.insider_watches, app_tables.notification_settings):
            for row in list(table.search(owner=user)):
                row.delete()
        user.delete()
    print("Notification fixtures cleaned up")
