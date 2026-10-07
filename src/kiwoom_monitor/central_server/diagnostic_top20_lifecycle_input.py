"""Opt-in shared subscription and TOP20 control causes; preflight, not a runner.

Only public market identities are copied. LOGIN, account payloads, credentials
and server error text never enter this tape. Native ACK policy remains unchanged.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from functools import wraps
import copy
import json
import time
from uuid import uuid4

from . import diagnostic_trace as trace
from .diagnostic_replay_contract import freeze_payload, thaw_payload

VERSION = "top20-realtime-tape/v1"
EVENT = "top20_realtime_input"
MAX_EVENTS = 50000
MAX_BYTES = 32 * 1024 * 1024
MAX_SUBSCRIBERS = 1000
MAX_CODES = 1000
CONTROL_EVENTS = frozenset({"connection_status", "connection_failed", "connection_opened",
                           "codes_added", "subscription_ready"})


def _codes(values):
    values = list(values)
    if (len(values) > MAX_CODES or any(type(code) is not str or not code or len(code) > 32 for code in values)
            or len(set(values)) != len(values)):
        raise ValueError("top20_realtime_codes_invalid")
    return values


def contributions(hub):
    if len(hub._subscribers) > MAX_SUBSCRIBERS:
        raise ValueError("top20_realtime_subscriber_limit")
    return sorted([{"subscriber_id": f"subscriber:{id(client):x}",
                    "component": client.capture_component,
                    "codes": _codes(sorted(client.codes)), "nxt_codes": _codes(sorted(client.nxt_codes)),
                    "program_codes": _codes(sorted(client.program_codes)),
                    "priority_codes": _codes(client.priority_codes)} for client in hub._subscribers],
                  key=lambda item: item["subscriber_id"])


def _emit(owner, kind, value, *, identifier=None):
    token = trace.input_token("top20_inputs")
    if token is None or getattr(owner, "_input_replay_only", False):
        return None
    from .realtime_hub import CapturedControlReceipt
    component = (f"realtime_hub:{id(owner):x}" if hasattr(owner, "_subscribers")
                 else f"realtime_collector:{id(owner):x}")
    fields = {"input_id": identifier or uuid4().hex, "input_kind": kind,
              "input_version": VERSION, "trace_id": token, "workload_id": "realtime",
              "producer_component": component, "entered_mono_ns": time.monotonic_ns(),
              "entered_wall_ns": time.time_ns()}
    try:
        complete = trace.emit_payload(token, EVENT, fields, value)
        return CapturedControlReceipt(token, component, fields["input_id"], kind, bool(complete))
    except Exception:
        try:
            trace.reject_input(token, {**fields, "reason": "top20_realtime_observer_error"})
        except Exception:
            pass
        return CapturedControlReceipt(token, component, fields["input_id"], kind, False)


def hub_initial(hub):
    """Snapshot before the first observed mutation; do not infer cold readiness."""
    token = trace.input_token("top20_inputs")
    if token is None or getattr(hub, "_recorded_lifecycle_epoch", None) == token:
        return
    receipt = _emit(hub, "hub_initial", {"subscriptions": contributions(hub),
        "upstream_ready": hub.upstream_ready, "upstream_codes": sorted(hub._upstream_codes)})
    hub._recorded_lifecycle_epoch = token
    hub._recorded_lifecycle_initial_complete = bool(receipt and receipt.complete)


def subscription_intent(hub, subscriber, action):
    _emit(hub, "subscription_intent", {"action": action,
        "subscriber_id": f"subscriber:{id(subscriber):x}",
        "component": subscriber.capture_component, "subscriptions": contributions(hub)})


def reject(owner, reason):
    token = trace.input_token("top20_inputs")
    if token is not None:
        try:
            trace.reject_input(token, {"workload_id": "realtime", "reason": reason,
                "producer_component": f"realtime_hub:{id(owner):x}" if hasattr(owner, "_subscribers")
                    else f"realtime_collector:{id(owner):x}"})
        except Exception:
            pass


def record_intent(hub, subscriber, action):
    if trace.input_token("top20_inputs") is None:
        return
    try:
        subscription_intent(hub, subscriber, action)
    except Exception:
        reject(hub, "top20_realtime_intent_invalid")


def record_ready(hub, codes, ready, approval=None):
    if trace.input_token("top20_inputs") is None:
        return None
    try:
        hub_initial(hub)
        valid = bool(approval and approval.trace_id == trace.input_token("top20_inputs") and approval.complete)
        receipt = _emit(hub, "upstream_ready", {"codes": _codes(codes), "ready": bool(ready),
            "approval_input_id": approval.input_id if valid else "", "approval_complete": valid})
        hub._recorded_ready_receipt = receipt
        return receipt
    except Exception:
        reject(hub, "top20_realtime_ready_invalid")
        return None


def record_hub_event(hub, event):
    if event.get("type") not in CONTROL_EVENTS or trace.input_token("top20_inputs") is None:
        return event
    from .realtime_hub import CapturedHubEvent
    try:
        hub_initial(hub)
        # TOP20 consumes no server error text. Retain only explicit public state.
        value = {key: event[key] for key in ("type", "scope", "codes") if key in event}
        status = event.get("payload") if event["type"] == "connection_status" else event.get("connection_status")
        if isinstance(status, dict):
            value["status"] = {key: status[key] for key in ("generation", "phase", "paused", "shutdown",
                "planned_reconnect", "outcome", "observation_expected", "gap_seconds") if key in status}
        parent = getattr(hub, "_recorded_ready_receipt", None)
        parent_id = parent.input_id if parent and parent.trace_id == trace.input_token("top20_inputs") else ""
        receipt = _emit(hub, "hub_event", {"event": value, "ready_input_id": parent_id})
        return CapturedHubEvent(event, source=receipt)
    except Exception:
        reject(hub, "top20_realtime_control_event_invalid")
        return event


def record_market_operation(collector, tick):
    if trace.input_token("top20_inputs") is None or collector._input_replay_only:
        return None
    return _emit(collector, "market_operation", {"source_time": collector._now_provider(), "tick": asdict(tick)})


def record_gap(collector, clear_continuous):
    if trace.input_token("top20_inputs") is None or collector._input_replay_only:
        return
    _emit(collector, "capture_gap", {"clear_continuous": bool(clear_continuous),
                                    "source_time": collector._now_provider()})


def captured_subscription(function):
    @wraps(function)
    async def run(collector, websocket, session, codes, nxt_codes, program_codes=None,
                  priority_codes=None, *, previous_groups=None):
        token = trace.input_token("top20_inputs")
        receipt = None
        if token is not None and not collector._input_replay_only:
            try:
                hub_initial(collector._hub)
                receipt = _emit(collector, "subscription_start", {
                    "hub_component": f"realtime_hub:{id(collector._hub):x}",
                    "generation": collector._connection_generation, "session": session,
                    "codes": _codes(codes), "nxt_codes": _codes(nxt_codes),
                    "program_codes": _codes(codes if program_codes is None else program_codes),
                    "priority_codes": _codes(priority_codes or ()),
                    "subscriptions": contributions(collector._hub)})
            except Exception:
                reject(collector, "top20_realtime_subscription_invalid")
        collector._recorded_subscription_batch = None
        try:
            result = await function(collector, websocket, session, codes, nxt_codes, program_codes,
                                    priority_codes, previous_groups=previous_groups)
        except BaseException as error:
            if receipt:
                _emit(collector, "subscription_end", {"outcome": "failed", "error_type": type(error).__name__},
                      identifier=receipt.input_id)
            raise
        if receipt:
            try:
                public = {group: [{"item": part["item"], "type": part["type"]}
                                  for part in parts if not set(part["type"]) & {"00", "04"}]
                          for group, parts in result.items()}
                end = _emit(collector, "subscription_end", {"outcome": "returned", "groups": public},
                            identifier=receipt.input_id)
                collector._recorded_subscription_batch = {"receipt": receipt, "end": end,
                    "acks": 0, "group_count": len(result)}
            except Exception:
                reject(collector, "top20_realtime_subscription_result_invalid")
        return result
    return run


def record_ack(collector, message):
    token = trace.input_token("top20_inputs")
    if token is None or collector._input_replay_only:
        return None
    batch = getattr(collector, "_recorded_subscription_batch", None)
    if not batch or batch["receipt"].trace_id != token:
        reject(collector, "top20_realtime_ack_without_subscription")
        return None
    batch["acks"] += 1
    code = message.get("return_code")
    accepted = code in (None, 0, "0")
    receipt = _emit(collector, "subscription_ack", {"subscription_input_id": batch["receipt"].input_id,
        "ordinal": batch["acks"], "accepted": accepted,
        "group_no": str(message.get("grp_no", ""))[:32]})
    if (accepted and batch["acks"] == batch["group_count"] and batch["receipt"].complete
            and batch["end"] and batch["end"].complete):
        return receipt
    return None


def _validate_contributions(value):
    if type(value) is not list or len(value) > MAX_SUBSCRIBERS:
        raise ValueError("top20_realtime_contributions_invalid")
    ids = set()
    for item in value:
        if (type(item) is not dict or type(item.get("subscriber_id")) is not str
                or not item["subscriber_id"] or item["subscriber_id"] in ids
                or type(item.get("component")) is not str or len(item["component"]) > 160):
            raise ValueError("top20_realtime_contributions_invalid")
        ids.add(item["subscriber_id"])
        for key in ("codes", "nxt_codes", "program_codes", "priority_codes"):
            if type(item.get(key)) is not list:
                raise ValueError("top20_realtime_contributions_invalid")
            _codes(item[key])
        if not set(item["nxt_codes"] + item["priority_codes"]).issubset(item["codes"]):
            raise ValueError("top20_realtime_contributions_invalid")


def read_realtime_tape(events):
    """Complete prefix gate. File checksums/sequence remain the outer reader's job."""
    if not 1 <= len(events) <= MAX_EVENTS:
        raise ValueError("top20_realtime_event_limit")
    records, subscriptions, ready, initial, topology = {}, {}, {}, {}, {}
    current_ready, approved_batches = {}, set()
    previous, charge, epochs = 0, 0, set()
    for row in events:
        if type(row) is not dict or type(row.get("seq")) is not int or row["seq"] <= previous:
            raise ValueError("top20_realtime_sequence_invalid")
        previous = row["seq"]
        if row.get("event_type") == "input_rejected":
            raise ValueError("top20_realtime_rejected_input")
        if row.get("event_type") != EVENT:
            continue
        if (row.get("input_version") != VERSION or type(row.get("input_id")) is not str
                or not row["input_id"] or type(row.get("trace_id")) is not str or not row["trace_id"]
                or type(row.get("producer_component")) is not str or not row["producer_component"]):
            raise ValueError("top20_realtime_identity_invalid")
        epochs.add(row["trace_id"])
        charge += freeze_payload(row.get("payload"), maximum_bytes=MAX_BYTES-charge).charge
        value = thaw_payload(row.get("payload"))
        if type(value) is not dict:
            raise ValueError("top20_realtime_payload_invalid")
        if type(row.get("entered_mono_ns")) is not int or row["entered_mono_ns"] < 0:
            raise ValueError("top20_realtime_time_invalid")
        kind, identifier = row.get("input_kind"), row["input_id"]
        if kind == "subscription_end":
            batch = subscriptions.get(identifier)
            if not batch or batch.get("end") or batch["row"]["producer_component"] != row["producer_component"]:
                raise ValueError("top20_realtime_subscription_pair_invalid")
            if value.get("outcome") not in {"returned", "failed"}:
                raise ValueError("top20_realtime_subscription_outcome_invalid")
            groups = value.get("groups")
            if value["outcome"] == "returned" and (type(groups) is not dict or not groups
                    or len(groups) > 100 or any(type(group) is not str or type(parts) is not list for group, parts in groups.items())):
                raise ValueError("top20_realtime_subscription_groups_invalid")
            if value["outcome"] == "returned":
                for parts in groups.values():
                    for part in parts:
                        if (type(part) is not dict or set(part) != {"item", "type"}
                                or type(part["item"]) is not list or type(part["type"]) is not list
                                or not part["type"] or set(part["type"]) - {"0B", "0w", "0g", "0J", "0U", "0s", "1h"}
                                or len(part["item"]) > MAX_CODES
                                or any(type(item) is not str or len(item) > 32 for item in part["item"])):
                            raise ValueError("top20_realtime_subscription_groups_invalid")
            batch["end"] = value
            batch["end_row"] = row
            continue
        if identifier in records:
            raise ValueError("top20_realtime_duplicate_input")
        records[identifier] = {"row": row, "value": value}
        if kind in {"hub_initial", "subscription_intent", "subscription_start"}:
            _validate_contributions(value.get("subscriptions"))
        if kind == "hub_initial":
            if row["producer_component"] in initial or type(value.get("upstream_ready")) is not bool:
                raise ValueError("top20_realtime_initial_invalid")
            _codes(value.get("upstream_codes", ()))
            initial[row["producer_component"]] = identifier
            topology[row["producer_component"]] = value["subscriptions"]
        elif kind == "subscription_intent":
            if row["producer_component"] not in initial or value.get("action") not in {"connect", "update", "disconnect"}:
                raise ValueError("top20_realtime_intent_without_initial")
            before = {item["subscriber_id"]: item for item in topology[row["producer_component"]]}
            after = {item["subscriber_id"]: item for item in value["subscriptions"]}
            subscriber, action = value.get("subscriber_id"), value["action"]
            if (type(subscriber) is not str or type(value.get("component")) is not str
                    or any(before.get(key) != after.get(key) for key in (set(before) | set(after)) - {subscriber})
                    or action == "connect" and (subscriber in before or subscriber not in after)
                    or action == "update" and (subscriber not in before or subscriber not in after)
                    or action == "disconnect" and (subscriber not in before or subscriber in after)
                    or (after.get(subscriber, before.get(subscriber, {})).get("component") != value["component"])):
                raise ValueError("top20_realtime_intent_transition_invalid")
            topology[row["producer_component"]] = value["subscriptions"]
        elif kind == "subscription_start":
            if (value.get("hub_component") not in initial or value.get("session") not in {"KRX", "NXT"}
                    or type(value.get("generation")) is not int or value["generation"] < 0):
                raise ValueError("top20_realtime_subscription_initial_missing")
            if value["subscriptions"] != topology[value["hub_component"]]:
                raise ValueError("top20_realtime_subscription_topology_mismatch")
            for key in ("codes", "nxt_codes", "program_codes", "priority_codes"):
                _codes(value.get(key, ()))
            subscriptions[identifier] = {"row": row, "start": value, "acks": []}
        elif kind == "subscription_ack":
            batch = subscriptions.get(value.get("subscription_input_id"))
            if (not batch or not batch.get("end") or batch["end"]["outcome"] != "returned"
                    or batch["row"]["producer_component"] != row["producer_component"]
                    or type(value.get("ordinal")) is not int or value["ordinal"] != len(batch["acks"])+1
                    or type(value.get("accepted")) is not bool or type(value.get("group_no")) is not str
                    or value["group_no"] and (value["group_no"] not in batch["end"]["groups"]
                        or value["group_no"] in {records[item]["value"]["group_no"] for item in batch["acks"]})
                    or len(batch["acks"]) >= len(batch["end"]["groups"])):
                raise ValueError("top20_realtime_ack_invalid")
            batch["acks"].append(identifier)
        elif kind == "upstream_ready":
            if row["producer_component"] not in initial or type(value.get("ready")) is not bool:
                raise ValueError("top20_realtime_ready_initial_missing")
            _codes(value.get("codes", ()))
            if value["ready"]:
                ack = records.get(value.get("approval_input_id"))
                batch = subscriptions.get(ack["value"].get("subscription_input_id")) if ack else None
                if (not value.get("approval_complete") or not batch or not batch.get("end")
                        or len(batch["acks"]) != len(batch["end"]["groups"])
                        or batch["acks"][-1] != value["approval_input_id"]
                        or any(not records[item]["value"]["accepted"] for item in batch["acks"])
                        or ack["value"]["subscription_input_id"] in approved_batches
                        or batch["start"]["hub_component"] != row["producer_component"]
                        or value["codes"] != batch["start"]["codes"]):
                    raise ValueError("top20_realtime_ready_without_approval")
                approved_batches.add(ack["value"]["subscription_input_id"])
            ready[identifier] = value
            current_ready[row["producer_component"]] = identifier
        elif kind == "hub_event":
            event = value.get("event")
            if type(event) is not dict or event.get("type") not in CONTROL_EVENTS:
                raise ValueError("top20_realtime_control_event_invalid")
            if event["type"] in {"subscription_ready", "codes_added", "connection_opened"}:
                parent = ready.get(value.get("ready_input_id"))
                if (not parent or not parent["ready"]
                        or value["ready_input_id"] != current_ready.get(row["producer_component"])):
                    raise ValueError("top20_realtime_control_without_ready")
        elif kind == "market_operation":
            tick = value.get("tick")
            if (not isinstance(value.get("source_time"), datetime) or type(tick) is not dict
                    or set(tick) != {"status_code", "trade_time", "remaining_time"}
                    or type(tick["status_code"]) is not str or not tick["status_code"]
                    or any(tick[key] is not None and type(tick[key]) is not str for key in ("trade_time", "remaining_time"))):
                raise ValueError("top20_realtime_market_operation_invalid")
        elif kind == "capture_gap":
            if type(value.get("clear_continuous")) is not bool or not isinstance(value.get("source_time"), datetime):
                raise ValueError("top20_realtime_gap_invalid")
        else:
            raise ValueError("top20_realtime_kind_unsupported")
    if (len(epochs) != 1 or not records or any(not batch.get("end") or
            batch["end"]["outcome"] == "returned" and
            (len(batch["acks"]) != len(batch["end"]["groups"])
             and not any(not records[item]["value"]["accepted"] for item in batch["acks"]))
            for batch in subscriptions.values())):
        raise ValueError("top20_realtime_incomplete_or_epoch_mismatch")
    return {"version": VERSION, "trace_id": epochs.pop(), "records": records,
            "subscriptions": subscriptions, "initial": initial, "top20_session_execution_ready": False}


def delivery_source(tape, first):
    identifier = first.get("parent_input_ids")
    if not isinstance(identifier, (list, tuple)) or len(identifier) != 1:
        raise ValueError("top20_realtime_delivery_parent_invalid")
    record = tape["records"].get(identifier[0])
    kind = first.get("event_kind")
    if (not record or record["row"]["seq"] >= first["seq"]
            or record["row"]["producer_component"] != first.get("source_component")
            or record["row"]["input_id"] != first.get("message_id")
            or type(first.get("parser_ordinal")) is not int or first["parser_ordinal"] != 0
            or (record["row"]["input_kind"] != "market_operation" if kind == "market_operation"
                else record["row"]["input_kind"] != "hub_event" or record["value"]["event"]["type"] != kind)):
        raise ValueError("top20_realtime_delivery_parent_invalid")
    return identifier[0]


def compile_lifecycle_descendants(events, *, component, include_top20=True):
    """Whole-service mask plan; never seed historical window results for exclusion.

    This gate validates shared ownership but does not authorize execution. T3/T4
    still own task/thread drain, file seeds and the native lifecycle runner.
    """
    if type(component) is not str or not component or type(include_top20) is not bool:
        raise ValueError("top20_lifecycle_selection_invalid")
    realtime = read_realtime_tape(events)
    from .diagnostic_top20_input import compile_delivery_coverage
    coverage = compile_delivery_coverage(events, trace_id=realtime["trace_id"], component=component)
    from .diagnostic_rest_input import EVENT as REST_EVENT, read_request_tape
    rest = read_request_tape(events) if any(row.get("event_type") == REST_EVENT for row in events) else {
        "calls": {}, "catalogs": {}}
    calls = rest["calls"]
    selected_causes = {identifier for identifier, call in calls.items() if call["owner"] == component}
    selected_causes.update(row["input_id"] for row in events if row.get("event_type") == REST_EVENT
                           and row.get("producer_component") == component)
    selected_causes.update(row["input_id"] for row in events if row.get("event_type") == "market_input"
                           and row.get("producer_component") == component)
    selected_causes.update(row["delivery_id"] for row in events if row.get("event_type") == "top20_delivery"
                           and row.get("producer_component") == component and row.get("delivery_id"))
    for call in calls.values():
        for _, value in [*call["transport"], *(entry for effect in call["effects"].values() for entry in effect)]:
            owners = {calls[parent]["owner"] for parent in value.get("parent_request_ids", ())}
            if component in owners and owners - {component}:
                raise ValueError("top20_lifecycle_mixed_shared_request")
    starts, ends = {}, {}
    for row in events:
        if row.get("event_type") not in {"operation_start", "operation_end"}:
            continue
        target = starts if row["event_type"] == "operation_start" else ends
        identifier = row.get("operation_id")
        if type(identifier) is not str or not identifier or identifier in target:
            raise ValueError("top20_lifecycle_operation_pair_invalid")
        target[identifier] = row
    from .diagnostic_replay_contract import CODEC_VERSION, thaw_operation_arguments
    replaced, excluded, peers = [], [], []
    for identifier, row in starts.items():
        ending = ends.get(identifier)
        if (not ending or any(ending.get(key) != row.get(key) for key in
                ("method", "producer_component", "actor_id", "workload_id", "cause_input_id", "codec_version", "payload_profile", "collection"))
                or any((key in ending) != (key in row) for key in ("payload_profile", "collection"))
                or ending.get("outcome") != "returned" or row.get("codec_version") != CODEC_VERSION):
            raise ValueError("top20_lifecycle_operation_pair_invalid")
        if (ending.get("seq", 0) <= row.get("seq", 0) or type(row.get("entered_mono_ns")) is not int
                or type(ending.get("finished_mono_ns")) is not int
                or ending["finished_mono_ns"] < row["entered_mono_ns"]):
            raise ValueError("top20_lifecycle_operation_pair_invalid")
        thaw_operation_arguments(row)
        if row.get("workload_id") == "top20" and row.get("actor_known") is not True:
            raise ValueError("top20_lifecycle_operation_owner_invalid")
        selected = row.get("producer_component") == component or row.get("cause_input_id") in selected_causes
        if selected:
            if row.get("actor_known") is not True or row.get("shared_owner_components"):
                raise ValueError("top20_lifecycle_operation_owner_invalid")
            (replaced if include_top20 else excluded).append(identifier)
        else:
            peers.append(identifier)
    if set(ends) != set(starts):
        raise ValueError("top20_lifecycle_operation_pair_invalid")
    for batch in realtime["subscriptions"].values():
        if any(not item["component"] and any(item[key] for key in ("codes", "nxt_codes", "program_codes"))
               for item in batch["start"]["subscriptions"]):
            raise ValueError("top20_lifecycle_ownerless_subscription")
    return {"scope": "top20_lifecycle_descendant_preflight", "delivery_coverage": coverage,
            "replaced_operation_ids": tuple(replaced),
            "excluded_operation_ids": tuple(excluded), "peer_operation_ids": tuple(peers),
            "service_cause_ids": tuple(sorted(selected_causes)) if include_top20 else (),
            "subscription_component_excluded": "" if include_top20 else component,
            "historical_ready_not_injected": True, "historical_outputs_not_seeded": True,
            "top20_session_execution_ready": False, "execution_authorized": False}


class SubscriptionInputUnavailable(RuntimeError):
    pass


class SubscriptionTape:
    """Match newly produced intents; never set READY or inject a subscriber queue.

    The future native runner supplies explicit component mappings. A changed
    subscription universe needs a recorded matching request, not old READY state.
    No websocket, credential or external connection API is exposed here.
    """
    def __init__(self, tape):
        self.tape = copy.deepcopy(tape)
        self.used, self.failure = set(), None

    def _fail(self, reason):
        self.failure = self.failure or reason
        raise SubscriptionInputUnavailable(reason)

    @staticmethod
    def _topology(subscriptions, component_map=None, excluded=()):
        result = []
        for item in subscriptions:
            component = item["component"]
            if component_map is not None:
                if component not in component_map:
                    raise SubscriptionInputUnavailable("top20_subscription_component_unbound")
                component = component_map[component]
            if component not in excluded:
                result.append({key: component if key == "component" else item[key]
                    for key in ("component", "codes", "nxt_codes", "program_codes", "priority_codes")})
        return sorted(result, key=lambda item: json.dumps(item, sort_keys=True))

    def match(self, hub, request, *, source_hub_component, component_map, exclude_components=()):
        if self.failure:
            self._fail(self.failure)
        keys = ("session", "codes", "nxt_codes", "program_codes", "priority_codes")
        if (type(request) is not dict or set(request) != set(keys) or request["session"] not in {"KRX", "NXT"}
                or type(component_map) is not dict or any(type(key) is not str or type(value) is not str
                    or not key or not value for key, value in component_map.items())):
            self._fail("top20_subscription_request_invalid")
        try:
            for key in keys[1:]:
                _codes(request[key])
            actual = self._topology(contributions(hub), component_map)
        except (ValueError, SubscriptionInputUnavailable) as error:
            self._fail(str(error))
        current_codes, current_nxt = hub.requested_codes()
        if (not set(request["codes"]).issubset(current_codes)
                or not set(request["nxt_codes"]).issubset(current_nxt)
                or any(item["component"] in exclude_components for item in actual)):
            self._fail("top20_subscription_intent_not_native_or_excluded")
        candidates = []
        for identifier, batch in self.tape["subscriptions"].items():
            if identifier in self.used or batch["start"]["hub_component"] != source_hub_component:
                continue
            if (all(list(batch["start"][key]) == list(request[key]) if key != "session"
                    else batch["start"][key] == request[key] for key in keys)
                    and self._topology(batch["start"]["subscriptions"], excluded=exclude_components) == actual):
                candidates.append((batch["row"]["seq"], identifier, batch))
        if not candidates:
            self._fail("top20_subscription_input_unavailable")
        _, identifier, batch = min(candidates)
        self.used.add(identifier)
        return {"subscription_input_id": identifier, "outcome": batch["end"]["outcome"],
                "entered_mono_ns": batch["row"]["entered_mono_ns"],
                "finished_mono_ns": batch["end_row"]["entered_mono_ns"],
                "groups": copy.deepcopy(batch["end"].get("groups", {})),
                "ack_inputs": [{"input_id": item,
                    "entered_mono_ns": self.tape["records"][item]["row"]["entered_mono_ns"],
                    **copy.deepcopy(self.tape["records"][item]["value"])} for item in batch["acks"]],
                "error_type": batch["end"].get("error_type", ""), "ready_applied": False}

    def report(self):
        return {"failure": self.failure, "unused_subscriptions": sorted(set(self.tape["subscriptions"]) - self.used),
                "top20_session_execution_ready": False}
