"""Current target ownership is distinct from an installed historical policy."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


def apply_current_settings(target, clock, *, order_enabled=True):
    profile = target.request['credential_profile_id']
    target.store.register_credential_profile('kiwoom_mock', profile, clock[0].isoformat())
    scope = target.binding.scope.to_dict()
    current = target.store.load_account_settings(scope)
    saved = target.store.save_account_settings(dict(scope=scope, active_profile_id=profile,
        monitor_enabled=True, mock_order_enabled=order_enabled), expected_revision=current['revision'])
    target.owner._settings_revisions = {profile: saved['revision']}
    target.settings = saved
    return saved


async def prepared_target(case):
    from tests.unit.test_selected_order_bootstrap import SelectedOrderBootstrapTests
    from tests.unit.test_mock_automation_specification import _FakeBrokerTransport
    from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
    from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime, ManualMockOrderGateway
    from kiwoom_monitor.domain.order_contract import AccountSnapshot
    from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository

    source, target, url = await SelectedOrderBootstrapTests.prepare(case, detailed=True)
    bundle, profile = target.owner.bundle('target-profile'), 'target-profile'
    for _ in range(2):
        bundle.clock[0] += timedelta(milliseconds=1)
        latest = target.store.append_account_binding(dict(credential_profile_id=profile, broker='kiwoom', environment='mock',
            account_ref=target.binding.scope.account_ref, verified_at=bundle.clock[0].isoformat(), verification_method='ka00001'))
    binding = replace(target.binding, binding_revision=latest['binding_revision'],
                      verified_at=datetime.fromisoformat(latest['verified_at']))
    target.binding, bundle.binding = binding, binding
    target.owner._revisions[profile] = 3
    target.request.update(credential_revision=3, binding_revision=binding.binding_revision)
    apply_current_settings(target, bundle.clock)
    case.enterContext(patch.object(target.store, '_now_provider', lambda: bundle.clock[0]))
    case.enterContext(patch.object(target.store, '_execution_wall_now', lambda: bundle.clock[0], create=True))
    repository = ExecutionRepository(target.store)
    transport = _FakeBrokerTransport(lambda: bundle.clock[0])
    runtime = ExecutionRuntime(OrderLifecycle(repository, transport, now_provider=lambda: bundle.clock[0]), repository,
        account_ref=binding.scope.account_ref, run_id='current-independent-target-run', owner_token='current-target-owner',
        now_provider=lambda: bundle.clock[0])
    runtime.start(new_orders_enabled=True)
    case.addCleanup(runtime.stop)
    snapshot = AccountSnapshot(binding.scope.account_ref, 250_000, 0, {}, bundle.clock[0])
    repository.save_account_snapshot('mock', snapshot, bundle.clock[0])
    async def read_account():
        return snapshot
    gateway = ManualMockOrderGateway(runtime, repository, read_account, now_provider=lambda: bundle.clock[0])
    case.addAsyncCleanup(gateway.close)
    bundle.runtime, bundle.gateway, bundle.run_id, bundle.repository = runtime, gateway, runtime.run_id, repository
    response = await target.client.post(url, json=target.request)
    case.assertEqual(200, response.status_code, response.text)
    case.assertFalse(runtime._new_orders_enabled)
    case.assertFalse(gateway._accepting)
    case.assertTrue(runtime._active)
    return SimpleNamespace(source=source, target=target, bundle=bundle, runtime=runtime, repository=repository,
        transport=transport, installation=response.json()['receipt'])


async def prepare_current_management(data, *, new_owner_token='fresh-original-management-owner', changed=False):
    target, source = data.target, data.source
    captured = {}
    async def prepare(identity, evidence, current_owner):
        from kiwoom_monitor.central_server.observation_frame_recovery import drain_thread_call
        captured['current_owner'] = current_owner
        checks = []
        def fenced():
            checks.append(1)
            if changed and len(checks) == 2:
                target.owner._revisions[identity.credential_profile_id] += 1
            return current_owner()
        return await drain_thread_call(target.store.prepare_selected_order_management, source.packet['operation_id'],
            identity=identity, broker_evidence=evidence, source_proof=source.proof,
            current_target_runtime=dict(account_ref=data.runtime.account_ref, run_id=data.runtime.run_id,
                                        owner_token=data.runtime.owner_token),
            new_owner_token=new_owner_token, current_owner=fenced,
            expected_settings_revision=target.settings['revision'])
    request = target.request
    result = await target.owner.verify_selected_order_identity(source.packet, source.digest, source.proof,
        credential_profile_id=request['credential_profile_id'], credential_revision=request['credential_revision'],
        account_ref=request['account_ref'], binding_revision=request['binding_revision'],
        broker_evidence_provider=target.broker.dated_provider, on_verified=prepare)
    return result, captured['current_owner']


async def actual_management_target(case, *, lost_cancel_ack=False, source_factory=None,
                                   broker_evidence_provider_factory=None, mock_order_enabled=True):
    """Real owner/bundle/Runtime/monitor; only the external broker is replaced."""
    from kiwoom_monitor.central_server.mock_runtime import MockAccountBundle, MockCredentialOwner
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.central_server.selected_order_recovery_routes import create_selected_order_recovery_router
    from kiwoom_monitor.infrastructure.kiwoom_rest import KiwoomSettings
    from kiwoom_monitor.infrastructure.kiwoom_rest.account_identity import KiwoomAccountIdentityReader
    from kiwoom_monitor.infrastructure.kiwoom_rest.mock_account import KiwoomMockAccountReader
    from kiwoom_monitor.domain.order_contract import AccountEnvironment, OrderSide, OrderState
    from tests.unit.test_selected_order_bootstrap import SelectedOrderBootstrapTests
    from tests.unit.test_selected_order_recovery import KEY, dated_orders
    from tests.unit.test_mock_account_bundle import FakeClient
    from tests.unit.test_close_order_management import BrokerBook
    from tests.unit.test_mock_automation_specification import _FakeBrokerTransport

    source = (await source_factory(case) if source_factory is not None else
        await SelectedOrderBootstrapTests.native_member_source(case, arm=True))
    target = await case.target(source)
    clock = target.owner.bundle('target-profile').clock
    # The source and target policy/binding coordinates deliberately differ.
    for _ in range(2):
        clock[0] += timedelta(milliseconds=1)
        latest = target.store.append_account_binding(dict(credential_profile_id='target-profile', broker='kiwoom',
            environment='mock', account_ref=target.binding.scope.account_ref,
            verified_at=clock[0].isoformat(), verification_method='ka00001'))
    target.binding = replace(target.binding, binding_revision=latest['binding_revision'],
        verified_at=datetime.fromisoformat(latest['verified_at']))
    MockCredentialOwner.__init__(target.owner, target.store, SimpleNamespace(), hmac_key=KEY,
        now_provider=lambda: clock[0])
    target.owner._revisions['target-profile'] = 3
    target.request.update(credential_revision=3, binding_revision=target.binding.binding_revision)
    settings = apply_current_settings(target, clock, order_enabled=mock_order_enabled)
    target.owner.vault.load = lambda provider, profile: SimpleNamespace(disabled=False, revision=3,
        payload={'credentials': {'app_key': 'fake', 'secret_key': 'fake'}})
    case.enterContext(patch.object(target.store, '_now_provider', lambda: clock[0]))
    case.enterContext(patch.object(target.store, '_execution_wall_now', lambda: clock[0], create=True))
    identity = await KiwoomAccountIdentityReader(target.broker, environment=AccountEnvironment.MOCK,
        hmac_key=KEY, now_provider=lambda: clock[0]).verify()
    if source.packet['version'] == 'selected-order-evidence/v5':
        target.broker.orders = source.evidence.orders
    else:
        target.broker.orders = dated_orders(source.packet)
    target.broker.start = AsyncMock()
    target.broker.close = AsyncMock()
    target.broker.begin_credential_change = AsyncMock()
    target.broker.end_credential_change = AsyncMock()
    client = FakeClient(KiwoomSettings('fake', 'fake', 'mock'))
    client.server_now = lambda: clock[0]
    transport = _FakeBrokerTransport(lambda: clock[0])
    context = SimpleNamespace(clock=clock, runtime=None, execution=None,
        buy=SimpleNamespace(intent=SimpleNamespace(symbol=source.packet['order']['symbol'])))
    book = BrokerBook(context, empty=True)
    for leg in target.broker.orders:
        book.rows[leg.broker_order_id] = dict(number=leg.broker_order_id, side=OrderSide(leg.side),
            ordered=leg.ordered_quantity, remaining=leg.remaining_quantity, price=leg.limit_price,
            fills=list(leg.fills), parent=leg.original_broker_order_id, state=OrderState(leg.state))
    async def read(reader):
        result = await book.read()
        return replace(result, account=replace(result.account, account_ref=reader._account_ref),
            orders=tuple(replace(row, account_ref=reader._account_ref) for row in result.orders))
    async def costs(reader, start, end):
        return await book.read_trade_cost_pages(start, end)
    case.enterContext(patch.object(KiwoomMockAccountReader, 'read', read))
    case.enterContext(patch.object(KiwoomMockAccountReader, 'read_trade_cost_pages', costs))
    case.enterContext(patch('kiwoom_monitor.central_server.mock_runtime.make_mock_transport', return_value=transport))
    case.enterContext(patch('kiwoom_monitor.central_server.mock_runtime.MockAccountRealtimeCollector.start', new=AsyncMock()))
    original_submit = transport.submit
    def submit(intent):
        result = original_submit(intent)
        result = replace(result, broker_order_id='current-management-' + result.broker_order_id)
        book.add(result.broker_order_id, intent)
        return result
    cancel_lost = []
    def cancel(intent, number, quantity=0):
        result = book.cancel(intent, number, quantity)
        if lost_cancel_ack and not cancel_lost:
            from kiwoom_monitor.infrastructure.kiwoom_rest.mock_execution import SubmissionUnknown
            cancel_lost.append(number)
            raise SubmissionUnknown('fake broker accepted one original cancellation; response lost')
        return result
    case.enterContext(patch.object(transport, 'submit', submit))
    case.enterContext(patch.object(transport, 'cancel', cancel))
    case.enterContext(patch.object(transport, 'amend', book.amend, create=True))
    bundle = MockAccountBundle(target.store, settings=client._settings,
        account_ref=target.binding.scope.account_ref, run_id='actual-current-target-run',
        credential_profile_id='target-profile', identity_hmac_key=KEY, client=client, broker=target.broker,
        identity=identity, binding=target.binding, order_transport_enabled=settings['mock_order_enabled'], require_initial_read=True,
        now_provider=lambda: clock[0])
    bundle.clock = clock  # External dated provider shares the same injected clock.
    context.runtime, context.execution = bundle.runtime, bundle.repository
    target.owner._bundles['target-profile'] = bundle
    target.owner._contexts['target-profile'] = bundle
    case.addAsyncCleanup(target.owner.close)
    await bundle.start()
    quotes = {}
    supervisor = MockAutomationSupervisor(target.store, target.owner, poll_seconds=.25,
        price_source=lambda symbol: quotes.get(symbol), now_provider=lambda: clock[0])
    target.owner._on_change = supervisor.bundle_changed
    case.addAsyncCleanup(supervisor.close)
    from fastapi import FastAPI, Header, HTTPException
    import httpx
    def authorize(authorization: str | None = Header(default=None)):
        if authorization != 'Bearer fixture':
            raise HTTPException(401)
    original_dated_provider = target.broker.dated_provider
    async def dated_provider(*, bundle, identity, packet, source_evidence=None):
        # The fake service supplies dated broker facts, not caller assertions.
        return await original_dated_provider(bundle=SimpleNamespace(
            account_ref=bundle.canonical_account_ref, clock=clock), identity=identity, packet=packet)
    if broker_evidence_provider_factory is not None:
        dated_provider = broker_evidence_provider_factory(book, target, clock)
    app = FastAPI()
    app.include_router(create_selected_order_recovery_router(target.store, target.owner, authorize,
        broker_evidence_provider=dated_provider, supervisor=supervisor))
    from kiwoom_monitor.central_server.mock_automation_routes import create_mock_automation_router
    app.include_router(create_mock_automation_router(supervisor, authorize))
    target.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://readonly.test',
        headers={'Authorization': 'Bearer fixture'})
    case.addAsyncCleanup(target.client.aclose)
    response = await target.client.post(target.url.replace('/assessment', '/bootstrap'), json=target.request)
    case.assertEqual(200, response.status_code, response.text)
    request = {key: value for key, value in target.request.items() if key != 'source_identity_proof'}
    request['expected_settings_revision'] = settings['revision']
    return SimpleNamespace(source=source, target=target, old=bundle, clock=clock,
        book=book, transport=transport, book_context=context, request=request, supervisor=supervisor, quotes=quotes,
        dated_provider=dated_provider)


async def reopen_management_target(case, data):
    """Restart native storage and its ordinary target reader, with no old lease."""
    from kiwoom_monitor.central_server.database import SQLiteQueryStore
    from kiwoom_monitor.central_server.mock_runtime import MockAccountBundle, MockCredentialOwner
    from kiwoom_monitor.central_server.mock_automation_supervisor import MockAutomationSupervisor
    from kiwoom_monitor.central_server.selected_order_recovery_routes import create_selected_order_recovery_router
    from kiwoom_monitor.central_server.mock_automation_routes import create_mock_automation_router
    from tests.unit.test_mock_account_bundle import FakeClient
    from tests.unit.test_selected_order_recovery import KEY
    from fastapi import FastAPI, Header, HTTPException
    import httpx
    target, clock = data.target, data.clock
    previous = target.owner.bundle('target-profile')
    await data.supervisor.close()
    await target.owner.close()
    target.store.close()
    factory = getattr(case, 'target_store_factory', None)
    store = factory() if factory else SQLiteQueryStore(target.path)
    store.initialize()
    case.addCleanup(store.close)
    case.enterContext(patch.object(store, '_now_provider', lambda: clock[0]))
    case.enterContext(patch.object(store, '_execution_wall_now', lambda: clock[0], create=True))
    vault = SimpleNamespace(load=lambda provider, profile: SimpleNamespace(disabled=False, revision=3,
        payload={'credentials': {'app_key': 'fake', 'secret_key': 'fake'}}))
    owner = MockCredentialOwner(store, vault, hmac_key=KEY, now_provider=lambda: clock[0])
    settings = store.load_account_settings(target.binding.scope.to_dict())
    client = FakeClient(previous.client._settings)
    client.server_now = lambda: clock[0]
    bundle = MockAccountBundle(store, settings=client._settings, account_ref=target.binding.scope.account_ref,
        run_id='fresh-target-reader-after-db-reopen', credential_profile_id='target-profile', identity_hmac_key=KEY,
        client=client, broker=target.broker, identity=previous._identity, binding=target.binding,
        order_transport_enabled=settings['mock_order_enabled'], require_initial_read=True, now_provider=lambda: clock[0])
    owner._contexts['target-profile'] = owner._bundles['target-profile'] = bundle
    owner._revisions['target-profile'] = 3
    data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
    await bundle.start()
    owner._settings_revisions['target-profile'] = settings['revision']
    supervisor = MockAutomationSupervisor(store, owner, poll_seconds=.25,
        price_source=lambda symbol: data.quotes.get(symbol), now_provider=lambda: clock[0])
    owner._on_change = supervisor.bundle_changed
    case.addAsyncCleanup(owner.close)
    case.addAsyncCleanup(supervisor.close)
    def authorize(authorization: str | None = Header(default=None)):
        if authorization != 'Bearer fixture':
            raise HTTPException(401)
    app = FastAPI()
    app.include_router(create_selected_order_recovery_router(store, owner, authorize,
        broker_evidence_provider=data.dated_provider, supervisor=supervisor))
    app.include_router(create_mock_automation_router(supervisor, authorize))
    client_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://readonly.test',
        headers={'Authorization': 'Bearer fixture'})
    case.addAsyncCleanup(client_http.aclose)
    target.store, target.owner, target.client, target.settings = store, owner, client_http, settings
    data.supervisor = supervisor
    data.request['expected_settings_revision'] = settings['revision']
    return previous, bundle


class SelectedManagementOwnerTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_selected_order_recovery import SelectedOrderRecoveryTests
    source, target = SelectedOrderRecoveryTests.source, SelectedOrderRecoveryTests.target

    async def test_public_prepare_joins_old_bundle_before_original_scope_reconciliation_owner(self):
        data = await actual_management_target(self)
        response = await data.target.client.post(data.target.url.replace('/assessment', '/prepare-management'),
            json=data.request)
        self.assertEqual(200, response.status_code, response.text)
        current = data.target.owner.bundle('target-profile')
        self.assertIsNot(data.old, current)
        self.assertFalse(data.old.runtime._active)
        self.assertIsNone(data.old.monitor._task)
        self.assertTrue(current.runtime._active)
        self.assertEqual(data.source.packet['account_ref'], current.account_ref)
        self.assertEqual(data.target.binding.scope.account_ref, current.canonical_account_ref)
        self.assertFalse(current.runtime._new_orders_enabled)
        self.assertEqual('RECONCILIATION_ONLY', response.json()['receipt']['status'])
        self.assertTrue(current.monitor.latest_automation_risk_cut['risk'].reconciliation_complete)
        self.assertEqual([], data.book.mutations)

    async def _exercise_current_acceptance(self, *, lost_cancel_ack=False, lost_accept_ack=False, stop_and_reaccept=False):
        data = await actual_management_target(self, lost_cancel_ack=lost_cancel_ack)
        original = data.source.selection['source'].read_bytes()
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        bundle = data.target.owner.bundle('target-profile')
        data.book_context.runtime, data.book_context.execution = bundle.runtime, bundle.repository
        data.clock[0] += timedelta(milliseconds=1)
        symbol = data.source.packet['order']['symbol']
        data.quotes[symbol] = dict(price_won=1200, available_at=data.clock[0],
            input_ref='collector-trade:target-independent-current-price', durable=False, market_status='TRADABLE')
        commits = []
        native_accept = data.target.store.accept_selected_order_management
        def accept_with_ack_loss(*args, **kwargs):
            result = native_accept(*args, **kwargs)
            commits.append(result)
            if lost_accept_ack:
                raise RuntimeError('fake response lost after native management commit')
            return result
        with patch.object(data.target.store, 'accept_selected_order_management', accept_with_ack_loss):
            accepted = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
                json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertEqual(1, len(commits))
        self.assertEqual(lost_accept_ack, accepted.json()['replayed'])
        self.assertEqual(('MANAGEMENT_READY', True, False), (accepted.json()['receipt']['status'],
            accepted.json()['receipt']['management_ready'], accepted.json()['receipt']['new_orders_enabled']))
        self.assertFalse(bundle.runtime._new_orders_enabled)
        runner = data.supervisor._runners[bundle.account_ref]
        self.assertEqual(1, len(runner.episodes))
        self.assertEqual(data.source.packet['source_spec_id'], runner.episode.status['spec_id'])
        import asyncio
        async def protection_submitted():
            cancellation_polled = False
            for sequence in range(200):
                data.clock[0] += timedelta(milliseconds=10)
                data.quotes[symbol] = {**data.quotes[symbol], 'available_at': data.clock[0],
                    'input_ref': 'collector-trade:target-later-protection-price:' + str(sequence)}
                data.supervisor.observe_automation_price(symbol, data.quotes[symbol])
                if (not cancellation_polled and data.book.mutations
                        and not bundle.runtime.observation_in_progress):
                    cancellation_polled = True
                    await bundle.monitor.refresh_recovery()
                if any(row.intent.side.value == 'SELL' for row in
                        bundle.repository.active_intents('mock', bundle.account_ref, bundle.run_id)):
                    return
                await asyncio.sleep(.05)
            self.fail(str((data.book.mutations, runner.status)))
        try:
            await asyncio.wait_for(protection_submitted(), timeout=30)
        except TimeoutError:
            self.fail(str((data.book.mutations, runner.status)))
        self.assertTrue(any(row[0] == 'CANCEL' for row in data.book.mutations), data.book.mutations)
        sells = tuple(record for record in bundle.repository.active_intents('mock', bundle.account_ref, bundle.run_id)
            if record.intent.side.value == 'SELL')
        self.assertEqual([1], [record.intent.quantity for record in sells])
        sell = sells[0]
        async def current_sell_managed():
            for sequence in range(200):
                status = runner.status
                if status.get('state') == 'WAITING_ORDER' and status.get('pending_intent_id') == sell.intent.intent_id:
                    return
                data.clock[0] += timedelta(milliseconds=10)
                data.quotes[symbol] = {**data.quotes[symbol], 'available_at': data.clock[0],
                    'input_ref': 'collector-trade:target-same-policy-sell:' + str(sequence)}
                data.supervisor.observe_automation_price(symbol, data.quotes[symbol])
                await asyncio.sleep(.05)
            self.fail(str(runner.status))
        await asyncio.wait_for(current_sell_managed(), timeout=30)
        from kiwoom_monitor.infrastructure.persistence.execution_repository import execution_broker_legs
        current_sell = bundle.repository.load(sell.intent.intent_id)
        working = [leg for leg in execution_broker_legs(current_sell) if leg.remaining_quantity]
        self.assertEqual(1, len(working))
        data.clock[0] += timedelta(milliseconds=1)
        data.book.fill(working[0].broker_order_id, 1, 'current-target-original-protection-fill')
        await bundle.monitor.refresh_recovery()
        async def confirmed_flat():
            for _ in range(200):
                head = bundle.repository.load_plan_position(bundle.account_ref, bundle.run_id, symbol, sell.intent.plan)
                if head.quantity == 0 and runner.episode.status.get('strategy_state') == 'cooldown':
                    return
                await asyncio.sleep(.05)
            self.fail(str((head, runner.status)))
        await asyncio.wait_for(confirmed_flat(), timeout=30)
        self.assertIsNone(runner.episode._episode_position.state.position_quantity)
        self.assertEqual(1, len([row for row in data.book.mutations if row[0] == 'CANCEL']))
        self.assertEqual(['SELL'], [row[1] for row in data.transport.calls])
        stored = bundle.repository.load(sell.intent.intent_id)
        self.assertEqual(1, stored.detailed_filled_quantity)
        status = await data.target.client.get(url.replace('/prepare-management', '/management'))
        self.assertEqual(200, status.status_code, status.text)
        self.assertEqual(data.source.packet['source_spec_id'], status.json()['management']['context']['spec_id'])
        self.assertEqual('MANAGEMENT_READY', status.json()['management']['status'])
        self.assertTrue(status.json()['control']['entry_resume_required'])
        self.assertTrue(status.json()['management_ready'])
        if stop_and_reaccept:
            first = status.json()['management']
            stopped = await data.target.client.post('/api/v1/mock-automation/stop', json=dict(
                account_ref=bundle.account_ref, credential_profile_id='target-profile',
                spec_id=first['context']['spec_id'], expected_control_revision=status.json()['control']['control_revision'],
                reason='test explicit selected management stop'))
            self.assertEqual(200, stopped.status_code, stopped.text)
            stopped_status = await data.target.client.get(url.replace('/prepare-management', '/management'))
            self.assertEqual(first, stopped_status.json()['management'], 'the historical receipt is immutable')
            self.assertFalse(stopped_status.json()['management_ready'])
            self.assertTrue(stopped_status.json()['reconciliation_ready'])
            data.clock[0] += timedelta(milliseconds=1)
            data.target.broker.orders = tuple(replace(order,
                filled_quantity=sum(fill.quantity for fill in data.book.rows[order.broker_order_id]['fills']),
                remaining_quantity=data.book.rows[order.broker_order_id]['remaining'],
                state=data.book.rows[order.broker_order_id]['state'].value,
                fills=tuple(data.book.rows[order.broker_order_id]['fills'])) for order in data.target.broker.orders)
            again = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
                json={**data.request, 'expected_control_revision': stopped_status.json()['control']['control_revision']})
            self.assertEqual(200, again.status_code, again.text)
            self.assertNotEqual(first['accepted_receipt_id'], again.json()['receipt']['accepted_receipt_id'])
            self.assertTrue(again.json()['supervisor']['control']['entry_resume_required'])
            self.assertFalse(bundle.runtime._new_orders_enabled)
            self.assertIs(runner, data.supervisor._runners[bundle.account_ref])
        self.assertEqual(original, data.source.selection['source'].read_bytes())
        return data

    async def test_public_current_acceptance_manages_only_original_pending_close_and_preserved_first_stop(self):
        await self._exercise_current_acceptance()

    async def test_lost_original_cancel_ack_is_reconciled_before_single_protection_sell(self):
        await self._exercise_current_acceptance(lost_cancel_ack=True)

    async def test_current_acceptance_commit_ack_loss_reads_first_native_receipt_once(self):
        await self._exercise_current_acceptance(lost_accept_ack=True)

    async def test_explicit_stop_preserves_receipt_then_fresh_management_acceptance_keeps_entry_off(self):
        await self._exercise_current_acceptance(stop_and_reaccept=True)

    async def test_global_transport_off_allows_reconciliation_but_no_management_acceptance(self):
        data = await actual_management_target(self, mock_order_enabled=False)
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        bundle = data.target.owner.bundle('target-profile')
        self.assertIsNone(bundle.gateway)
        await bundle.monitor.refresh_recovery()
        response = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
            json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn('MOCK_ORDER_TRANSPORT_DISABLED', response.text)
        status = await data.target.client.get(url.replace('/prepare-management', '/management'))
        self.assertTrue(status.json()['reconciliation_ready'])
        self.assertFalse(status.json()['management_ready'])
        self.assertEqual('STOPPED', status.json()['control']['desired_state'])
        self.assertEqual([], data.book.mutations)

    async def test_applied_transport_off_joins_original_owner_and_exposes_only_historical_acceptance(self):
        data = await self._exercise_current_acceptance()
        old = data.target.owner.bundle('target-profile')
        url = data.target.url.replace('/assessment', '/management')
        original = (await data.target.client.get(url)).json()['management']
        settings = data.target.store.load_account_settings(data.target.binding.scope.to_dict())
        applied = await data.target.owner.update_settings(settings['scope'],
            {key: settings[key] for key in ('active_profile_id', 'monitor_enabled')} | {'mock_order_enabled': False},
            settings['revision'])
        self.assertEqual(applied['settings']['revision'], applied['applied_revision'])
        current = data.target.owner.bundle('target-profile')
        self.assertFalse(old.runtime._active)
        self.assertIsNone(old.monitor._task)
        self.assertIsNone(current.selected_management_context)
        self.assertEqual(data.target.binding.scope.account_ref, current.account_ref)
        self.assertIsNone(current.gateway)
        status = (await data.target.client.get(url)).json()
        self.assertEqual(original, status['management'])
        self.assertFalse(status['management_ready'])
        self.assertFalse(status['reconciliation_ready'])

    async def test_db_reopen_retains_original_facts_but_requires_fresh_canonical_preparation_and_acceptance(self):
        data = await self._exercise_current_acceptance()
        url = data.target.url.replace('/assessment', '/management')
        original = (await data.target.client.get(url)).json()['management']
        previous, reader = await reopen_management_target(self, data)
        self.assertFalse(previous.runtime._active)
        self.assertIsNone(reader.selected_management_context)
        status = (await data.target.client.get(url)).json()
        self.assertEqual(original, status['management'])
        self.assertFalse(status['management_ready'])
        self.assertFalse(status['reconciliation_ready'])
        data.clock[0] += timedelta(seconds=1)
        data.target.broker.orders = tuple(replace(order,
            filled_quantity=sum(fill.quantity for fill in data.book.rows[order.broker_order_id]['fills']),
            remaining_quantity=data.book.rows[order.broker_order_id]['remaining'],
            state=data.book.rows[order.broker_order_id]['state'].value,
            fills=tuple(data.book.rows[order.broker_order_id]['fills'])) for order in data.target.broker.orders)
        prepared = await data.target.client.post(url.replace('/management', '/prepare-management'), json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        self.assertNotEqual(original['context']['management_id'], prepared.json()['receipt']['context']['management_id'])
        control = data.target.store.load_mock_automation_control(data.source.packet['account_ref'])
        self.assertEqual('STOPPED', control['desired_state'])
        accepted = await data.target.client.post(url.replace('/management', '/accept-management'),
            json={**data.request, 'expected_control_revision': control['control_revision']})
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertTrue(accepted.json()['supervisor']['control']['entry_resume_required'])
        self.assertFalse(data.target.owner.bundle('target-profile').runtime._new_orders_enabled)
        self.assertEqual(['SELL'], [row[1] for row in data.transport.calls])

    async def test_native_missing_current_recovery_cannot_accept_even_with_fresh_risk(self):
        data = await actual_management_target(self)
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        native_accept = data.target.store.accept_selected_order_management
        def lose_current_recovery(context, **kwargs):
            with data.target.store._lock, data.target.store._connection() as connection:
                connection.execute('BEGIN IMMEDIATE')
                connection.execute('DELETE FROM central_documents WHERE collection=? AND owner=? AND document_key=?',
                    ('execution_mock_automation_current_recovery', context.account_ref, context.admission_id))
            return native_accept(context, **kwargs)
        with patch.object(data.target.store, 'accept_selected_order_management', lose_current_recovery):
            response = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
                json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn('SELECTED_MANAGEMENT_CURRENT_RECOVERY_REQUIRED', response.text)
        self.assertEqual('STOPPED', data.target.store.load_mock_automation_control(data.source.packet['account_ref'])['desired_state'])
        self.assertEqual([], data.book.mutations)

    async def test_current_epoch_change_rolls_back_management_cas_and_keeps_entry_off(self):
        from kiwoom_monitor.central_server.selected_order_bootstrap import CURRENT_MANAGEMENT, ACCEPTANCES
        data = await actual_management_target(self)
        url = data.target.url.replace('/assessment', '/prepare-management')
        prepared = await data.target.client.post(url, json=data.request)
        self.assertEqual(200, prepared.status_code, prepared.text)
        native_accept = data.target.store.accept_selected_order_management
        def race(context, **kwargs):
            original_owner, checks = kwargs['current_owner'], []
            def current_owner():
                checks.append(1)
                if len(checks) == 2:
                    data.target.owner._revisions['target-profile'] += 1
                return original_owner()
            return native_accept(context, **{**kwargs, 'current_owner': current_owner})
        with patch.object(data.target.store, 'accept_selected_order_management', race):
            response = await data.target.client.post(url.replace('/prepare-management', '/accept-management'),
                json={**data.request, 'expected_control_revision': 1})
        self.assertEqual(409, response.status_code, response.text)
        current = data.target.store.load_document(CURRENT_MANAGEMENT,
            data.target.binding.scope.account_ref, data.target.binding.scope.account_ref)['document']
        self.assertEqual(('RECONCILIATION_ONLY', False), (current['status'], current['management_ready']))
        control = data.target.store.load_mock_automation_control(data.source.packet['account_ref'])
        self.assertEqual(('STOPPED', 1, True), (control['desired_state'], control['control_revision'], control['entry_resume_required']))
        self.assertEqual([], data.target.store.load_documents(ACCEPTANCES, data.source.packet['account_ref'], 20))
        self.assertFalse(data.target.owner.bundle('target-profile').runtime._new_orders_enabled)
        self.assertEqual([], data.book.mutations)


class SelectedManagementLeaseTests(unittest.IsolatedAsyncioTestCase):
    from tests.unit.test_selected_order_recovery import SelectedOrderRecoveryTests
    source, target = SelectedOrderRecoveryTests.source, SelectedOrderRecoveryTests.target

    async def test_fresh_current_target_hands_one_lease_to_original_run_without_activating_past_approval(self):
        from kiwoom_monitor.application.order_lifecycle import OrderLifecycle
        from kiwoom_monitor.application.selected_order_recovery import validate_management_closure
        from kiwoom_monitor.central_server.execution_runtime import ExecutionRuntime
        from kiwoom_monitor.domain.selected_order_evidence import selected_order_management_context_from_dict
        from kiwoom_monitor.infrastructure.persistence.execution_repository import ExecutionRepository
        from kiwoom_monitor.infrastructure.persistence.forward_evaluation_repository import ForwardEvaluationRepository
        data = await prepared_target(self)
        spec, _, admission = validate_management_closure(data.source.packet)
        self.assertNotEqual(spec.credential_profile_id, data.target.binding.credential_profile_id)
        self.assertNotEqual(spec.binding_revision, data.target.binding.binding_revision)
        source_bytes = data.source.selection['source'].read_bytes()
        result, current_owner = await prepare_current_management(data)
        receipt = result['receipt']
        self.assertEqual(('RECONCILIATION_ONLY', False, False, False),
            (receipt['status'], receipt['order_authority'], receipt['management_ready'], receipt['new_orders_enabled']))
        context = selected_order_management_context_from_dict(receipt['context'])
        self.assertEqual((spec.spec_id, admission.admission_id, data.source.packet['account_ref']),
                         (context.spec_id, context.admission_id, context.account_ref))
        self.assertEqual((3, data.target.binding.binding_revision), (context.credential_revision, context.binding_revision))
        with self.assertRaisesRegex(RuntimeError, 'ownership was lost'):
            data.runtime.heartbeat()
        self.assertFalse(data.runtime.stop(), 'the old target token cannot release the new canonical owner')
        repository = ExecutionRepository(data.target.store)
        repository.bind_selected_management_context(context, current_owner=current_owner)
        manager = ExecutionRuntime(OrderLifecycle(repository, data.transport, now_provider=lambda: data.bundle.clock[0]),
            repository, account_ref=context.account_ref, run_id=context.execution_run_id,
            owner_token='fresh-original-management-owner', now_provider=lambda: data.bundle.clock[0])
        self.addCleanup(manager.stop)
        manager.start(new_orders_enabled=False)
        manager.heartbeat()
        with data.target.store.mock_automation_read_scope() as cursor:
            rows = cursor.execute('SELECT owner_key,owner_token FROM central_execution_runtime_leases').fetchall()
        self.assertEqual([('mock:' + context.canonical_account_ref,
            context.execution_run_id + ':fresh-original-management-owner')], [tuple(row) for row in rows])
        bare_repository = ExecutionRepository(data.target.store)
        with self.assertRaisesRegex(RuntimeError, 'another mock execution runtime'):
            ExecutionRuntime(OrderLifecycle(bare_repository, data.transport),
                bare_repository, account_ref=context.account_ref,
                run_id=context.execution_run_id, owner_token='bare-origin-owner', now_provider=lambda: data.bundle.clock[0]).start()
        ordinary = ForwardEvaluationRepository(data.target.store)
        selected = ForwardEvaluationRepository(data.target.store, selected_management_context=context)
        self.assertIsNone(ordinary.load_mock_automation_spec(context.account_ref, context.spec_id))
        self.assertEqual(spec, selected.load_mock_automation_spec(context.account_ref, context.spec_id))
        self.assertEqual(admission, selected.load_mock_automation_admission_for_spec(context.account_ref, context.spec_id))
        fresh_lease = selected.load_mock_automation_lease_for_admission(context.account_ref, context.admission_id)
        self.assertEqual(receipt['current_lease_receipt'], fresh_lease.to_dict())
        self.assertEqual(source_bytes, data.source.selection['source'].read_bytes())
        self.assertEqual([], data.transport.calls)
        self.assertFalse(manager._new_orders_enabled)

    async def test_target_epoch_race_rolls_back_preparation_and_retains_only_current_target_lease(self):
        from kiwoom_monitor.central_server.selected_order_bootstrap import CURRENT_MANAGEMENT
        data = await prepared_target(self)
        with self.assertRaisesRegex(RuntimeError, 'CURRENT_LEASE_CHANGED|PREPARATION_CHANGED'):
            await prepare_current_management(data, changed=True)
        self.assertIsNone(data.target.store.load_document(CURRENT_MANAGEMENT,
            data.target.binding.scope.account_ref, data.target.binding.scope.account_ref))
        with data.target.store.mock_automation_read_scope() as cursor:
            rows = cursor.execute('SELECT owner_key,owner_token FROM central_execution_runtime_leases').fetchall()
        self.assertEqual([('mock:' + data.runtime.account_ref, data.runtime.run_id + ':' + data.runtime.owner_token)],
                         [tuple(row) for row in rows])
        self.assertFalse(data.runtime._new_orders_enabled)
        self.assertEqual([], data.transport.calls)
