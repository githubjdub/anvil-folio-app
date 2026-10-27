"""Daily insider digests; network sends happen only after a committed claim.

SMS API: https://www.twilio.com/docs/messaging/api/message-resource
A queued response means provider acceptance, not confirmed handset delivery.
Ambiguous acceptance is never automatically retried.
"""
from datetime import datetime, timedelta, timezone
import base64
import json
import re
import socket
import uuid
from urllib import error, parse, request
from zoneinfo import ZoneInfo

import anvil.email
import anvil.secrets
from anvil.tables import app_tables, in_transaction, order_by
from anvil.tables import query as q


_PHONE = re.compile(r"\+[1-9][0-9]{7,14}\Z")
_EMAIL = re.compile(r"[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+\Z")
_SMS_SETUP = "Text delivery is not configured. Ask the platform owner to configure the SMS provider; your in-app inbox remains available."


class DeliveryError(Exception):
    """Expected, safe-to-display transport failure, with explicit retry policy."""
    def __init__(self, message, retryable=False, ambiguous=False):
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous = ambiguous


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _sms_config():
    try:
        sid, token, sender = (anvil.secrets.get_secret(name) for name in (
            "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"))
    except anvil.secrets.SecretError:
        return None
    if (not isinstance(sid, str) or not re.fullmatch(r"AC[0-9a-fA-F]{32}", sid)
            or not isinstance(token, str) or not token.strip()
            or not isinstance(sender, str) or not _PHONE.fullmatch(sender)):
        return None
    return sid, token, sender


def capabilities(user):
    email = user["email"] or ""
    return {"email": bool(_EMAIL.fullmatch(email)), "email_address": email,
            "sms": _sms_config() is not None,
            "email_setup_message": "Add a valid email address to your account to receive daily email.",
            "sms_setup_message": _SMS_SETUP}


def _send_sms(destination, body):
    config = _sms_config()
    if config is None:
        raise DeliveryError(_SMS_SETUP, retryable=True)
    sid, token, sender = config
    data = parse.urlencode({"To": destination, "From": sender, "Body": body}).encode("utf-8")
    auth = base64.b64encode((sid + ":" + token).encode("utf-8")).decode("ascii")
    req = request.Request("https://api.twilio.com/2010-04-01/Accounts/%s/Messages.json" % sid,
                          data=data, headers={"Authorization": "Basic " + auth,
                                              "Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with request.urlopen(req, timeout=20) as response:
            raw = response.read(65537)
    except error.HTTPError as failure:
        status = failure.code
        failure.close()
        if status == 429:
            raise DeliveryError("The text provider is busy. A limited retry is scheduled.", retryable=True) from None
        if 400 <= status < 500:
            raise DeliveryError("The text provider rejected this digest. Check your number or contact the platform owner.") from None
        raise DeliveryError("The text provider response was uncertain. This digest will not be resent automatically.", ambiguous=True) from None
    except (error.URLError, TimeoutError, socket.timeout, ConnectionError):
        raise DeliveryError("Text delivery could not be confirmed. This digest will not be resent automatically.", ambiguous=True) from None
    try:
        document = json.loads(raw) if len(raw) <= 65536 else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        document = None
    if not isinstance(document, dict):
        raise DeliveryError("The text provider response was incomplete. This digest will not be resent automatically.", ambiguous=True)
    provider_id = document.get("sid")
    if not isinstance(provider_id, str) or not re.fullmatch(r"(?:SM|MM)[0-9a-fA-F]{32}", provider_id):
        raise DeliveryError("The text provider response was incomplete. This digest will not be resent automatically.", ambiguous=True)
    if document.get("status") in ("failed", "undelivered", "canceled"):
        raise DeliveryError("The text provider could not deliver this digest. Review your number or contact the platform owner.")
    return provider_id


def _send_email(destination, subject, body):
    try:
        report = anvil.email.send(to=destination, from_name="Folio", subject=subject, text=body)
    except anvil.email.SendFailure:
        # The email service does not expose enough transport detail to establish
        # whether a failed call was accepted; choose duplicate prevention.
        raise DeliveryError("Email delivery could not be confirmed. This digest will not be resent automatically.", ambiguous=True) from None
    return report.message_id or "accepted"


def send_digest(channel, destination, subject, body):
    return _send_sms(destination, body) if channel == "sms" else _send_email(destination, subject, body)


def _due_date(settings, now):
    local = _utc(now).astimezone(ZoneInfo(settings["timezone"]))
    if local.strftime("%H:%M") < settings["delivery_time"]:
        return None
    if settings["last_scheduled_date"] is not None and settings["last_scheduled_date"] >= local.date():
        return None
    return local.date()


@in_transaction
def _stage_digest(user, now):
    user = app_tables.users.get_by_id(user.get_id())
    if user is None:
        return None
    settings = app_tables.notification_settings.get(owner=user)
    if not user["enabled"] or settings is None or not settings["enabled"] or settings["channel"] == "in_app":
        return None
    day = _due_date(settings, now)
    if day is None:
        return None
    channel = settings["channel"]
    existing = app_tables.notification_deliveries.get(owner=user, channel=channel, scheduled_date=day)
    if existing is not None:
        return existing
    messages = list(app_tables.notification_messages.search(
        order_by("created_at"), owner=user, delivery=None,
        created_at=q.greater_than_or_equal_to(settings["delivery_since_at"] or settings["created_at"]))[:500])
    if not messages:
        # Consume today's scheduled window even when there is no digest. New
        # filings later today belong in tomorrow's digest at the chosen time.
        settings["last_scheduled_date"] = day
        return None
    delivery = app_tables.notification_deliveries.add_row(
        owner=user, channel=channel, scheduled_date=day, created_at=now,
        status="pending", attempts=0, next_attempt_at=now, last_error="")
    for message in messages:
        message["delivery"] = delivery
    settings.update(last_scheduled_date=day, delivery_status="pending", delivery_error="")
    return delivery


@in_transaction
def _claim_delivery(delivery, now):
    # Scheduled runs may hold separate cached Row instances of this delivery.
    # Re-read inside the transaction so only the first current claim can send.
    delivery = app_tables.notification_deliveries.get_by_id(delivery.get_id())
    if delivery is None:
        return None
    owner = delivery["owner"]
    user = app_tables.users.get_by_id(owner.get_id()) if owner is not None else None
    settings = app_tables.notification_settings.get(owner=user) if user is not None else None
    if delivery["status"] not in ("pending", "retry_pending"):
        if delivery["status"] == "sending" and _utc(delivery["claimed_at"]) < now - timedelta(minutes=20):
            delivery.update(status="unknown", last_error="Delivery was interrupted after sending began. This digest will not be resent automatically.")
            if (settings is not None and settings["channel"] == delivery["channel"]
                    and _utc(settings["updated_at"]) <= _utc(delivery["created_at"])):
                settings.update(delivery_status="unknown", delivery_error=delivery["last_error"])
        return None
    if delivery["next_attempt_at"] and _utc(delivery["next_attempt_at"]) > now:
        return None
    if (user is None or not user["enabled"] or settings is None or not settings["enabled"]
            or settings["channel"] != delivery["channel"]
            or _utc(settings["updated_at"]) > _utc(delivery["created_at"])):
        delivery.update(status="cancelled", last_error="Preferences changed before this digest was sent.")
        if (settings is not None and settings["channel"] == delivery["channel"]
                and settings["last_scheduled_date"] == delivery["scheduled_date"]
                and settings["delivery_status"] in ("pending", "retry_pending")):
            settings.update(delivery_status="cancelled", delivery_error=delivery["last_error"])
        return None
    if _utc(now).astimezone(ZoneInfo(settings["timezone"])).date() != delivery["scheduled_date"]:
        delivery.update(status="failed", last_error="The delivery window expired; the messages remain in your inbox.")
        settings.update(delivery_status="failed", delivery_error=delivery["last_error"])
        return None
    destination = settings["phone"] if delivery["channel"] == "sms" else user["email"]
    if (delivery["channel"] == "sms" and (not settings["sms_consent_at"] or not _PHONE.fullmatch(destination or ""))) or (delivery["channel"] == "email" and not _EMAIL.fullmatch(destination or "")):
        delivery.update(status="failed", last_error="Update your delivery details in Notifications.")
        settings.update(delivery_status="failed", delivery_error=delivery["last_error"])
        return None
    token = uuid.uuid4().hex
    delivery.update(status="sending", attempts=(delivery["attempts"] or 0) + 1,
                    claimed_at=now, attempt_id=token, last_error="")
    settings.update(delivery_status="sending", delivery_error="")
    return {"attempt_id": token, "channel": delivery["channel"], "destination": destination,
            "messages": list(app_tables.notification_messages.search(order_by("created_at"), delivery=delivery, owner=user))}


def _digest_text(messages, channel):
    subject = "Folio: %s new insider transaction%s" % (len(messages), "" if len(messages) == 1 else "s")
    if channel == "sms":
        counts = {}
        for row in messages:
            symbol = row["symbol"]
            counts.setdefault(symbol, {"buy": 0, "sell": 0})[row["action"]] += 1
        details = ["%s %sB/%sS" % (symbol, count["buy"], count["sell"]) for symbol, count in sorted(counts.items())]
        body = "%s. %s" % (subject, "; ".join(details[:6]))
        if len(details) > 6:
            body += "; +%s more securities" % (len(details) - 6)
        return subject, body[:350] + ". Details in your Folio inbox. Reply STOP to opt out."
    lines = [subject, "", "New SEC Form 4 purchase/sale disclosures for securities you watch.", ""]
    for row in messages[:30]:
        lines.extend([row["title"], row["body"], row["filing_url"], ""])
    if len(messages) > 30:
        lines.append("%s more transactions are in your Folio inbox." % (len(messages) - 30))
    lines.extend(["Manage alert preferences or pause delivery in Folio > Notifications.",
                  "Filing dates can be later than transaction dates."])
    return subject, "\n".join(lines)


@in_transaction
def _finish_delivery(delivery, token, now, provider_id=None, failure=None):
    delivery = app_tables.notification_deliveries.get_by_id(delivery.get_id())
    if delivery is None:
        return
    if delivery["attempt_id"] != token or delivery["status"] != "sending":
        return
    if failure is None:
        status, message = "accepted", "Accepted by the delivery provider; receipt is not confirmed."
        delivery.update(status=status, provider_id=str(provider_id or "accepted"), last_error="")
        delivery["next_attempt_at"] = None
        for row in app_tables.notification_messages.search(delivery=delivery, owner=delivery["owner"]):
            row["delivered_at"] = now
    else:
        status = "unknown" if failure.ambiguous else ("retry_pending" if failure.retryable and delivery["attempts"] < 3 else "failed")
        message = str(failure)[:300]
        delivery.update(status=status, last_error=message)
        delivery["next_attempt_at"] = now + timedelta(minutes=5) if status == "retry_pending" else None
    settings = app_tables.notification_settings.get(owner=delivery["owner"])
    if settings is not None and settings["channel"] == delivery["channel"] and _utc(settings["updated_at"]) <= _utc(delivery["created_at"]):
        settings.update(delivery_status=status, delivery_error=message)


def deliver_due(user, now, sender=None):
    """Private injection point for isolated tests; never exposed to clients."""
    sender = sender or send_digest
    _stage_digest(user, now)
    attempts = 0
    for delivery in app_tables.notification_deliveries.search(owner=user, status=q.any_of("pending", "retry_pending", "sending")):
        claim = _claim_delivery(delivery, now)
        if claim is None:
            continue
        subject, body = _digest_text(claim["messages"], claim["channel"])
        try:
            provider_id = sender(claim["channel"], claim["destination"], subject, body)
        except DeliveryError as failure:
            _finish_delivery(delivery, claim["attempt_id"], now, failure=failure)
        else:
            _finish_delivery(delivery, claim["attempt_id"], now, provider_id=provider_id)
        attempts += 1
    return attempts
