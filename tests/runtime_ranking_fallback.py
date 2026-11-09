"""Disposable server check: anvil repl - < tests/runtime_ranking_fallback.py."""
from datetime import datetime, timedelta, timezone
import copy
import uuid

import anvil.users
from anvil.tables import app_tables
import investor_profiles as rankings
import portfolios
import memberships
import stripe_billing
import creator_portfolios


tag = uuid.uuid4().hex[:10]
quarter = rankings.previous_quarter()[2]
snapshot = app_tables.profile_rankings.get(quarter=quarter)
backup = ({'participants': copy.deepcopy(snapshot['participants']),
           'generated_at': snapshot['generated_at'],
           'refresh_state': copy.deepcopy(snapshot['refresh_state'])} if snapshot is not None else None)
get_user = anvil.users.get_user
get_directory = portfolios._portfolios_for
get_subscriptions = stripe_billing._subscriptions
require_creator = creator_portfolios._require_creator
created_users, created_profiles = [], []
now = datetime.now(timezone.utc)


def finish_cycle():
    for _ in range(150):
        result = rankings.advance_profile_rankings(bypass_step_cooldown=True)
        if not result['pending']:
            return result
    raise AssertionError('Ranking cycle failed to finish')


try:
    for index in range(9):
        user = app_tables.users.add_row(email='ranking-fallback-%s-%s@example.invalid' % (tag, index),
            enabled=index != 5, membership_tier='premium', membership_source='creator')
        created_users.append(user)
        if index == 4:
            user['membership_expires_at'] = now - timedelta(seconds=1)
        if index == 6:
            user.update(membership_tier='free', membership_source=None)
        profile = rankings._profile_for(user)
        profile.update(primary_portfolio_id='fixture-' + str(index), ranking_opt_in=index != 8,
            ranking_key=None if index < 2 else profile['ranking_key'],
            metrics={'quarter': quarter, 'quarter_label': quarter, 'schema_version': 1,
                'return': None if index == 7 else .1, 'portfolio_name': 'Private fixture',
                'value': 100, 'holding_count': 1, 'holdings': [], 'note': 'Fixture'})
        created_profiles.append(profile)
    portfolios._portfolios_for = lambda user: [{'portfolio_id': 'fixture-' + str(created_users.index(user))}]
    snapshot = app_tables.profile_rankings.get(quarter=quarter)
    if snapshot is None:
        snapshot = app_tables.profile_rankings.add_row(quarter=quarter)
    previous = [{'user_id': 'previous-fixture', 'return': .4, 'valid_until': None}]
    old_stamp = now - timedelta(hours=2)
    snapshot.update(participants=previous, generated_at=old_stamp, refresh_state={})
    first = rankings.advance_profile_rankings()
    assert first['pending'] and snapshot['participants'] == previous
    assert snapshot['generated_at'] == old_stamp
    assert rankings._claim_ranking_batch(quarter) is None, 'Persistent step cooldown ignored'
    assert finish_cycle()['updated_at'] > old_stamp
    participant_ids = {p['user_id'] for p in snapshot['participants']}
    assert {u.get_id() for u in created_users[:4]} <= participant_ids
    assert not participant_ids.intersection(u.get_id() for u in created_users[4:])
    assert rankings._claim_ranking_batch(quarter, True) is None, 'Completed snapshot cooldown ignored'
    print('PASS: live legacy-key migration, bounded pagination, persisted cooldown, complete snapshot publication, verified/disabled/expired/opt-out eligibility')

    # Keep this assertion independent of any preexisting live cohort.
    snapshot['participants'] = [{'user_id': u.get_id(), 'return': .1, 'valid_until': None} for u in created_users[:4]]
    for user in created_users[:4]:
        anvil.users.get_user = lambda user=user: user
        result = rankings.profile_summary_for(user)['ranking']
        assert result['quartile'] == 2 and result['percentile'] == 50 and result['rank'] == 1
        assert 'participants' not in result and 'user_id' not in result
    peers = list(snapshot['participants'])
    peers[-1]['valid_until'] = now.timestamp() - 1
    snapshot['participants'] = peers
    assert rankings.profile_summary_for(created_users[0])['ranking']['quartile'] is None
    assert rankings.profile_summary_for(created_users[0])['ranking']['count'] == 3
    print('PASS: four-person minimum, identical-return midranks, expired peer filtering and private ranking responses')

    # The Stripe transport is replaced completely: this checks local entitlement
    # reconciliation without issuing requests or touching payment accounts.
    billing_user = created_users[3]
    customer = 'cus_ranking_fixture_' + tag
    billing_user.update(stripe_customer_id=customer, membership_source='subscription',
                        membership_expires_at=now + timedelta(days=1))
    stripe_billing._subscriptions = lambda config, customer_id: []
    stripe_billing._reconcile_customer(customer, {'STRIPE_PLUS_PRICE_ID': 'price_fixture'})
    assert billing_user.get_id() not in {p['user_id'] for p in snapshot['participants']}
    assert rankings._ranking_candidate(created_profiles[3], quarter) is None
    print('PASS: billing entitlement revocation immediately invalidates the ranking; no Stripe network requests')

    creator_portfolios._require_creator = lambda: created_users[0]
    result = memberships.creator_set_membership(created_users[2].get_id(), 'free')
    assert result['ok'] and not result['membership']['is_premium']
    assert created_users[2].get_id() not in {p['user_id'] for p in snapshot['participants']}
    print('PASS: creator membership revocation immediately invalidates the ranking')

    snapshot.update(generated_at=old_stamp, refresh_state={})
    old_claim = rankings._claim_ranking_batch(quarter, True)
    assert rankings._claim_ranking_batch(quarter, True) is None
    state = dict(snapshot['refresh_state'])
    state['lease_until'] = 0
    snapshot['refresh_state'] = state
    replacement = rankings._claim_ranking_batch(quarter, True)
    assert replacement['token'] != old_claim['token']
    assert not rankings._commit_ranking_batch(old_claim, [], [])
    rankings._commit_ranking_batch(replacement, [], [])
    claim = rankings._claim_ranking_batch(quarter, True)
    stale = rankings._ranking_candidate(created_profiles[0], quarter)
    anvil.users.get_user = lambda: created_users[0]
    assert rankings.set_investor_profile(None, False)['ok']
    rankings._commit_ranking_batch(claim, [created_profiles[0]], [stale])
    assert created_users[0].get_id() not in {p['user_id'] for p in snapshot['participants']}
    print('PASS: concurrent worker lease, expired-lease takeover, stale worker rejection and opt-out during in-flight refresh')

    anvil.users.get_user = lambda: None
    try:
        rankings.refresh_my_profile_rankings()
        raise AssertionError('Anonymous ranking refresh accepted')
    except anvil.users.AuthenticationFailed:
        pass
    print('PASS: anonymous ordinary-call access rejected')
finally:
    anvil.users.get_user = get_user
    portfolios._portfolios_for = get_directory
    stripe_billing._subscriptions = get_subscriptions
    creator_portfolios._require_creator = require_creator
    for profile in created_profiles:
        profile.delete()
    for user in created_users:
        user.delete()
    snapshot = app_tables.profile_rankings.get(quarter=quarter)
    if backup is None:
        if snapshot is not None:
            snapshot.delete()
    elif snapshot is not None:
        snapshot.update(**backup)
    print('CLEANUP: ranking fixtures removed and previous snapshot restored')
