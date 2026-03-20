from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_USAGE_LOG = PROJECT_ROOT / "data" / "token_usage.jsonl"


@dataclass(frozen=True)
class UsageEntry:
    timestamp_utc: str
    model: str
    base_url: str | None
    request_id: str | None
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    max_tokens: int
    latency_ms: int


@dataclass(frozen=True)
class UsageSummary:
    request_count: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


@dataclass(frozen=True)
class AverageTpm:
    input_tpm: float
    output_tpm: float
    total_tpm: float


def parse_timestamp(timestamp_utc: str) -> datetime:
    return datetime.fromisoformat(timestamp_utc)


def build_usage_entry(
    *,
    model: str,
    base_url: str | None,
    max_tokens: int,
    latency_ms: int,
    usage: Any,
    request_id: str | None,
) -> UsageEntry:
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    cache_creation_input_tokens = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cache_read_input_tokens = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    total_tokens = input_tokens + output_tokens
    timestamp_utc = datetime.now(timezone.utc).isoformat()

    return UsageEntry(
        timestamp_utc=timestamp_utc,
        model=model,
        base_url=base_url,
        request_id=request_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        max_tokens=max_tokens,
        latency_ms=latency_ms,
    )


def append_usage_entry(entry: UsageEntry, log_file: Path = DEFAULT_USAGE_LOG) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(asdict(entry), sort_keys=True))
        file.write("\n")


def read_usage_entries(log_file: Path = DEFAULT_USAGE_LOG) -> list[UsageEntry]:
    if not log_file.exists():
        return []

    entries: list[UsageEntry] = []
    with log_file.open("r", encoding="utf-8") as file:
        for line in file:
            stripped_line = line.strip()
            if not stripped_line:
                continue
            payload = json.loads(stripped_line)
            entries.append(UsageEntry(**payload))
    return entries


def summarize_usage(entries: Iterable[UsageEntry]) -> UsageSummary:
    request_count = 0
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0

    for entry in entries:
        request_count += 1
        input_tokens += entry.input_tokens
        output_tokens += entry.output_tokens
        total_tokens += entry.total_tokens
        cache_creation_input_tokens += entry.cache_creation_input_tokens
        cache_read_input_tokens += entry.cache_read_input_tokens

    return UsageSummary(
        request_count=request_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
    )


def calculate_rolling_tpm(
    entries: Iterable[UsageEntry],
    *,
    window_seconds: int = 60,
    now: datetime | None = None,
) -> int:
    window_end = now or datetime.now(timezone.utc)
    window_start = window_end - timedelta(seconds=window_seconds)
    total_tokens = 0

    for entry in entries:
        entry_time = parse_timestamp(entry.timestamp_utc)
        if window_start <= entry_time <= window_end:
            total_tokens += entry.total_tokens

    return total_tokens


def calculate_average_tpm(entry: UsageEntry) -> AverageTpm:
    elapsed_minutes = entry.latency_ms / 60000
    if elapsed_minutes <= 0:
        return AverageTpm(
            input_tpm=0.0,
            output_tpm=0.0,
            total_tpm=0.0,
        )

    return AverageTpm(
        input_tpm=entry.input_tokens / elapsed_minutes,
        output_tpm=entry.output_tokens / elapsed_minutes,
        total_tpm=entry.total_tokens / elapsed_minutes,
    )


def format_usage_report(entries: list[UsageEntry]) -> str:
    summary = summarize_usage(entries)
    lines = [
        f"log_file={DEFAULT_USAGE_LOG}",
        f"requests={summary.request_count}",
        f"input_tokens={summary.input_tokens}",
        f"output_tokens={summary.output_tokens}",
        f"total_tokens={summary.total_tokens}",
        f"cache_creation_input_tokens={summary.cache_creation_input_tokens}",
        f"cache_read_input_tokens={summary.cache_read_input_tokens}",
    ]

    if entries:
        last_entry = entries[-1]
        lines.extend(
            [
                f"last_request_at={last_entry.timestamp_utc}",
                f"last_model={last_entry.model}",
                f"last_total_tokens={last_entry.total_tokens}",
            ]
        )

    return "\n".join(lines)


def build_usage_payload(entries: list[UsageEntry]) -> dict[str, Any]:
    summary = summarize_usage(entries)
    payload: dict[str, Any] = {
        "log_file": str(DEFAULT_USAGE_LOG),
        "requests": summary.request_count,
        "input_tokens": summary.input_tokens,
        "output_tokens": summary.output_tokens,
        "total_tokens": summary.total_tokens,
        "cache_creation_input_tokens": summary.cache_creation_input_tokens,
        "cache_read_input_tokens": summary.cache_read_input_tokens,
    }

    if entries:
        last_entry = entries[-1]
        payload["last_request_at"] = last_entry.timestamp_utc
        payload["last_model"] = last_entry.model
        payload["last_total_tokens"] = last_entry.total_tokens

    return payload


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Show the persisted token usage summary.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the usage summary as JSON.",
    )
    parser.add_argument(
        "--total-only",
        action="store_true",
        help="Print only the lifetime total token count.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    entries = read_usage_entries()
    if args.total_only:
        print(summarize_usage(entries).total_tokens)
        return

    if args.json:
        print(json.dumps(build_usage_payload(entries), indent=2, sort_keys=True))
        return

    print(format_usage_report(entries))


if __name__ == "__main__":
    main()
