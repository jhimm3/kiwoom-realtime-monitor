"""Offline subscription transport for the native market collector.

No LOGIN, token, socket connection, historical READY or subscriber-queue
injection. The caller owns source scheduling, workload selection and drain.
This adapter alone is not full TOP20 lifecycle/performance acceptance.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time

from kiwoom_monitor.application.market_session_schedule import realtime_subscription_target
from .diagnostic_top20_lifecycle_input import SubscriptionTape, SubscriptionInputUnavailable
from .realtime_collector import CentralRealtimeCollector, _registered_trade_sources


class _OfflineSocket:
    """Only the native registration writer uses this; nothing reaches a network."""
    async def send(self, value):
        frame = json.loads(value)
        if frame.get("trnm") not in {"REG", "REMOVE"}:
            raise SubscriptionInputUnavailable("top20_offline_wire_command_forbidden")


class Top20SubscriptionTransport:
    def __init__(self, collector, tape, *, source_hub_component, component_map,
                 exclude_components=(), preserve_ack_delay=True):
        if type(collector) is not CentralRealtimeCollector or not collector._input_replay_only:
            raise ValueError("top20_transport_requires_native_offline_collector")
        if type(tape) is not SubscriptionTape or type(preserve_ack_delay) is not bool:
            raise ValueError("top20_transport_tape_invalid")
        self.collector, self.tape = collector, tape
        self.source_hub_component = source_hub_component
        self.component_map = copy.deepcopy(component_map)
        self.exclude_components = tuple(exclude_components)
        self.preserve_ack_delay = preserve_ack_delay
        self.groups = {}
        self._signature = None
        self._subscribed = ()
        self._connection_approved = False
        self.receipts = []
        self.failure = None
        self.received_rows = self.delivered_rows = 0
        self.unapproved_rows = self.unregistered_rows = 0

    def _fail(self, reason):
        self.failure = self.failure or reason
        self.tape._fail(reason)

    async def update(self):
        """A current native hub intent must match before any ACK can be applied."""
        collector, hub = self.collector, self.collector._hub
        if collector._credential_shutdown:
            raise RuntimeError("INPUT_REPLAY_NOT_RUNNING")
        codes, nxt = hub.requested_codes()
        target = realtime_subscription_target(codes, set(nxt), collector._now_provider(),
                                              environment=collector._environment)
        request = {"session": "KRX" if target.krx_codes else "NXT",
                   "codes": list(target.active_codes), "nxt_codes": list(target.nxt_codes),
                   "program_codes": list(hub.requested_program_codes()),
                   "priority_codes": list(hub.requested_priority_codes())}
        signature = json.dumps(request, sort_keys=True), target.signature
        if signature == self._signature:
            return None
        if not target.active_codes:
            # No producer owns a subscription. Do not reuse a past subscription
            # or force READY simply to consume all recorded input.
            hub.set_upstream_ready((), False)
            collector.accept_replay_sources(set(), reset_all=False)
            self._signature = signature
            self._subscribed = ()
            self._connection_approved = False
            self.groups = {}
            return None
        try:
            matched = self.tape.match(hub, request, source_hub_component=self.source_hub_component,
                component_map=self.component_map, exclude_components=self.exclude_components)
            if matched["outcome"] != "returned":
                self._fail("top20_transport_partial_registration_unsupported")
            started = time.monotonic()
            groups = await collector._send_subscription(_OfflineSocket(), request["session"],
                target.active_codes, target.nxt_codes, request["program_codes"],
                request["priority_codes"], previous_groups=self.groups)
            public = {group: [part for part in parts if not set(part["type"]) & {"00", "04"}]
                      for group, parts in groups.items()}
            if public != matched["groups"]:
                self._fail("top20_transport_native_registration_changed")
            pending = len(groups)
            added = tuple(code for code in target.active_codes if code not in self._subscribed)
            sources = _registered_trade_sources(groups)
            acknowledgements = matched["ack_inputs"]
            if not acknowledgements:
                self._fail("top20_transport_ack_missing")
            for ack in acknowledgements:
                delay = (ack["entered_mono_ns"] - matched["entered_mono_ns"]) / 1e9
                if delay < 0:
                    self._fail("top20_transport_ack_time_invalid")
                if self.preserve_ack_delay:
                    await asyncio.sleep(max(0, delay - (time.monotonic() - started)))
                pending = collector._accept_registration_ack(
                    {"trnm": "REG", "return_code": 0 if ack["accepted"] else 1}, pending,
                    session=request["session"], subscribed=target.active_codes, sources=sources,
                    added=added, reset_all=not self._connection_approved,
                )
            if pending:
                self._fail("top20_transport_ack_missing")
            self.groups = groups
            self._signature = signature
            self._subscribed = target.active_codes
            self._connection_approved = True
            receipt = {"source_subscription_input_id": matched["subscription_input_id"],
                "source_ack_input_ids": [ack["input_id"] for ack in acknowledgements],
                "native_ready": hub.upstream_ready,
                "native_subscription_codes": list(target.active_codes),
                "elapsed_ms": round((time.monotonic() - started) * 1000, 3)}
            self.receipts.append(receipt)
            return receipt
        except BaseException as error:
            self.failure = self.failure or type(error).__name__
            raise

    def gap(self, *, clear_continuous=True):
        self.collector.accept_replay_gap(clear_continuous=clear_continuous)
        self.collector._hub.set_upstream_ready((), False)
        self._signature = None
        self._connection_approved = False

    @property
    def connection_approved(self):
        return self._connection_approved

    def registered_message(self, message):
        """Filter recorded wire input by the new subscription, including OFF masks.

        A peer/removed symbol's old tick cannot secretly reach a native writer.
        Account rows are never accepted. Actual parsing still belongs to the
        collector's allowlisted input entry point.
        """
        from .diagnostic_replay_contract import validate_collector_message
        validate_collector_message(message)
        received = len(message["data"])
        self.received_rows += received
        if not self._connection_approved:
            # A disconnected source cannot deliver ticks just because its old
            # registration groups are retained for native replacement.
            self.unapproved_rows += received
            return received, 0
        allowed = {(kind, item) for parts in self.groups.values() for part in parts
                   for kind in part["type"] if kind not in {"00", "04"} for item in part["item"]}
        value = copy.deepcopy(message)
        def item_key(row):
            for key in ("item", "stk_cd", "code"):
                item = row.get(key)
                if isinstance(item, str) and item.strip():
                    return item.strip()
                if isinstance(item, list) and item and isinstance(item[0], str) and item[0].strip():
                    return item[0].strip()
            return ""
        value["data"] = [row for row in value["data"] if (row["type"], item_key(row)) in allowed]
        if value["data"]:
            self.collector.accept_replay_message(value)
        delivered = len(value["data"])
        self.delivered_rows += delivered
        self.unregistered_rows += received - delivered
        return received, delivered

    def report(self):
        return {"failure": self.failure, "subscriptions": copy.deepcopy(self.receipts),
                "tape": self.tape.report(), "historical_ready_injected": False,
                "received_rows": self.received_rows, "delivered_rows": self.delivered_rows,
                "unapproved_rows": self.unapproved_rows, "unregistered_rows": self.unregistered_rows,
                "network_access": False}
