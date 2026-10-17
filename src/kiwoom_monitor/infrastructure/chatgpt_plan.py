"""Account catalog and completed-only public Responses/SSE for ChatGPT plans."""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.error import HTTPError
from urllib.request import Request

from kiwoom_monitor.infrastructure.chatgpt_auth import (
    ChatGPTAuth, ChatGPTPlanError, RESOURCE, _json_request, safe_request_id,
)


def reasoning_choices(model: str) -> tuple[str, ...]:
    """Verified public model support; unknown models keep their service default."""
    if model in {"gpt-6.1-sol", "gpt-6-astra"}:
        return ("low", "medium", "high", "xhigh", "max")
    if model in {"gpt-6-sol", "gpt-6-luna", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}:
        return ("none", "low", "medium", "high", "xhigh", "max")
    return ()


def validate_reasoning(model: str, effort: str) -> None:
    if effort and effort not in reasoning_choices(model):
        raise ValueError("선택한 모델에서 지원하지 않는 추론 수준입니다.")


@dataclass(frozen=True)
class GroundedResponse:
    text: str
    usage: dict
    sources: tuple[dict, ...] = ()
    search_calls: int = 0
    web_actions: tuple[dict, ...] = ()


def search_evidence(final: dict, *, actions: list[dict] | None = None) -> tuple[tuple[dict, ...], int]:
    """Retain tool-returned URLs, never trust URLs invented in assistant JSON."""
    sources: dict[str, dict] = {}
    calls = 0
    for item in final.get("output", []):
        if item.get("type") == "web_search_call":
            calls += 1
            action = item.get("action", {})
            candidates = list(action.get("sources", [])) if item.get("status") == "completed" else []
            if item.get("status") == "completed" and action.get("type") in {"open_page", "find_in_page"}:
                candidates.append({"url": action.get("url")})
            action_urls = []
        else:
            candidates = [a for c in item.get("content", []) for a in c.get("annotations", [])
                          if a.get("type") == "url_citation"]
        for source in candidates:
            url = source.get("url")
            if (isinstance(url, str) and len(url) <= 4000
                    and urlsplit(url).scheme in {"http", "https"} and urlsplit(url).hostname
                    and not urlsplit(url).username and not urlsplit(url).password):
                sources[url] = {"url": url, "title": str(source.get("title") or "")[:500]}
                if item.get("type") == "web_search_call":
                    action_urls.append(url)
        if item.get("type") == "web_search_call" and actions is not None:
            actions.append({"type": action.get("type") if action.get("type") in {
                "search", "open_page", "find_in_page"} else "other",
                "urls": list(dict.fromkeys(action_urls))})
            if item.get("status") != "completed":
                actions[-1]["status"] = item.get("status") if item.get("status") in {
                    "failed", "in_progress", "incomplete"} else "unknown"
    return tuple(sources.values()), calls


class ChatGPTPlanClient:
    def __init__(self, auth: ChatGPTAuth):
        self.auth = auth

    def _models(self, account: dict) -> tuple[tuple[str, str], ...]:
        result = _json_request(Request(RESOURCE + "/models", headers={
            "Authorization": "Bearer " + account["access_token"]}), self.auth.opener)
        models = result.get("models")
        if not isinstance(models, list):
            raise ChatGPTPlanError("response_invalid")
        visible = []
        for model in models:
            if not isinstance(model, dict) or model.get("visibility") != "list":
                continue
            if not isinstance(model.get("slug"), str) or not model["slug"] or not isinstance(model.get("display_name"), str):
                raise ChatGPTPlanError("response_invalid")
            visible.append((model["display_name"], model["slug"]))
        return tuple(visible)

    def models(self) -> tuple[tuple[str, str], ...]:
        with self.auth.session() as account:
            return self._models(account)

    def respond(self, model: str, prompt: str, *, cancel: threading.Event | None = None,
                reasoning_effort: str = "") -> tuple[str, dict]:
        result = self._respond(model, prompt, cancel=cancel, reasoning_effort=reasoning_effort)
        return result.text, result.usage

    def research(self, model: str, prompt: str, *, reasoning_effort: str = "",
                 stream_timeout: float | None = 600) -> GroundedResponse:
        return self._respond(model, prompt, reasoning_effort=reasoning_effort, web_search=True,
                             stream_timeout=stream_timeout)

    def _respond(self, model: str, prompt: str, *, cancel=None, reasoning_effort: str = "",
                 web_search: bool = False, stream_timeout: float | None = 180) -> GroundedResponse:
        validate_reasoning(model, reasoning_effort)
        if cancel is not None and cancel.is_set():
            raise ChatGPTPlanError("cancelled")
        with self.auth.session() as account:
            if cancel is not None and cancel.is_set():
                raise ChatGPTPlanError("cancelled")
            if model not in {slug for _label, slug in self._models(account)}:
                raise ChatGPTPlanError("model_unavailable")
            payload = {"model": model, "store": False, "stream": True,
                       "input": [{"role": "user", "content": prompt}]}
            if reasoning_effort:
                payload["reasoning"] = {"effort": reasoning_effort}
            if web_search:
                payload.update(tools=[{"type": "web_search"}], tool_choice="required",
                               include=["web_search_call.action.sources"])
            request = Request(RESOURCE + "/responses", method="POST", headers={
                "Authorization": "Bearer " + account["access_token"],
                "Content-Type": "application/json", "Accept": "text/event-stream",
            }, data=json.dumps(payload, ensure_ascii=False).encode())
            try:
                with self.auth.opener(request) as response:
                    response_status = int(response.status)
                    request_id = safe_request_id(response.headers, request)
                    content_type = response.headers.get("Content-Type") or response.headers.get("content-type") or ""
                    # Some direct-route responses omit this header. Their body
                    # must still pass the complete SSE terminal/output checks.
                    if content_type and "text/event-stream" not in content_type.lower():
                        error = ChatGPTPlanError("response_invalid", response_status, request_id=request_id, response_failure="stream_content_type")
                        error.response_diagnostics = _response_shape(response)
                        raise error
                    try:
                        final = _completed_response(response, cancel, stream_timeout=stream_timeout)
                    except ChatGPTPlanError as error:
                        error.request_id = safe_request_id(response.headers, request)
                        error.status_code = response_status
                        raise
            except HTTPError as error:
                try:
                    value = json.loads(error.read(65536))
                    detail = value.get("error") if isinstance(value, dict) else None
                    code = detail.get("code") if isinstance(detail, dict) else "upstream_error"
                    shape = "error" if detail is not None else "detail" if isinstance(value, dict) and "detail" in value else "other"
                except Exception:
                    code, shape = "upstream_error", "other"
                finally:
                    error.close()
                raise ChatGPTPlanError(str(code), error.code, body_shape=shape, request_id=safe_request_id(error.headers, request), inference_attempted=True) from None
            except ChatGPTPlanError as error:
                error.inference_attempted = True
                raise
            except Exception:
                raise ChatGPTPlanError("stream_interrupted", inference_attempted=True) from None
            try:
                actions = []
                sources, calls = search_evidence(final, actions=actions) if web_search else ((), 0)
                return GroundedResponse(_final_text(final), _usage(final), sources, calls, tuple(actions))
            except ValueError:
                raise ChatGPTPlanError("response_invalid", inference_attempted=True,
                                       response_failure="web_search_evidence_invalid") from None
            except ChatGPTPlanError as error:
                error.inference_attempted = True
                error.status_code = response_status
                error.request_id = request_id
                output = final.get("output", [])
                items = output if isinstance(output, list) else []
                error.response_diagnostics = {**final.get("_stream_diagnostics", {}),
                    "final_output_count": len(items),
                    "final_message_count": sum(isinstance(item, dict) and item.get("type") == "message" for item in items),
                    "final_reasoning_count": sum(isinstance(item, dict) and item.get("type") == "reasoning" for item in items),
                    "final_output_text_present": isinstance(final.get("output_text"), str) and bool(final["output_text"]),
                    "final_usage_present": isinstance(final.get("usage"), dict)}
                raise


def _response_shape(response) -> dict:
    """Fixed structural diagnostics only; never return headers/body values."""
    content_type = (response.headers.get("Content-Type") or response.headers.get("content-type") or "").lower()
    category = next((name for prefix, name in (("application/json", "json"),
        ("text/html", "html"), ("text/plain", "text"), ("text/event-stream", "sse"))
        if content_type.startswith(prefix)), "other" if content_type else "missing")
    result = {"content_type_category": category}
    try:
        body = response.read(65536).lstrip()
        result["body_is_sse"] = body.startswith((b"event:", b"data:", b":"))
        if body.startswith(b"{"):
            value = json.loads(body)
            result["body_is_json_object"] = isinstance(value, dict)
            if isinstance(value, dict):
                for name in ("id", "object", "status", "output", "output_text", "usage", "error", "detail"):
                    result[name + "_present"] = name in value
                result["response_completed"] = value.get("status") == "completed"
        else:
            result["body_is_json_object"] = False
    except Exception:
        result["shape_read_failed"] = True
    return result


def _completed_response(response, cancel: threading.Event | None, *, stream_timeout: float | None = 180) -> dict:
    data: list[str] = []
    event_name = ""
    size = 0
    counts = {kind: 0 for kind in ("response.created", "response.output_item.added",
        "response.content_part.added", "response.output_text.delta", "response.output_text.done",
        "response.content_part.done", "response.output_item.done", "response.completed")}
    response_id = ""
    added: dict[int, str] = {}
    completed_items: dict[int, dict] = {}
    deadline = time.monotonic() + stream_timeout if stream_timeout is not None else None
    while True:
        if cancel is not None and cancel.is_set():
            raise ChatGPTPlanError("cancelled")
        if deadline is not None and time.monotonic() >= deadline:
            raise ChatGPTPlanError("timeout")
        raw = response.readline(1024 * 1024 + 1)
        if cancel is not None and cancel.is_set():
            raise ChatGPTPlanError("cancelled")
        if not raw:
            raise ChatGPTPlanError("stream_interrupted")
        size += len(raw)
        if size > 8 * 1024 * 1024 or len(raw) > 1024 * 1024:
            raise ChatGPTPlanError("response_invalid", response_failure="stream_size")
        try:
            line = raw.decode("utf-8").rstrip("\r\n")
        except UnicodeError:
            raise ChatGPTPlanError("response_invalid", response_failure="stream_encoding") from None
        if line.startswith("data:"):
            data.append(line[5:].lstrip(" "))
        elif line.startswith("event:"):
            event_name = line[6:].strip()
        elif not line:
            if not data:
                event_name = ""
                continue
            try:
                event = json.loads("\n".join(data))
                kind = event["type"]
                if event_name and event_name != kind:
                    raise ChatGPTPlanError("response_invalid", response_failure="event_type_mismatch")
            except ChatGPTPlanError:
                raise
            except Exception:
                raise ChatGPTPlanError("response_invalid", response_failure="event_json") from None
            data, event_name = [], ""
            if kind in counts:
                counts[kind] += 1
            if kind == "response.created":
                created = event.get("response")
                response_id = str(created.get("id") or "") if isinstance(created, dict) else ""
            if kind in {"response.output_item.added", "response.output_item.done"}:
                index, item = event.get("output_index"), event.get("item")
                if type(index) is not int or index < 0 or not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise ChatGPTPlanError("response_invalid", response_failure="output_reconstruction")
                if kind == "response.output_item.added":
                    if index in added:
                        raise ChatGPTPlanError("response_invalid", response_failure="output_reconstruction")
                    added[index] = item["id"]
                else:
                    if index in completed_items or added.get(index) != item["id"]:
                        raise ChatGPTPlanError("response_invalid", response_failure="output_reconstruction")
                    completed_items[index] = item
            if kind == "response.completed":
                final = event.get("response")
                if not isinstance(final, dict) or final.get("status") != "completed" or final.get("error"):
                    raise ChatGPTPlanError("response_invalid", response_failure="completed_status")
                if final.get("output") == [] and completed_items:
                    # output_item.done contains the authoritative complete item;
                    # never reconstruct a successful response from text deltas.
                    if (not response_id or final.get("id") != response_id or set(added) != set(completed_items)
                            or set(completed_items) != set(range(len(completed_items)))):
                        raise ChatGPTPlanError("response_invalid", response_failure="output_reconstruction")
                    final["output"] = [completed_items[index] for index in range(len(completed_items))]
                final["_stream_diagnostics"] = {"event_counts": counts}
                return final
            if kind == "response.incomplete":
                raise ChatGPTPlanError("response_incomplete")
            if kind in {"response.failed", "error"}:
                final = event.get("response") or event
                error = final.get("error") or final
                code = error.get("code", "upstream_error") if isinstance(error, dict) else "upstream_error"
                raise ChatGPTPlanError(str(code))
            # Deltas and output_text.done are never successful results.


def _final_text(final: dict) -> str:
    parts = []
    output = final.get("output")
    if not isinstance(output, list):
        raise ChatGPTPlanError("response_invalid", response_failure="output_schema")
    for item in output:
        if not isinstance(item, dict):
            raise ChatGPTPlanError("response_invalid", response_failure="output_item")
        if item.get("type") != "message":
            continue
        if item.get("status") != "completed":
            raise ChatGPTPlanError("response_invalid", response_failure="message_status")
        if item.get("role") != "assistant":
            raise ChatGPTPlanError("response_invalid", response_failure="message_role")
        if not isinstance(item.get("content"), list):
            raise ChatGPTPlanError("response_invalid", response_failure="message_content")
        for content in item["content"]:
            if not isinstance(content, dict) or content.get("type") != "output_text" or not isinstance(content.get("text"), str):
                raise ChatGPTPlanError("response_invalid", response_failure="text_content")
            parts.append(content["text"])
    text = "".join(parts)
    if not text.strip():
        raise ChatGPTPlanError("response_invalid", response_failure="empty_output")
    return text


def _usage(final: dict) -> dict:
    usage = final.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(key)) is not int or usage[key] < 0
            for key in ("input_tokens", "output_tokens", "total_tokens")):
        raise ChatGPTPlanError("response_invalid", response_failure="usage_fields")
    return {key: usage[key] for key in ("input_tokens", "output_tokens", "total_tokens")}
