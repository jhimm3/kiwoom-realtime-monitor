"""Single RAM Shadow selection document contract; no runtime, state or native I/O."""
from __future__ import annotations

import json
from copy import deepcopy

SHADOW_RESULT_COLLECTION = "ram_shadow_diagnostics"
SHADOW_RESULT_OWNER = "market_theme"
SHADOW_SELECTION_COLLECTION = "ram_shadow_selection"

def _validate_selection_header(value):
    """Validate the existing common selection fields without loading a kernel."""
    required = {
        "version", "revision", "enabled", "theme_ids", "leadership_venue", "recording"
    }
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"stock_thesis", "position_comparison", "position_account_live", "position_allocation"}:
        raise ValueError("invalid RAM shadow selection fields")
    if value["version"] != "ram-shadow-selection/v1" or type(value["revision"]) is not int or value["revision"] < 0:
        raise ValueError("invalid RAM shadow selection version or revision")
    if type(value["enabled"]) is not bool or type(value["recording"]) is not bool:
        raise ValueError("RAM shadow enabled/recording must be boolean")
    themes = value["theme_ids"]
    if (not isinstance(themes, list) or len(themes) > 32 or
        any(not isinstance(v, str) or not v.strip() or v != v.strip() or len(v) > 120 for v in themes) or
        len(set(themes)) != len(themes)):
        raise ValueError("select at most 32 distinct nonempty theme IDs")
    if value["leadership_venue"] not in {"KRX", "NXT"}:
        raise ValueError("RAM shadow requires a KRX/NXT venue")
    return themes

def validate_ram_shadow_selection(value):
    """Preserve the full installed validation, including optional policy decoding."""
    themes = _validate_selection_header(value)
    request = _selected_stock_thesis(value)
    if request is not None and request.entry_thesis.theme_id not in themes:
        raise ValueError("selected research thesis theme must be an observed theme")
    document = value.get("position_comparison")
    live_account = value.get("position_account_live", False)
    if type(live_account) is not bool or (live_account and document is None):
        raise ValueError("live position observation requires explicit comparison inputs")
    if document is not None:
        from kiwoom_monitor.application.position_target import POSITION_ACTION_COMPARISON_SHADOW_VERSION
        if not isinstance(document, dict) or document.get("schema") != POSITION_ACTION_COMPARISON_SHADOW_VERSION:
            raise ValueError("position comparison requires a fixed input document")
        if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 256 * 1024:
            raise ValueError("position comparison input exceeds 256 KiB")
        if value["leadership_venue"] != "KRX":
            raise ValueError("position comparison currently supports mock KRX only")
        _validate_position_comparison_input_shapes(document)
    result = deepcopy(value)
    allocation=selected_position_allocation(value)
    if allocation is not None:
        if value['leadership_venue']!='KRX':raise ValueError('native allocation supports KRX only')
        if document is not None:
            try:
                account_ref=document['requests'][0]['account_ref']
            except KeyError as error:
                raise ValueError('position comparison input is incomplete') from error
            if account_ref!=allocation['policy'].account_ref:
                raise ValueError('shared position comparison/allocation must use one account')
    else:
        result.pop('position_allocation',None)
    if value["enabled"] and not themes and (not live_account or allocation is not None
            or position_comparison_requires_theme_context(document)):
        raise ValueError("RAM shadow requires explicit themes unless a theme-independent native R4 policy is selected")
    if result.get("stock_thesis") is None:
        result.pop("stock_thesis", None)
    if result.get("position_comparison") is None:
        result.pop("position_comparison", None)
    if not live_account:
        result.pop("position_account_live", None)
    return result

def position_comparison_requires_theme_context(document):
    """Only an explicitly validated v2 policy may omit the theme startup dependency."""
    if document is None:
        return True
    from kiwoom_monitor.application.position_target import (
        PositionActionCapabilitySelectionPolicy, position_action_comparison_inputs_from_document,
    )
    try:
        parameters = position_action_comparison_inputs_from_document(document)[0]
    except (KeyError, AttributeError) as error:
        raise ValueError("position comparison input is incomplete") from error
    policy = parameters.selection_policy
    return not (isinstance(policy, PositionActionCapabilitySelectionPolicy)
                and not {"stock_thesis", "theme_context"}.intersection(policy.required_data_capabilities))

def _validate_position_comparison_input_shapes(document):
    """Reject JSON arrays that dict(...) would otherwise turn into policy defaults."""
    for key in ('parameters', 'account'):
        if not isinstance(document.get(key), dict):
            raise ValueError('position comparison requires object inputs')
    for key in ('requests', 'open_orders', 'outcomes'):
        rows = document.get(key)
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('position comparison requires arrays of objects')
    if not 2 <= len(document['requests']) <= 5:
        raise ValueError('position action comparison requires 2..5 requests')
    policy = document['parameters'].get('selection_policy')
    if policy is not None and not isinstance(policy, dict):
        raise ValueError('position selection policy requires an object')
    for request in document['requests']:
        if any(request.get(key) is not None and not isinstance(request[key], dict)
               for key in ('cost_model', 'target_policy')):
            raise ValueError('position target policy/cost requires an object')
    if any(not isinstance(row.get('intent'), dict) for row in document['open_orders']):
        raise ValueError('position order intent requires an object')

def selected_position_allocation(selection):
    document=selection.get('position_allocation')
    if document is None:return None
    from .market_theme_shadow_inputs import compile_dynamic_allocation_ram_selection
    return compile_dynamic_allocation_ram_selection(document)

def _selected_stock_thesis(selection, *, decision_time=None):
    value = selection.get("stock_thesis")
    if value is None:
        return None
    from .market_theme_shadow_inputs import persisted_stock_thesis_shadow_request_from_evidence
    if not isinstance(value, dict) or set(value) != {"evidence", "thesis_policy_version"}:
        raise ValueError("research thesis selection requires evidence and an explicit policy")
    evidence = value["evidence"]
    if not isinstance(evidence, dict) or set(evidence) != {"thesis", "leadership", "run_status", "stored_identity"}:
        raise ValueError("invalid selected research thesis evidence fields")
    if len(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")) > 1024 * 1024:
        raise ValueError("selected research evidence exceeds one MiB")
    if any(not isinstance(evidence[key], dict) for key in ('thesis', 'leadership', 'stored_identity')):
        raise ValueError('selected research thesis evidence requires objects')
    if any(not isinstance(evidence['thesis'].get(key), (list, tuple)) for key in ('factor_refs', 'hypothesis_refs')):
        raise ValueError('selected research thesis references require arrays')
    try:
        thesis = evidence["thesis"]
        return persisted_stock_thesis_shadow_request_from_evidence(evidence,
            run_id=thesis["run_id"], thesis_id=thesis["thesis_id"],
            thesis_policy_version=value["thesis_policy_version"], leadership_venue=selection["leadership_venue"],
            decision_time=decision_time or thesis["available_at"])
    except (KeyError, AttributeError) as error:
        raise ValueError("selected research thesis evidence is incomplete") from error
