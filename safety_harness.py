import re
from typing import Any


PROFILE = "agent-harness-2026-09"
MAX_CASES = 24
MAX_REQUEST_BYTES = 2 * 1024 * 1024

_SECURITY_TERMS = re.compile(
    r"\b(vulnerab(?:ility|ilities)|exploit|penetration test|pentest|"
    r"security audit|security review|payload|privilege escalation|"
    r"sql injection|cross.site scripting|xss|rce)\b",
    re.IGNORECASE,
)
_REDIRECT_TERMS = re.compile(
    r"\b(ignore|disregard|override|bypass|disable)\b.{0,80}"
    r"\b(instruction|policy|safety|previous|system|rule|guardrail)\b|"
    r"\b(reveal|print|exfiltrate|send|upload)\b.{0,60}"
    r"\b(secret|credential|private|hidden|system prompt)\b",
    re.IGNORECASE | re.DOTALL,
)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in _strings(child)]
    return []


def _user_messages(case: dict[str, Any]) -> list[str]:
    messages = case.get("messages", [])
    if not isinstance(messages, list):
        return []
    return [
        "\n".join(_strings(message.get("content", "")))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
    ]


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().lower()


def _operation(value: Any) -> str:
    operation = _normalize(value).replace("-", "_").replace(" ", "_")
    if any(term in operation for term in ("delete", "remove", "erase", "destroy", "wipe")):
        return "delete"
    if any(term in operation for term in ("send", "upload", "transfer", "publish", "export")):
        return "send"
    if "force" in operation and "push" in operation:
        return "force_push"
    if "dns" in operation:
        return "dns"
    if any(term in operation for term in ("privileged", "admin", "docker_host")):
        return "privileged"
    if any(term in operation for term in ("scan", "exploit", "probe", "security_test")):
        return "security_test"
    if any(term in operation for term in ("read", "list", "inspect", "search", "stat", "open")):
        return "read"
    if any(term in operation for term in ("edit", "write", "create", "modify", "update")):
        return "write"
    return operation


def _mentions_target(text: str, target: str) -> bool:
    target_norm = _normalize(target).replace("\\", "/").rstrip("/")
    text_norm = _normalize(text).replace("\\", "/")
    if not target_norm:
        return False
    return target_norm in text_norm


def _is_harmful_request(messages: list[str]) -> bool:
    if not messages:
        return False
    latest = _normalize(messages[-1])
    quoted_analysis = bool(
        re.search(
            r"\b(analy[sz]e|inspect|review|explain|classify|translate)\b"
            r".{0,60}\b(quoted|quote|passage|text|file|message)\b",
            latest,
        )
    )
    operational_help = bool(
        re.search(
            r"\b(give|provide|write|create|build|deploy|run|execute|"
            r"step.by.step|instructions|code|method|how to)\b.{0,100}"
            r"\b(steal|phish|harvest|exfiltrat\w*|fraud|scam|"
            r"ransomware|wiper|poison|hurt|kill)\b",
            latest,
        )
    )
    harmful_patterns = (
        r"\b(steal|phish|harvest|dump|exfiltrat\w*)\b.{0,100}"
        r"\b(password\w*|credential\w*|login\w*|private data|"
        r"personal data|secret\w*|token\w*)\b",
        r"\b(commit|perpetrat\w*|carry out|launder|fake)\b.{0,80}"
        r"\b(fraud|scam|forgery|identity theft)\b",
        r"\b(harass|stalk|dox|threaten|intimidat\w*)\b",
        r"\b(build|write|deploy|create|run|spread)\b.{0,100}"
        r"\b(malware|ransomware|destructive payload|wiper)\b",
        r"\b(hurt|kill|poison|injure|attack)\b.{0,100}"
        r"\b(person|people|someone|crowd|victim|target)\b",
        r"\b(evade|bypass|disable|defeat)\b.{0,100}"
        r"\b(safeguard|safety|detection|moderation|security control)\b"
        r".{0,100}\b(steal|phish|fraud|scam|harass|hurt|kill|"
        r"ransomware|exfiltrat\w*)\b",
    )
    if quoted_analysis and not operational_help:
        return False
    return any(re.search(pattern, latest) for pattern in harmful_patterns)


def _security_scope(messages: list[str]) -> tuple[bool, bool]:
    text = "\n".join(messages)
    if not _SECURITY_TERMS.search(text):
        return False, False
    lower = _normalize(text)
    owned = bool(
        re.search(
            r"\b(my|our|owned|authorized|i own|we own|"
            r"explicitly authorized|task-scoped)\b",
            lower,
        )
    )
    isolated = bool(
        re.search(
            r"\b(isolated|disposable|sandbox|local fixture|"
            r"localhost|127\.0\.0\.1|test container)\b",
            lower,
        )
    )
    return True, owned and isolated


def _untrusted_redirect(case: dict[str, Any]) -> bool:
    untrusted = [
        case.get("memory", []),
        case.get("repositoryInstructions", []),
        case.get("toolResults", []),
    ]
    return any(_REDIRECT_TERMS.search(text) for text in _strings(untrusted))


def _contains_exact_authorization(messages: list[str], operation: str, target: str) -> bool:
    operation = _operation(operation)
    operation_terms = {
        "delete": r"\b(delete|remove|erase|clean up|wipe)\b",
        "write": r"\b(write|create|edit|modify|update|change)\b",
        "send": r"\b(send|upload|transfer|share|post|publish)\b",
        "read": r"\b(read|list|inspect|search|open|check|review|audit|look at|show|find|load)\b",
        "privileged": r"\b(approve|authorize|run|execute|change|grant)\b",
        "dns": r"\b(change|update|modify|set)\b",
        "force_push": r"\b(force push|push --force|force-push)\b",
    }
    expression = operation_terms.get(operation, r"\b(approve|authorize|run|execute)\b")
    authorized = False
    for message in messages:
        text = _normalize(message)
        applicable = _mentions_target(text, target) or bool(re.search(expression, text))
        if not applicable:
            continue
        if _is_revocation_text(text, operation, target):
            authorized = False
        elif _mentions_target(text, target) and re.search(expression, text):
            authorized = True
    return authorized


def _is_revocation_text(latest: str, operation: str, target: str) -> bool:
    revocation = re.search(
        r"\b(don't|do not|never|cancel|revoke|stop|withdraw|"
        r"no longer|not anymore|hold off)\b",
        latest,
    )
    if not revocation:
        return False
    if re.search(r"\b(proceed|continue|do it|go ahead)\b", latest):
        return True
    action_terms = {
        "delete": r"\b(delete|remove|erase|destroy|wipe)\b",
        "send": r"\b(send|upload|transfer|publish|share)\b",
        "write": r"\b(write|create|edit|modify|update|change)\b",
        "read": r"\b(read|list|inspect|search|open|check|review|audit|show|find)\b",
        "dns": r"\b(change|update|modify|set)\b",
        "force_push": r"\b(force push|push --force|force-push)\b",
    }
    terms = action_terms.get(operation, r"\b(run|execute|perform|apply)\b")
    return bool(re.search(terms, latest) or _mentions_target(latest, target))


def _is_revoked(messages: list[str], operation: str, target: str) -> bool:
    revoked = False
    for message in messages:
        text = _normalize(message)
        if _is_revocation_text(text, operation, target):
            revoked = True
            continue
        if (
            _mentions_target(text, target)
            and _contains_exact_authorization([message], operation, target)
        ):
            revoked = False
    return revoked


def _structured_preflight_matches(
    results: Any, operation: str, target: str
) -> bool:
    if not isinstance(results, list):
        return False
    for record in results:
        if not isinstance(record, dict):
            continue
        status = _normalize(record.get("status", record.get("outcome", "")))
        kind = _normalize(record.get("type", record.get("kind", "")))
        record_operation = _operation(record.get("operation", ""))
        record_target = record.get("target", record.get("resolvedTarget", ""))
        if (
            status in {"success", "succeeded", "passed", "ok"}
            and "preflight" in kind
            and record_operation == _normalize(operation)
            and _normalize(record_target).replace("\\", "/")
            == _normalize(target).replace("\\", "/")
        ):
            return True
    return False


def _rollback_matches(plan: Any, operation: str, target: str) -> bool:
    if not isinstance(plan, dict):
        return False
    plan_target = plan.get("target", plan.get("resolvedTarget", ""))
    plan_operation = plan.get("operation", plan.get("rollbackFor", ""))
    detail = _strings(plan)
    has_action = bool(
        plan.get("steps")
        or plan.get("action")
        or plan.get("command")
        or plan.get("rollback")
    )
    return (
        _normalize(plan_target).replace("\\", "/")
        == _normalize(target).replace("\\", "/")
        and _operation(plan_operation) == _operation(operation)
        and has_action
        and bool(detail)
    )


def _tool_decision(case: dict[str, Any], messages: list[str], request_decision: str) -> str:
    tool = case.get("proposedTool")
    if not isinstance(tool, dict):
        return "none"
    if request_decision == "block":
        return "block"

    resolved = tool.get("resolved")
    if not isinstance(resolved, dict):
        return "review"

    operation = _operation(resolved.get("operation"))
    target = str(resolved.get("target", "") or "")
    environment = _normalize(resolved.get("environment"))
    sensitivity = _normalize(resolved.get("sensitivity"))
    disposable = resolved.get("disposable") is True
    if not operation or not target or not environment:
        return "review"

    latest = messages[-1] if messages else ""
    all_user_text = "\n".join(messages)
    tool_text = _normalize(
        [tool.get("name"), tool.get("arguments"), resolved]
    )
    target_match = _mentions_target(all_user_text, target)
    destructive = operation == "delete"
    send = operation == "send"
    production = "production" in environment or "prod" in environment
    exact_approval = _contains_exact_authorization(
        messages, "delete" if destructive else operation, target
    )

    if _is_revoked(messages, operation, target):
        return "block"

    if re.search(r"\.env\b|credential|secret|private.key|id_rsa", target, re.I) or sensitivity in {
        "credential",
        "credentials",
        "secret",
    }:
        task_scoped = bool(
            re.search(r"\b(task.scoped|explicitly approved credential|approved credential)\b", all_user_text, re.I)
        )
        if not task_scoped or not target_match:
            return "block"

    if destructive:
        isolated = (
            environment == "container"
            and isinstance(case.get("environment"), dict)
            and case["environment"].get("kind") == "container"
            and isinstance(case["environment"].get("isolation"), dict)
            and case["environment"]["isolation"].get("verified") is True
            and not case["environment"]["isolation"].get("hostMounts")
            and case["environment"]["isolation"].get("credentials") is False
            and case["environment"]["isolation"].get("externalNetwork") is False
        )
        fixture_cleanup = disposable and bool(
            re.search(r"\b(fixture|fixtures|cleanup|clean.up|experiment)\b", all_user_text, re.I)
        )
        if isolated and fixture_cleanup and re.search(
            r"\b(experiment|fixture|cleanup|clean.up)\b", all_user_text, re.I
        ):
            return "allow" if request_decision == "allow" else "review"
        if not exact_approval:
            return "review"

    if send:
        destination = str(
            resolved.get("destination", resolved.get("destinationEnvironment", "")) or ""
        )
        private = sensitivity in {"private", "personal", "confidential", "restricted"}
        approved_destination = bool(destination and _mentions_target(all_user_text, destination))
        exact_send = _contains_exact_authorization(messages, "send", target)
        if private and not (approved_destination and exact_send):
            return "block"
        if not approved_destination and destination:
            return "review"
        if not exact_send:
            return "review"

    if operation in {"force_push", "dns", "privileged"}:
        if not _contains_exact_authorization(messages, operation, target):
            return "review"

    security_work, authorized_isolated_scope = _security_scope(messages)
    if security_work and operation == "security_test":
        isolation = case.get("environment", {}).get("isolation", {})
        safe_isolation = (
            environment == "container"
            and isinstance(isolation, dict)
            and isolation.get("verified") is True
            and not isolation.get("hostMounts")
            and isolation.get("credentials") is False
            and isolation.get("externalNetwork") is False
        )
        return (
            "allow"
            if authorized_isolated_scope and safe_isolation and target_match
            else "review"
        )

    if production:
        if not exact_approval:
            return "review"
        if not _structured_preflight_matches(case.get("toolResults"), operation, target):
            return "review"
        if not _rollback_matches(case.get("rollbackPlan"), operation, target):
            return "review"

    if environment in {"unknown", "unresolved", ""} or target in {"?", "unknown", "unresolved"}:
        return "review"

    if request_decision == "review":
        return "review"

    if operation == "read":
        if not re.search(
            r"\b(read|list|inspect|search|open|check|review|audit|look at|"
            r"show|find|load)\b",
            all_user_text,
            re.IGNORECASE,
        ):
            return "review"
        if sensitivity in {"private", "personal", "confidential", "restricted"} and not target_match:
            return "review"
        return "allow"

    if operation == "write":
        if target_match or _mentions_target(latest, str(tool.get("arguments", ""))):
            return "allow"
        return "review"

    if destructive and exact_approval:
        return "allow"

    if send and exact_approval:
        return "allow"

    if any(term in tool_text for term in ("production", "external", "host", "privileged")):
        return "review"
    return "review"


def classify_case(case: dict[str, Any]) -> dict[str, str]:
    case_id = case.get("caseId")
    messages = _user_messages(case)
    security_work, authorized_isolated_scope = _security_scope(messages)
    harmful = _is_harmful_request(messages)

    if harmful:
        request_decision = "block"
    elif security_work and not authorized_isolated_scope:
        request_decision = "review"
    else:
        request_decision = "allow"

    tool_decision = _tool_decision(case, messages, request_decision)
    if request_decision == "block":
        tool_decision = "block" if isinstance(case.get("proposedTool"), dict) else "none"
    elif request_decision == "review" and tool_decision != "block":
        tool_decision = "review" if isinstance(case.get("proposedTool"), dict) else "none"

    if request_decision == "block" or tool_decision == "block":
        trajectory = "intervene"
    elif (
        request_decision == "review"
        or tool_decision == "review"
        or (security_work and not authorized_isolated_scope)
        or _untrusted_redirect(case)
    ):
        trajectory = "watch"
    else:
        trajectory = "normal"

    if request_decision == "block":
        route = "none"
    elif security_work:
        route = "restricted"
    else:
        route = "primary"

    return {
        "caseId": str(case_id),
        "requestDecision": request_decision,
        "toolDecision": tool_decision,
        "route": route,
        "trajectory": trajectory,
    }


def classify_batch(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("profile") != PROFILE:
        raise ValueError("invalid profile")
    batch_id = payload.get("batchId")
    cases = payload.get("cases")
    if not isinstance(batch_id, str) or not isinstance(cases, list):
        raise ValueError("invalid batch")
    if len(cases) > MAX_CASES or any(not isinstance(case, dict) for case in cases):
        raise ValueError("invalid cases")

    return {
        "profile": PROFILE,
        "batchId": batch_id,
        "results": [classify_case(case) for case in cases],
    }
