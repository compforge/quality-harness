"""Translate the Kubernetes label-selector model without broadening its match set."""

from kubernetes_asyncio import client as kubernetes


def label_selector(selector: kubernetes.V1LabelSelector | None) -> str:
    if selector is None:
        raise ValueError("Resource has no Pod selector")
    terms = [f"{key}={value}" for key, value in sorted((selector.match_labels or {}).items())]
    for requirement in selector.match_expressions or []:
        key, operator, values = requirement.key, requirement.operator, requirement.values or []
        if operator in ("In", "NotIn") and values:
            word = "in" if operator == "In" else "notin"
            terms.append(f"{key} {word} ({','.join(sorted(values))})")
        elif operator in ("Exists", "DoesNotExist") and not values:
            terms.append(key if operator == "Exists" else f"!{key}")
        else:
            raise ValueError(f"Invalid Pod selector requirement: {key!r} {operator!r}")
    if not terms:
        # An empty API selector means all Pods. Resource-based access must never silently broaden.
        raise ValueError("Resource has no Pod selector")
    return ",".join(terms)
