"""Decode collector responses without exposing scrape configuration in errors."""

from math import isfinite
from typing import Literal

from harness_common.scrape import ScrapeConfig, ScrapeTarget

from harness_toolbox.errors import ErrorKind, MetricDBQueryError


def _invalid() -> MetricDBQueryError:
    return MetricDBQueryError("Invalid MetricDB scrape response", kind=ErrorKind.INVALID_RESPONSE)


def _record(value: object) -> dict:
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _string(record: dict, key: str) -> str | None:
    if key in record and not isinstance(record[key], str):
        raise _invalid()
    return record.get(key)


def _labels(value: object) -> dict[str, str] | None:
    if value is None:
        return None
    record = _record(value)
    if any(not isinstance(v, str) for v in record.values()):
        raise _invalid()
    return record


def scrape_configs(data: dict) -> list[ScrapeConfig]:
    import yaml

    source = data.get("yaml")
    if not isinstance(source, str):
        raise _invalid()
    try:
        config = _record(yaml.safe_load(source))
    except (yaml.YAMLError, RecursionError):
        raise _invalid() from None
    global_config = _record(config.get("global", {}))
    interval = _string(global_config, "scrape_interval")
    timeout = _string(global_config, "scrape_timeout")
    jobs = config.get("scrape_configs", [])
    if not isinstance(jobs, list):
        raise _invalid()
    result = []
    for item in jobs:
        job = _record(item)
        name = _string(job, "job_name")
        if not name or not name.strip():
            raise _invalid()
        result.append(
            ScrapeConfig(
                job_name=name,
                scrape_interval=_string(job, "scrape_interval")
                if "scrape_interval" in job
                else interval,
                scrape_timeout=_string(job, "scrape_timeout")
                if "scrape_timeout" in job
                else timeout,
                metrics_path=_string(job, "metrics_path"),
                scheme=_string(job, "scheme"),
            )
        )
    return result


def scrape_targets(
    data: dict, state_filter: Literal["active", "dropped", "any"]
) -> list[ScrapeTarget]:
    result = []
    states: tuple[Literal["active", "dropped"], ...] = ("active", "dropped")
    for state in states:
        if state_filter != "any" and state_filter != state:
            continue
        group = data.get(state + "Targets")
        if not isinstance(group, list):
            raise _invalid()
        for item in group:
            target = _record(item)
            duration = target.get("lastScrapeDuration")
            if "lastScrapeDuration" in target and (
                type(duration) not in (float, int) or not isfinite(duration)
            ):
                raise _invalid()
            url = _string(target, "scrapeUrl")
            discovered = _labels(target.get("discoveredLabels"))
            if (state == "active" and url is None) or (state == "dropped" and discovered is None):
                raise _invalid()
            result.append(
                ScrapeTarget(
                    state=state,
                    discovered_labels=discovered,
                    labels=_labels(target.get("labels")),
                    scrape_pool=_string(target, "scrapePool"),
                    scrape_url=url,
                    health=_string(target, "health"),
                    last_error=_string(target, "lastError"),
                    last_scrape=_string(target, "lastScrape"),
                    last_scrape_duration=duration,
                    scrape_interval=_string(target, "scrapeInterval"),
                    scrape_timeout=_string(target, "scrapeTimeout"),
                )
            )
    return result
