import json

from perf_harness.observe.k8s import (
    _parse_cpu_m,
    _parse_mem_mi,
    parse_kubectl_top,
    parse_kubectl_top_per_pod,
    parse_pod_counts,
    parse_pod_resources,
    parse_ps_rss,
)


def test_parse_cpu_m():
    assert _parse_cpu_m("500m") == 500.0
    assert _parse_cpu_m("2") == 2000.0
    assert _parse_cpu_m("1.5") == 1500.0
    assert _parse_cpu_m("") is None and _parse_cpu_m("bad") is None


def test_parse_mem_mi():
    assert _parse_mem_mi("2Gi") == 2048.0
    assert _parse_mem_mi("512Mi") == 512.0
    assert _parse_mem_mi("1Gi") == 1024.0  # "Gi" matched before "G"
    assert _parse_mem_mi(str(1024 * 1024)) == 1.0  # bare bytes → MiB
    assert _parse_mem_mi("") is None and _parse_mem_mi("bad") is None


def test_parse_kubectl_top():
    cpu, mem = parse_kubectl_top("example-abc123   850m   1234Mi\n")
    assert cpu == 850.0
    assert mem == 1234.0


def test_parse_kubectl_top_empty():
    assert parse_kubectl_top("") == (None, None)


def test_parse_kubectl_top_per_pod_and_summed_agree():
    text = "chat-abc   850m   1234Mi\nchat-def   150m   766Mi\n"
    per = parse_kubectl_top_per_pod(text)
    assert per == {"chat-abc": (850.0, 1234.0), "chat-def": (150.0, 766.0)}
    # the summed view derives from the same per-pod rows — they can't disagree
    assert parse_kubectl_top(text) == (1000.0, 2000.0)
    assert parse_kubectl_top_per_pod("") == {}


def test_parse_pod_resources():
    j = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "chat-abc"},
                    "spec": {
                        "containers": [
                            {
                                "resources": {
                                    "requests": {"cpu": "500m", "memory": "1Gi"},
                                    "limits": {"cpu": "1", "memory": "2Gi"},
                                }
                            },
                            # sidecar with requests only — its cpu still counts there
                            {"resources": {"requests": {"cpu": "100m"}}},
                        ]
                    },
                },
                # nothing set on any container → the pod is omitted entirely
                {
                    "metadata": {"name": "chat-def"},
                    "spec": {"containers": [{"resources": {}}]},
                },
            ]
        }
    )
    out = parse_pod_resources(j)
    assert out == {
        "chat-abc": {
            "cpu_request": 600.0,
            "mem_request": 1024.0,
            "cpu_limit": 1000.0,
            "mem_limit": 2048.0,
        }
    }
    assert parse_pod_resources("not json") == {}  # probe skips itself this tick


def test_parse_pod_counts():
    text = json.dumps(
        {
            "items": [
                {
                    "metadata": {"name": "ready"},
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                },
                {
                    "metadata": {"name": "blocked"},
                    "status": {
                        "phase": "Pending",
                        "conditions": [
                            {
                                "type": "PodScheduled",
                                "status": "False",
                                "reason": "Unschedulable",
                            }
                        ],
                    },
                },
                {
                    "metadata": {
                        "name": "terminating",
                        "deletionTimestamp": "2026-01-01T00:00:00Z",
                    },
                    "status": {
                        "phase": "Running",
                        "conditions": [{"type": "Ready", "status": "True"}],
                    },
                },
                {
                    "metadata": {"name": "completed"},
                    "status": {"phase": "Succeeded", "conditions": []},
                },
            ]
        }
    )
    assert parse_pod_counts(text) == {
        "total": 4,
        "active": 3,
        "ready": 1,
        "running": 2,
        "pending": 1,
        "unschedulable": 1,
        "terminating": 1,
    }


def test_parse_ps_rss():
    text = (
        "  PID   RSS COMMAND\n"
        "    1   1024 /usr/bin/python3 -m uvicorn main:create_app --factory --workers 2\n"
        "    7 460800 python3 -c from multiprocessing.spawn import spawn_main; spawn_main()\n"
        "    8 471040 python3 -c from multiprocessing.spawn import spawn_main; spawn_main()\n"
    )
    n_workers, rss_mi = parse_ps_rss(text)
    assert n_workers == 2
    assert abs(rss_mi - (1024 + 460800 + 471040) / 1024.0) < 0.1
