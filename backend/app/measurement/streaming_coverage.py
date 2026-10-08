"""Streaming-coverage measurement (task `streaming-coverage-measurement`).

Observes a RUNNING backend from the outside and reports which monitored
symbols produced ticks, closed 1m candles and 1m feature updates on the
backend's own ``/ws`` boundary during an explicit measurement window.

WHAT THIS IS NOT
----------------
It never connects a market-data provider, requests feeds, edits the scanner
universe or starts trading. Its only side effects on the backend are one
extra WebSocket connection that subscribes to three existing outbound
channels, and two read-only HTTP GETs per run (``/market/subscription-status``
at the beginning and end, plus ``/scanner/universe`` when the saved universe
is the monitored set).

Counts are events observed at the backend WebSocket boundary. They do not
prove provider capacity, lossless upstream delivery, profitability or full
protective coverage. A symbol with no events may be quiet, outside market
hours, unsubscribed, or faulty; this module never classifies the cause. The
beginning/end identity snapshots cannot prove that nothing changed between
them. No automation-enablement threshold is defined here.

Structure (see docs/architecture/scanner-design.md §18.17):
  config/validation -> HTTP reads -> WebSocket reader -> CoverageCollector
  (bounded, synchronous, clock-injected) -> build_report -> console / JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import math
import os
import re
import signal
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TextIO
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from app.scanner.universe import is_valid_ticker_format

SCHEMA = "streaming-coverage/1"

# Existing outbound channels (app/api/websocket/channels.py EVENT_TO_CHANNEL).
CH_TICK = "market.tick"
CH_CANDLE = "market.candle"
CH_FEATURES = "features.updated"
CHANNELS: tuple[str, ...] = (CH_TICK, CH_CANDLE, CH_FEATURES)
CATEGORY_BY_CHANNEL: dict[str, str] = {
    CH_TICK: "ticks",
    CH_CANDLE: "candles_1m",
    CH_FEATURES: "features_1m",
}
CATEGORIES: tuple[str, ...] = tuple(CATEGORY_BY_CHANNEL.values())
TIMEFRAME = "1m"

STATUS_PATH = "/market/subscription-status"
UNIVERSE_PATH = "/scanner/universe"
WS_PATH = "/ws"

# Exit codes.
EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_SETUP_FAILED = 3
EXIT_INTERRUPTED = 4
EXIT_REPORT_WRITE_FAILED = 5

# Hard bounds on everything the collector may retain beyond per-symbol stats.
MAX_ANOMALY_SAMPLES = 20
MAX_UNMONITORED_SAMPLE_SYMBOLS = 20
MAX_DISTINCT_LABELS = 16  # distinct timeframe / channel labels counted before folding into "other"
MAX_EXCERPT_CHARS = 120
MAX_HTTP_BODY_BYTES = 1 << 20

INTERPRETATION: tuple[str, ...] = (
    "Counts are events observed at the backend WebSocket boundary during the window only.",
    "They do not prove provider capacity, lossless upstream delivery, profitability or full protective coverage.",
    "A symbol with no events may be quiet, outside market hours, not subscribed, or faulty; the cause is not classified.",
    "Beginning/end subscription snapshots cannot prove that no provider change occurred between them.",
    "Local inventory is the adapter's own record of requests, not provider acknowledgement or verified capacity.",
    "Tick regression classes compare source minutes against each symbol's prior high-water mark; they do not diagnose the cause.",
    "No automation-enablement threshold is defined, and counts alone do not support a no-dropped-ticks claim.",
)


class InputError(ValueError):
    """Invalid command-line / configuration input (exit code 2)."""


class SetupError(RuntimeError):
    """Precondition or setup failure before the window begins (exit code 3)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

_SENSITIVE_PAIR = re.compile(
    r"(?i)\b(token|api[_-]?key|apikey|key|secret|password|passwd|pwd|auth|authorization|access[_-]?token)=([^\s&#'\"]+)"
)
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s@]+@")


def redact_url(url: str) -> str:
    """URL safe to print/store: userinfo -> ``***``, every query value -> ``REDACTED``, fragment dropped."""
    try:
        parts = urlsplit(url)
        netloc = parts.netloc
        if "@" in netloc:
            netloc = "***@" + netloc.rsplit("@", 1)[1]
        query = ""
        if parts.query:
            query = "&".join(f"{k}=REDACTED" for k, _ in parse_qsl(parts.query, keep_blank_values=True))
        return urlunsplit((parts.scheme, netloc, parts.path, query, ""))
    except ValueError:
        return "<unparseable-url>"


class Redactor:
    """Scrubs credentials from free text (exception messages, close reasons)."""

    def __init__(self, raw_urls: tuple[str, ...] = ()) -> None:
        secrets: set[str] = set()
        for raw in raw_urls:
            if not raw:
                continue
            with contextlib.suppress(ValueError):
                parts = urlsplit(raw)
                carries_credentials = False
                for value in (parts.username, parts.password):
                    if value:
                        carries_credentials = True
                        secrets.add(value)
                        secrets.add(unquote(value))
                if "@" in parts.netloc:
                    secrets.add(parts.netloc.rsplit("@", 1)[0])
                for _, query_value in parse_qsl(parts.query, keep_blank_values=False):
                    carries_credentials = True
                    secrets.add(query_value)
                if carries_credentials:
                    secrets.add(raw)  # a plain host:port URL is not a secret and stays readable in errors
        # Longest first so a whole URL is replaced before its components.
        self._secrets = sorted((s for s in secrets if len(s) >= 1), key=len, reverse=True)

    def __call__(self, text: object) -> str:
        out = str(text)
        for secret in self._secrets:
            out = out.replace(secret, "***")
        out = _URL_USERINFO.sub(r"\1***@", out)
        out = _SENSITIVE_PAIR.sub(r"\1=REDACTED", out)
        return out


# ---------------------------------------------------------------------------
# Configuration / input validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MeasurementConfig:
    http_base: str  # no userinfo, no trailing slash
    ws_url: str  # no userinfo
    auth_header: str | None
    redacted_backend: str
    raw_urls: tuple[str, ...]
    symbols: tuple[str, ...] | None  # None -> capture the saved scanner universe
    duration_s: float
    connect_timeout_s: float = 10.0
    http_timeout_s: float = 10.0
    ack_timeout_s: float = 10.0
    duplicates_dropped: int = 0

    @property
    def symbol_source(self) -> str:
        return "scanner_universe" if self.symbols is None else "explicit"


def parse_symbols(text: str) -> tuple[tuple[str, ...], int]:
    """Comma/space separated tickers -> (ordered unique uppercase tuple, duplicates dropped)."""
    seen: dict[str, None] = {}
    duplicates = 0
    for token in re.split(r"[,\s]+", text.strip()):
        if not token:
            continue
        symbol = token.upper()
        if not is_valid_ticker_format(symbol):
            raise InputError(f"invalid ticker format: {token!r} (1-5 letters, optional .X share-class suffix)")
        if symbol in seen:
            duplicates += 1
        else:
            seen[symbol] = None
    if not seen:
        raise InputError("--symbols contained no symbols")
    return tuple(seen), duplicates


def _positive_finite(name: str, value: float) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
        raise InputError(f"{name} must be a positive, finite number")
    return float(value)


def build_config(
    backend_url: str,
    *,
    symbols: str | None,
    scanner_universe: bool,
    duration_s: float,
    connect_timeout_s: float = 10.0,
    http_timeout_s: float = 10.0,
    ack_timeout_s: float = 10.0,
) -> MeasurementConfig:
    if bool(symbols) == bool(scanner_universe):
        raise InputError("choose exactly one of --symbols or --scanner-universe")
    duration = _positive_finite("--duration", duration_s)
    connect_t = _positive_finite("--connect-timeout", connect_timeout_s)
    http_t = _positive_finite("--http-timeout", http_timeout_s)
    ack_t = _positive_finite("--ack-timeout", ack_timeout_s)

    raw = (backend_url or "").strip()
    try:
        parts = urlsplit(raw)
        port = parts.port  # validates the port number
        username, password = parts.username, parts.password
        hostname = parts.hostname
    except ValueError:
        raise InputError("backend URL is not a valid URL") from None
    if parts.scheme.lower() not in ("http", "https", "ws", "wss") or not hostname:
        raise InputError("backend URL must look like http(s)://host[:port] or ws(s)://host[:port]")
    if parts.query or parts.fragment:
        raise InputError("backend URL must not contain a query string or fragment")

    secure = parts.scheme.lower() in ("https", "wss")
    host = f"[{hostname}]" if ":" in hostname else hostname
    hostport = host if port is None else f"{host}:{port}"
    path = parts.path.rstrip("/")
    http_base = f"{'https' if secure else 'http'}://{hostport}{path}"
    ws_url = f"{'wss' if secure else 'ws'}://{hostport}{path}{WS_PATH}"

    auth_header = None
    if username is not None:
        token = base64.b64encode(f"{unquote(username)}:{unquote(password or '')}".encode()).decode("ascii")
        auth_header = f"Basic {token}"

    explicit: tuple[str, ...] | None = None
    duplicates = 0
    if symbols:
        explicit, duplicates = parse_symbols(symbols)

    return MeasurementConfig(
        http_base=http_base,
        ws_url=ws_url,
        auth_header=auth_header,
        redacted_backend=redact_url(f"{'https' if secure else 'http'}://{hostport}{path}"),
        raw_urls=(raw,),
        symbols=explicit,
        duration_s=duration,
        connect_timeout_s=connect_t,
        http_timeout_s=http_t,
        ack_timeout_s=ack_t,
        duplicates_dropped=duplicates,
    )


# ---------------------------------------------------------------------------
# Clock (monotonic for durations/offsets; wall clock only for labels)
# ---------------------------------------------------------------------------

class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)


def _r(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _parse_source_ts(value: object) -> tuple[datetime | None, bool]:
    """-> (aware UTC datetime or None if unparseable, was_naive)."""
    if not isinstance(value, str) or not value:
        return None, False
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None, False
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc), True
    return dt.astimezone(timezone.utc), False


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------

class _CategoryStats:
    __slots__ = (
        "count", "first_source", "last_source", "first_off", "last_off",
        "first_utc", "last_utc", "max_gap", "_last_mono", "_max_source",
    )

    def __init__(self) -> None:
        self.count = 0
        self.first_source: datetime | None = None
        self.last_source: datetime | None = None
        self.first_off: float | None = None
        self.last_off: float | None = None
        self.first_utc: datetime | None = None
        self.last_utc: datetime | None = None
        self.max_gap: float | None = None
        self._last_mono: float | None = None
        self._max_source: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "first_source_ts": _iso(self.first_source),
            "last_source_ts": _iso(self.last_source),
            "first_received_offset_s": _r(self.first_off),
            "last_received_offset_s": _r(self.last_off),
            "first_received_utc": _iso(self.first_utc),
            "last_received_utc": _iso(self.last_utc),
            "max_gap_s": _r(self.max_gap),
        }


class _BoundedCounter:
    """Counts labels; beyond MAX_DISTINCT_LABELS distinct labels the rest fold into 'other'."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def add(self, label: str) -> None:
        label = label[:32]
        if label not in self.counts and len(self.counts) >= MAX_DISTINCT_LABELS:
            label = "other"
        self.counts[label] = self.counts.get(label, 0) + 1

    def as_dict(self) -> dict[str, int]:
        return dict(sorted(self.counts.items()))


class CoverageCollector:
    """Synchronous, bounded event classifier. All time is passed in.

    ``mono`` is a monotonic reading used for offsets/gaps/deadline; ``utc`` is a
    wall-clock label recorded as receipt time. Source timestamps come from the
    event payload and are never mixed with either.
    """

    def __init__(self, monitored: tuple[str, ...], duration_s: float) -> None:
        self.monitored = tuple(monitored)
        self.duration_s = duration_s
        self._monitored_set = frozenset(monitored)
        self.stats: dict[str, dict[str, _CategoryStats]] = {
            s: {c: _CategoryStats() for c in CATEGORIES} for s in self.monitored
        }
        self.phase = "idle"  # idle -> setup -> measuring
        self.setup_error: str | None = None
        self._pending_acks: set[str] = set(CHANNELS)
        self.ack_latency_s: dict[str, float] = {}
        self._setup_mono: float | None = None
        self.window_start_mono: float | None = None
        self.window_start_utc: datetime | None = None
        self.deadline_mono: float | None = None
        self.last_accepted_mono: float | None = None

        self.messages_seen = 0
        self.malformed = 0
        self.malformed_by_kind = _BoundedCounter()
        self.pre_window_events: dict[str, int] = {c: 0 for c in CHANNELS}
        self.post_window_ignored = 0
        self.other_timeframe: dict[str, _BoundedCounter] = {CH_CANDLE: _BoundedCounter(), CH_FEATURES: _BoundedCounter()}
        self.unmonitored_events: dict[str, int] = {c: 0 for c in CHANNELS}
        self.unmonitored_sample: set[str] = set()
        self.unmonitored_sample_capped = False
        self.unexpected_channels = _BoundedCounter()
        self.meta_errors = 0
        self.source_ts_regressions: dict[str, int] = {c: 0 for c in CATEGORIES}
        self.tick_regressions_by_minute: dict[str, dict[str, int]] = {
            symbol: {"same_minute": 0, "earlier_minute": 0} for symbol in self.monitored
        }
        self.tick_regression_first_examples: dict[str, dict[str, dict[str, Any] | None]] = {
            symbol: {"same_minute": None, "earlier_minute": None} for symbol in self.monitored
        }
        self.duplicate_source_ts: dict[str, int] = {c: 0 for c in CATEGORIES if c != "ticks"}
        self.naive_source_timestamps = 0
        self.samples: list[dict[str, Any]] = []
        self.samples_dropped = 0

    # -- lifecycle -----------------------------------------------------------
    def begin_setup(self, mono: float) -> None:
        self.phase = "setup"
        self._setup_mono = mono

    @property
    def window_started(self) -> bool:
        return self.phase == "measuring"

    def window_elapsed(self, mono: float) -> float:
        if self.window_start_mono is None:
            return 0.0
        return max(0.0, min(mono - self.window_start_mono, self.duration_s))

    # -- bookkeeping ---------------------------------------------------------
    def _sample(self, kind: str, mono: float, *, channel: str | None = None, symbol: str | None = None,
                excerpt: str | None = None, source_ts: datetime | None = None,
                high_water_source_ts: datetime | None = None, minute_relation: str | None = None) -> None:
        if len(self.samples) >= MAX_ANOMALY_SAMPLES:
            self.samples_dropped += 1
            return
        offset = None if self.window_start_mono is None else _r(mono - self.window_start_mono)
        sample = {
            "kind": kind,
            "phase": self.phase,
            "channel": channel[:32] if isinstance(channel, str) else None,
            "symbol": symbol[:16] if isinstance(symbol, str) else None,
            "received_offset_s": offset,
            "excerpt": None if excerpt is None else excerpt[:MAX_EXCERPT_CHARS],
        }
        if source_ts is not None:
            sample["source_ts"] = _iso(source_ts)
            sample["high_water_source_ts"] = _iso(high_water_source_ts)
            sample["minute_relation"] = minute_relation
        self.samples.append(sample)

    def _malformed(self, kind: str, mono: float, raw: object, *, channel: str | None = None,
                   symbol: str | None = None) -> str:
        self.malformed += 1
        self.malformed_by_kind.add(kind)
        text = raw if isinstance(raw, str) else repr(raw)
        self._sample(kind, mono, channel=channel, symbol=symbol, excerpt=text)
        return "malformed"

    def retained_item_count(self) -> int:
        """Number of items held in every growing container (used to prove boundedness)."""
        n = len(self.stats) * len(CATEGORIES)
        n += len(self.tick_regressions_by_minute) + len(self.tick_regression_first_examples)
        n += sum(example is not None for classes in self.tick_regression_first_examples.values()
                 for example in classes.values())
        n += len(self.samples) + len(self.unmonitored_sample) + len(self.ack_latency_s)
        n += len(self.malformed_by_kind.counts) + len(self.unexpected_channels.counts)
        n += sum(len(c.counts) for c in self.other_timeframe.values())
        return n

    # -- message intake ------------------------------------------------------
    def process(self, raw: object, mono: float, utc: datetime) -> str:
        """Classify one raw WebSocket message; returns a short outcome label."""
        self.messages_seen += 1
        if self.phase == "measuring" and self.deadline_mono is not None and mono > self.deadline_mono:
            self.post_window_ignored += 1
            return "post_window"

        if isinstance(raw, (bytes, bytearray)):
            return self._malformed("binary_frame", mono, "<binary>")
        if not isinstance(raw, str):
            return self._malformed("not_text", mono, raw)
        try:
            message = json.loads(raw)
        except (ValueError, RecursionError):
            return self._malformed("invalid_json", mono, raw)
        if not isinstance(message, dict):
            return self._malformed("not_object", mono, raw)
        channel = message.get("channel")
        if not isinstance(channel, str) or not channel:
            return self._malformed("missing_channel", mono, raw)

        if channel == "_meta":
            return self._on_meta(message, mono, utc, raw)
        if channel not in CATEGORY_BY_CHANNEL:
            self.unexpected_channels.add(channel)
            self._sample("unexpected_channel", mono, channel=channel)
            return "unexpected_channel"
        return self._on_event(channel, message, mono, utc, raw)

    def _on_meta(self, message: dict, mono: float, utc: datetime, raw: str) -> str:
        if "error" in message:
            self.meta_errors += 1
            self._sample("meta_error", mono, channel="_meta", excerpt=str(message.get("error")))
            if self.phase == "setup" and self.setup_error is None:
                self.setup_error = "subscription_rejected"
            return "meta_error"
        subscribed = message.get("subscribed")
        if isinstance(subscribed, str) and self.phase == "setup" and subscribed in self._pending_acks:
            self._pending_acks.discard(subscribed)
            assert self._setup_mono is not None
            self.ack_latency_s[subscribed] = mono - self._setup_mono
            if not self._pending_acks:
                self.phase = "measuring"
                self.window_start_mono = mono
                self.window_start_utc = utc
                self.deadline_mono = mono + self.duration_s
            return "ack"
        return "meta_ignored"

    def _on_event(self, channel: str, message: dict, mono: float, utc: datetime, raw: str) -> str:
        if self.phase != "measuring":
            self.pre_window_events[channel] += 1
            return "pre_window"

        symbol = message.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            return self._malformed("missing_symbol", mono, raw, channel=channel)
        payload = message.get("payload")
        if not isinstance(payload, dict):
            return self._malformed("missing_payload", mono, raw, channel=channel, symbol=symbol)

        if channel == CH_TICK:
            source_value = payload.get("exchange_ts")
        else:
            timeframe = payload.get("timeframe")
            if not isinstance(timeframe, str) or not timeframe:
                return self._malformed("bad_timeframe", mono, raw, channel=channel, symbol=symbol)
            if timeframe != TIMEFRAME:
                self.other_timeframe[channel].add(timeframe)
                return "other_timeframe"
            source_value = payload.get("candle_ts")

        if symbol not in self._monitored_set:
            self.unmonitored_events[channel] += 1
            if symbol[:16] not in self.unmonitored_sample:
                if len(self.unmonitored_sample) < MAX_UNMONITORED_SAMPLE_SYMBOLS:
                    self.unmonitored_sample.add(symbol[:16])
                else:
                    self.unmonitored_sample_capped = True
            return "unmonitored"

        source, naive = _parse_source_ts(source_value)
        if source is None:
            return self._malformed("bad_source_ts", mono, raw, channel=channel, symbol=symbol)
        if naive:
            self.naive_source_timestamps += 1

        category = CATEGORY_BY_CHANNEL[channel]
        stats = self.stats[symbol][category]
        assert self.window_start_mono is not None
        offset = mono - self.window_start_mono
        if stats.count == 0:
            stats.first_source, stats.first_off, stats.first_utc = source, offset, utc
        else:
            gap = mono - (mono if stats._last_mono is None else stats._last_mono)
            if stats.max_gap is None or gap > stats.max_gap:
                stats.max_gap = gap
            if stats._max_source is not None:
                if source < stats._max_source:
                    self.source_ts_regressions[category] += 1
                    if category == "ticks":
                        relation = ("same_minute" if source.replace(second=0, microsecond=0)
                                    == stats._max_source.replace(second=0, microsecond=0)
                                    else "earlier_minute")
                        self.tick_regressions_by_minute[symbol][relation] += 1
                        if self.tick_regression_first_examples[symbol][relation] is None:
                            self.tick_regression_first_examples[symbol][relation] = {
                                "source_ts": _iso(source),
                                "high_water_source_ts": _iso(stats._max_source),
                                "received_offset_s": _r(offset),
                            }
                        self._sample("source_ts_regression", mono, channel=channel, symbol=symbol,
                                     source_ts=source, high_water_source_ts=stats._max_source,
                                     minute_relation=relation)
                    else:
                        self._sample("source_ts_regression", mono, channel=channel, symbol=symbol)
                elif source == stats._max_source and category in self.duplicate_source_ts:
                    self.duplicate_source_ts[category] += 1
                    self._sample("duplicate_source_ts", mono, channel=channel, symbol=symbol)
        if stats._max_source is None or source > stats._max_source:
            stats._max_source = source
        stats.last_source = source
        stats.count += 1
        stats.last_off, stats.last_utc, stats._last_mono = offset, utc, mono
        self.last_accepted_mono = mono
        return "event"


# ---------------------------------------------------------------------------
# HTTP reads (stdlib only; read-only GETs; no redirects; bounded body)
# ---------------------------------------------------------------------------

class HttpReadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # noqa: D401
        return None


def http_get_json(url: str, *, timeout: float, auth_header: str | None) -> Any:
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
    if auth_header:
        request.add_header("Authorization", auth_header)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)  # direct; ignores proxy env
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(MAX_HTTP_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise HttpReadError(f"http_status_{exc.code}") from None
    except TimeoutError:
        raise HttpReadError("timeout") from None
    except (urllib.error.URLError, OSError):
        raise HttpReadError("connection_failed") from None
    if len(body) > MAX_HTTP_BODY_BYTES:
        raise HttpReadError("response_too_large")
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        raise HttpReadError("invalid_json") from None


def _short(value: object, limit: int = 200) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def reduce_subscription_status(body: object, monitored: tuple[str, ...]) -> dict[str, Any]:
    """Reduce GET /market/subscription-status to identity/connection/inventory availability.

    Never reports capacity or delivery: the route itself says both are unknown, and
    this tool has no evidence for either.
    """
    if not isinstance(body, dict):
        return {"read_ok": False, "error": "unexpected_response_shape"}
    provider = body.get("provider")
    inventory = body.get("inventory")
    connected = body.get("connected")
    status = body.get("status")
    if status not in ("available", "unavailable") or not isinstance(inventory, dict):
        return {"read_ok": False, "error": "unexpected_response_shape"}
    if status == "available":
        if (not isinstance(provider, dict)
                or not isinstance(provider.get("id"), str) or not provider["id"]
                or not isinstance(provider.get("class_name"), str) or not provider["class_name"]
                or not (connected is None or isinstance(connected, bool))):
            return {"read_ok": False, "error": "unexpected_response_shape"}
    elif provider is not None or connected is not None:
        return {"read_ok": False, "error": "unexpected_response_shape"}
    availability = inventory.get("availability")
    symbols = inventory.get("symbols")
    count = inventory.get("count")
    if inventory.get("basis") != "locally_tracked_requests":
        return {"read_ok": False, "error": "unexpected_response_shape"}
    if availability == "available":
        if (status != "available" or connected is not True
                or not isinstance(symbols, list) or not all(isinstance(s, str) for s in symbols)
                or not isinstance(count, int) or isinstance(count, bool) or count != len(symbols)):
            return {"read_ok": False, "error": "unexpected_response_shape"}
    elif availability != "unavailable" or symbols is not None or count is not None:
        return {"read_ok": False, "error": "unexpected_response_shape"}
    out: dict[str, Any] = {
        "read_ok": True,
        "status": status,
        "reason": _short(body.get("reason")),
        "provider": None,
        "connected": connected if isinstance(connected, bool) else None,
        "inventory": None,
        "monitored_locally_listed": None,
    }
    if isinstance(provider, dict):
        out["provider"] = {"id": _short(provider.get("id")), "class_name": _short(provider.get("class_name"))}
    out["inventory"] = {
        "availability": availability,
        "reason": _short(inventory.get("reason")),
        "basis": _short(inventory.get("basis")),
        "count": count,
    }
    if availability == "available":
        out["monitored_locally_listed"] = len(set(monitored) & set(symbols))
    return out


def compare_identity(start: dict[str, Any] | None, end: dict[str, Any] | None) -> dict[str, Any]:
    note = "Two snapshots cannot show that nothing changed between them."
    if not start or not end or not start.get("read_ok") or not end.get("read_ok"):
        return {"comparable": False, "changed": None, "changed_fields": [], "note": note}

    def view(s: dict[str, Any]) -> dict[str, Any]:
        provider = s.get("provider") or {}
        return {
            "status": s.get("status"),
            "provider_id": provider.get("id"),
            "provider_class": provider.get("class_name"),
            "connected": s.get("connected"),
        }

    a, b = view(start), view(end)
    changed = sorted(k for k in a if a[k] != b[k])
    return {"comparable": True, "changed": bool(changed), "changed_fields": changed, "note": note}


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(
    *,
    config: MeasurementConfig,
    collector: CoverageCollector | None,
    monitored: tuple[str, ...] | None,
    status: str,
    end_reason: str,
    window_elapsed_s: float,
    started_utc: datetime | None,
    ended_utc: datetime | None,
    diagnostics_start: dict[str, Any] | None,
    diagnostics_end: dict[str, Any] | None,
    connection: dict[str, Any],
    setup_failure: dict[str, str] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "status": status,
        "end_reason": end_reason,
        "measurement": {
            "backend": config.redacted_backend,
            "requested_duration_s": _r(config.duration_s),
            "window_elapsed_s": _r(window_elapsed_s),
            "started_utc": _iso(started_utc),
            "ended_utc": _iso(ended_utc),
            "timeframe_filter": TIMEFRAME,
            "channels": list(CHANNELS),
        },
        "monitored": {
            "source": config.symbol_source,
            "duplicates_dropped": config.duplicates_dropped,
            "count": len(monitored) if monitored is not None else None,
            "symbols": list(monitored) if monitored is not None else None,
        },
        "diagnostics": {
            "start": diagnostics_start,
            "end": diagnostics_end,
            "identity": compare_identity(diagnostics_start, diagnostics_end),
        },
        "connection": connection,
        "setup_failure": setup_failure,
        "interpretation": list(INTERPRETATION),
    }
    if collector is None or not collector.window_started:
        return report

    symbols_rows = []
    totals = {c: 0 for c in CATEGORIES}
    with_events = {c: 0 for c in CATEGORIES}
    none_in = {c: [] for c in CATEGORIES}
    for symbol in sorted(collector.stats):
        row: dict[str, Any] = {"symbol": symbol}
        for category in CATEGORIES:
            st = collector.stats[symbol][category]
            row[category] = st.as_dict()
            totals[category] += st.count
            if st.count:
                with_events[category] += 1
            else:
                none_in[category].append(symbol)
        symbols_rows.append(row)
    no_event_any = sorted(s for s in collector.stats if all(collector.stats[s][c].count == 0 for c in CATEGORIES))

    report["channels"] = {
        "acknowledged": sorted(collector.ack_latency_s),
        "ack_latency_s": {k: _r(v) for k, v in sorted(collector.ack_latency_s.items())},
    }
    report["totals"] = {
        "events": totals,
        "symbols_with_events": with_events,
        "monitored_symbols": len(collector.stats),
    }
    report["no_events"] = {**{c: none_in[c] for c in CATEGORIES}, "all_categories": no_event_any}
    report["symbols"] = symbols_rows
    report["anomalies"] = {
        "messages_seen": collector.messages_seen,
        "malformed_messages": collector.malformed,
        "malformed_by_kind": collector.malformed_by_kind.as_dict(),
        "other_timeframe_filtered": {c: collector.other_timeframe[c].as_dict() for c in sorted(collector.other_timeframe)},
        "unmonitored_symbol_events": dict(sorted(collector.unmonitored_events.items())),
        "unmonitored_symbol_sample": sorted(collector.unmonitored_sample),
        "unmonitored_symbol_sample_capped": collector.unmonitored_sample_capped,
        "unexpected_channels": collector.unexpected_channels.as_dict(),
        "meta_errors": collector.meta_errors,
        "pre_window_events": dict(sorted(collector.pre_window_events.items())),
        "post_window_ignored": collector.post_window_ignored,
        "source_ts_regressions": dict(sorted(collector.source_ts_regressions.items())),
        "tick_regressions_by_minute": {
            "totals": {
                relation: sum(counts[relation] for counts in collector.tick_regressions_by_minute.values())
                for relation in ("same_minute", "earlier_minute")
            },
            "by_symbol": {symbol: dict(collector.tick_regressions_by_minute[symbol])
                          for symbol in sorted(collector.tick_regressions_by_minute)},
            "first_examples_by_symbol": {symbol: dict(collector.tick_regression_first_examples[symbol])
                                         for symbol in sorted(collector.tick_regression_first_examples)},
        },
        "duplicate_source_ts": dict(sorted(collector.duplicate_source_ts.items())),
        "naive_source_timestamps": collector.naive_source_timestamps,
        "samples": list(collector.samples),
        "samples_dropped": collector.samples_dropped,
        "sample_limit": MAX_ANOMALY_SAMPLES,
    }
    return report


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_report(path: str, report: dict[str, Any]) -> None:
    """Atomic write (temp file in the same directory, then replace)."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=".streaming-coverage-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(render_json(report))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def check_report_path(path: str) -> None:
    """Fail BEFORE a long window if the selected report path cannot be written."""
    absolute = os.path.abspath(path)
    if os.path.isdir(absolute):
        raise InputError("--json-report must be a file path, not a directory")
    directory = os.path.dirname(absolute)
    if not os.path.isdir(directory):
        raise InputError(f"--json-report directory does not exist: {directory}")
    if not os.access(directory, os.W_OK):
        raise InputError(f"--json-report directory is not writable: {directory}")


def _fmt_offset(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _name_list(names: list[str], limit: int = 20) -> str:
    if not names:
        return "none"
    shown = ", ".join(names[:limit])
    return shown if len(names) <= limit else f"{shown} (+{len(names) - limit} more)"


def render_console(report: dict[str, Any], *, max_rows: int = 50) -> str:
    m, mon = report["measurement"], report["monitored"]
    lines = [
        f"Streaming coverage: {report['status'].upper()} ({report['end_reason']})",
        f"Backend: {m['backend']}   window: {m['window_elapsed_s']}s of {m['requested_duration_s']}s requested "
        f"(monotonic)   monitored: {mon['count']} ({mon['source']})",
    ]
    conn = report["connection"]
    if conn.get("interrupted"):
        lines.append(f"Connection interrupted: {conn.get('reason')} (close code {conn.get('close_code')}) "
                     f"at +{_fmt_offset(conn.get('interrupted_at_offset_s'))}s")
    if report.get("setup_failure"):
        sf = report["setup_failure"]
        lines.append(f"Setup failure: {sf['code']}" + (f" - {sf['detail']}" if sf.get("detail") else ""))
    for label, key in (("start", "start"), ("end", "end")):
        d = report["diagnostics"][key]
        if d is None:
            lines.append(f"Subscription diagnostics ({label}): not read")
        elif not d.get("read_ok"):
            lines.append(f"Subscription diagnostics ({label}): unreadable ({d.get('error')})")
        else:
            prov = d.get("provider") or {}
            inv = d.get("inventory") or {}
            lines.append(
                f"Subscription diagnostics ({label}): status={d.get('status')} provider={prov.get('id')}/"
                f"{prov.get('class_name')} connected={d.get('connected')} local inventory={inv.get('availability')}"
                f" count={inv.get('count')} monitored locally listed={d.get('monitored_locally_listed')}"
            )
    ident = report["diagnostics"]["identity"]
    if ident["comparable"]:
        lines.append(f"Identity snapshots changed: {ident['changed']} {ident['changed_fields'] or ''}".rstrip())
    if "symbols" not in report:
        return "\n".join(lines) + "\n"

    t = report["totals"]
    lines.append(
        f"Totals: ticks={t['events']['ticks']} candles_1m={t['events']['candles_1m']} features_1m={t['events']['features_1m']}"
        f"   symbols with events: ticks={t['symbols_with_events']['ticks']} candles_1m="
        f"{t['symbols_with_events']['candles_1m']} features_1m={t['symbols_with_events']['features_1m']} of {t['monitored_symbols']}"
    )
    rows = report["symbols"]
    shown = rows[:max_rows] if max_rows > 0 else rows
    width = max([6] + [len(r["symbol"]) for r in shown])
    lines.append("")
    lines.append(f"{'SYMBOL':<{width}}  {'TICKS':>8}  {'C1M':>6}  {'F1M':>6}  {'LAST_TICK+s':>11}  {'LAST_C1M+s':>10}  {'LAST_F1M+s':>10}")
    for r in shown:
        lines.append(
            f"{r['symbol']:<{width}}  {r['ticks']['count']:>8}  {r['candles_1m']['count']:>6}  {r['features_1m']['count']:>6}"
            f"  {_fmt_offset(r['ticks']['last_received_offset_s']):>11}  {_fmt_offset(r['candles_1m']['last_received_offset_s']):>10}"
            f"  {_fmt_offset(r['features_1m']['last_received_offset_s']):>10}"
        )
    if len(shown) < len(rows):
        lines.append(f"... {len(rows) - len(shown)} more symbols (use --table-rows 0 or the JSON report)")
    ne = report["no_events"]
    lines.append("")
    lines.append("No observed events (cause not classified):")
    for key, label in (("ticks", "ticks"), ("candles_1m", "1m candles"), ("features_1m", "1m features"),
                       ("all_categories", "any category")):
        lines.append(f"  {label:<12} {len(ne[key]):>4}: {_name_list(ne[key])}")
    a = report["anomalies"]
    lines.append(
        f"Anomalies: malformed={a['malformed_messages']} {a['malformed_by_kind'] or ''} "
        f"unmonitored={sum(a['unmonitored_symbol_events'].values())} "
        f"other-timeframe={sum(sum(v.values()) for v in a['other_timeframe_filtered'].values())} "
        f"unexpected-channel={sum(a['unexpected_channels'].values())} meta-errors={a['meta_errors']} "
        f"pre-window={sum(a['pre_window_events'].values())} post-window={a['post_window_ignored']} "
        f"ts-regressions={sum(a['source_ts_regressions'].values())} duplicates={sum(a['duplicate_source_ts'].values())}"
    )
    tick_rels = a["tick_regressions_by_minute"]["totals"]
    lines.append(f"Tick regressions vs source high-water: same-minute={tick_rels['same_minute']} "
                 f"earlier-minute={tick_rels['earlier_minute']}")
    lines.append("Note: " + report["interpretation"][0] + " " + report["interpretation"][1])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Measurement runner
# ---------------------------------------------------------------------------

class Measurement:
    """One measurement run. Build with a config, ``await run()``, then read ``report()``."""

    def __init__(self, config: MeasurementConfig, *, clock: Any | None = None) -> None:
        self.config = config
        self.clock = clock or SystemClock()
        self.redact = Redactor(config.raw_urls)
        self.status = "not_started"
        self.end_reason = ""
        self.monitored: tuple[str, ...] | None = config.symbols
        self.collector: CoverageCollector | None = None
        self.diagnostics_start: dict[str, Any] | None = None
        self.diagnostics_end: dict[str, Any] | None = None
        self.setup_failure: dict[str, str] | None = None
        self.connection: dict[str, Any] = {"interrupted": False, "reason": None, "close_code": None,
                                           "close_reason": None, "interrupted_at_offset_s": None}
        self._closed_mono: float | None = None
        self._started_utc: datetime | None = None
        self._ended_utc: datetime | None = None
        self._end_mono: float | None = None
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._ready = asyncio.Event()

    # -- results -------------------------------------------------------------
    @property
    def exit_code(self) -> int:
        return {"completed": EXIT_OK, "failed_setup": EXIT_SETUP_FAILED, "interrupted": EXIT_INTERRUPTED}.get(
            self.status, EXIT_INTERRUPTED)

    def report(self) -> dict[str, Any]:
        elapsed = 0.0
        if self.collector is not None and self.collector.window_started:
            end = self._end_mono if self._end_mono is not None else self.clock.monotonic()
            elapsed = self.collector.window_elapsed(end)
        return build_report(
            config=self.config, collector=self.collector, monitored=self.monitored, status=self.status,
            end_reason=self.end_reason, window_elapsed_s=elapsed, started_utc=self._started_utc,
            ended_utc=self._ended_utc, diagnostics_start=self.diagnostics_start, diagnostics_end=self.diagnostics_end,
            connection=self.connection, setup_failure=self.setup_failure,
        )

    # -- http ------------------------------------------------------------------
    async def _get(self, path: str) -> Any:
        url = self.config.http_base + path
        return await asyncio.to_thread(
            http_get_json, url, timeout=self.config.http_timeout_s, auth_header=self.config.auth_header)

    async def _capture_monitored(self) -> tuple[str, ...]:
        if self.config.symbols is not None:
            return self.config.symbols
        try:
            body = await self._get(UNIVERSE_PATH)
        except HttpReadError as exc:
            raise SetupError("universe_read_failed", exc.code) from None
        raw = body.get("symbols") if isinstance(body, dict) else None
        if not isinstance(raw, list) or not all(isinstance(s, str) for s in raw):
            raise SetupError("universe_read_failed", "unexpected_response_shape")
        unique: dict[str, None] = {}
        for item in raw:
            symbol = item.strip().upper()
            if not is_valid_ticker_format(symbol):
                raise SetupError("universe_read_failed", "universe contained an invalid ticker")
            unique.setdefault(symbol)
        if not unique:
            raise SetupError("empty_monitored_set", "the saved scanner universe is empty")
        return tuple(sorted(unique))

    async def _read_diagnostics(self, monitored: tuple[str, ...]) -> dict[str, Any]:
        try:
            body = await self._get(STATUS_PATH)
        except HttpReadError as exc:
            return {"read_ok": False, "error": exc.code}
        except Exception as exc:  # noqa: BLE001 - recorded, never raised
            return {"read_ok": False, "error": f"unexpected_{type(exc).__name__}"}
        return reduce_subscription_status(body, monitored)

    # -- websocket -------------------------------------------------------------
    async def _read_loop(self, ws: Any) -> None:
        from websockets.exceptions import ConnectionClosed

        collector = self.collector
        assert collector is not None
        try:
            async for raw in ws:
                collector.process(raw, self.clock.monotonic(), self.clock.utc_now())
                if collector.window_started or collector.setup_error:
                    self._ready.set()
        except ConnectionClosed:
            pass
        except Exception as exc:  # noqa: BLE001 - reader faults are reported, not raised
            self.connection["reason"] = f"reader_error:{type(exc).__name__}"
        finally:
            self.connection["close_code"] = getattr(ws, "close_code", None)
            reason = getattr(ws, "close_reason", None)
            if isinstance(reason, str) and reason:
                self.connection["close_reason"] = self.redact(reason)[:120]
            self._closed_mono = self.clock.monotonic()
            self._ready.set()

    async def _open_and_subscribe(self) -> None:
        from websockets.asyncio.client import connect

        assert self.collector is not None
        headers = {"Authorization": self.config.auth_header} if self.config.auth_header else None
        try:
            self._ws = await connect(
                self.config.ws_url, additional_headers=headers, open_timeout=self.config.connect_timeout_s,
                max_size=1 << 20)
        except Exception as exc:  # noqa: BLE001
            raise SetupError("websocket_connect_failed", self.redact(f"{type(exc).__name__}: {exc}")) from None
        self.collector.begin_setup(self.clock.monotonic())
        self._reader = asyncio.ensure_future(self._read_loop(self._ws))
        try:
            for channel in CHANNELS:
                await self._ws.send(json.dumps({"action": "subscribe", "channel": channel}))
        except Exception as exc:  # noqa: BLE001
            raise SetupError("subscribe_send_failed", self.redact(f"{type(exc).__name__}: {exc}")) from None
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=self.config.ack_timeout_s)
        except asyncio.TimeoutError:
            missing = sorted(self.collector._pending_acks)
            raise SetupError("acknowledgement_timeout", "missing: " + ", ".join(missing)) from None
        if self.collector.setup_error:
            raise SetupError(self.collector.setup_error, "backend rejected a subscribe request")
        if not self.collector.window_started:
            raise SetupError("connection_closed_during_setup", f"close code {self.connection.get('close_code')}")

    async def _close(self) -> None:
        ws, reader = self._ws, self._reader
        self._ws = self._reader = None
        if ws is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(ws.close(), timeout=5.0)
        if reader is not None:
            if not reader.done():
                reader.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader

    # -- main ------------------------------------------------------------------
    async def run(self, stop_event: asyncio.Event | None = None) -> int:
        self._started_utc = self.clock.utc_now()
        self.status = "running"
        try:
            await self._setup_and_measure(stop_event)
        except SetupError as exc:
            self.status = "failed_setup"
            self.end_reason = exc.code
            self.setup_failure = {"code": exc.code, "detail": self.redact(exc.detail)[:300]}
        except asyncio.CancelledError:
            self.status, self.end_reason = "interrupted", "cancelled"
            self._end_mono = self.clock.monotonic()
            self._ended_utc = self.clock.utc_now()
            await asyncio.shield(self._close())
            raise
        except Exception as exc:  # noqa: BLE001 - reported as a failed setup, never a traceback with URLs
            self.status = "failed_setup"
            self.end_reason = "unexpected_error"
            self.setup_failure = {"code": "unexpected_error", "detail": self.redact(type(exc).__name__)}
        finally:
            if self._ws is not None or self._reader is not None:
                await asyncio.shield(self._close())
        self._ended_utc = self.clock.utc_now()
        return self.exit_code

    async def _setup_and_measure(self, stop_event: asyncio.Event | None) -> None:
        self.monitored = await self._capture_monitored()
        self.collector = CoverageCollector(self.monitored, self.config.duration_s)
        self.diagnostics_start = await self._read_diagnostics(self.monitored)
        if not self.diagnostics_start.get("read_ok"):
            raise SetupError("start_diagnostics_unavailable", str(self.diagnostics_start.get("error")))
        await self._open_and_subscribe()

        collector, reader = self.collector, self._reader
        assert collector is not None and reader is not None and collector.deadline_mono is not None
        waiters: set[asyncio.Future] = {reader}
        stop_waiter = asyncio.ensure_future(stop_event.wait()) if stop_event is not None else None
        if stop_waiter is not None:
            waiters.add(stop_waiter)
        try:
            remaining = max(0.0, collector.deadline_mono - self.clock.monotonic())
            done, _ = await asyncio.wait(waiters, timeout=remaining, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if stop_waiter is not None and not stop_waiter.done():
                stop_waiter.cancel()
        self._end_mono = self.clock.monotonic()
        if stop_waiter is not None and stop_waiter in done:
            self.status, self.end_reason = "interrupted", "stop_requested"
        elif reader in done:
            closed = self._closed_mono if self._closed_mono is not None else self._end_mono
            self._end_mono = closed
            self.status, self.end_reason = "interrupted", "connection_lost"
            self.connection.update(
                interrupted=True,
                reason=self.connection.get("reason") or "connection_closed",
                interrupted_at_offset_s=_r(collector.window_elapsed(closed)),
            )
        else:
            self.status, self.end_reason = "completed", "window_elapsed"
            self._end_mono = collector.deadline_mono

        await self._close()
        # Read the end snapshot after the connection is closed so no further events are attributed.
        self.diagnostics_end = await self._read_diagnostics(self.monitored)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="measure_streaming_coverage.py",
        description=(
            "Observe a RUNNING backend's /ws boundary for an explicit window and report which monitored symbols "
            "produced ticks, closed 1m candles and 1m feature updates. Read-only: never connects a provider, "
            "requests feeds, edits the universe or trades."),
    )
    p.add_argument("--backend-url", required=True, help="Backend root, e.g. http://127.0.0.1:8000 (http(s) or ws(s))")
    p.add_argument("--symbols", help="Explicit comma-separated symbols, e.g. AAPL,MSFT,NVDA")
    p.add_argument("--scanner-universe", action="store_true",
                   help="Capture the saved scanner universe once (GET /scanner/universe) instead of --symbols")
    p.add_argument("--duration", type=float, required=True, help="Measurement window in seconds (> 0)")
    p.add_argument("--json-report", metavar="PATH", help="Also write the full JSON report to this explicit path")
    p.add_argument("--table-rows", type=int, default=50, help="Console rows to print (0 = all); default 50")
    p.add_argument("--connect-timeout", type=float, default=10.0)
    p.add_argument("--http-timeout", type=float, default=10.0)
    p.add_argument("--ack-timeout", type=float, default=10.0)
    return p


async def amain(argv: list[str], *, out: TextIO | None = None, err: TextIO | None = None,
                stop_event: asyncio.Event | None = None, clock: Any | None = None) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    parser = build_parser()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):  # argparse prints to the real streams
            args = parser.parse_args(argv)
    except SystemExit as exc:  # usage/help already printed
        return EXIT_INVALID_INPUT if exc.code not in (0, None) else EXIT_OK
    redactor = Redactor((args.backend_url or "",))
    try:
        if args.table_rows < 0:
            raise InputError("--table-rows must be >= 0")
        config = build_config(
            args.backend_url, symbols=args.symbols, scanner_universe=args.scanner_universe,
            duration_s=args.duration, connect_timeout_s=args.connect_timeout,
            http_timeout_s=args.http_timeout, ack_timeout_s=args.ack_timeout)
        if args.json_report:
            check_report_path(args.json_report)
    except InputError as exc:
        print(f"error: {redactor(exc)}", file=err)
        return EXIT_INVALID_INPUT

    measurement = Measurement(config, clock=clock)
    try:
        await measurement.run(stop_event)
    except asyncio.CancelledError:
        pass  # cleanup already ran; fall through to report the partial, interrupted measurement
    report = measurement.report()
    out.write(render_console(report, max_rows=args.table_rows))
    code = measurement.exit_code
    if args.json_report:
        try:
            write_report(args.json_report, report)
            out.write(f"JSON report written: {args.json_report}\n")
        except OSError as exc:
            print(f"error: could not write JSON report ({type(exc).__name__})", file=err)
            return EXIT_REPORT_WRITE_FAILED
    return code


def main(argv: list[str] | None = None) -> int:
    async def runner() -> int:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed: list[int] = []
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.add_signal_handler(sig, stop.set)
                installed.append(sig)
        try:
            return await amain(list(sys.argv[1:] if argv is None else argv), stop_event=stop)
        finally:
            for sig in installed:
                with contextlib.suppress(Exception):
                    loop.remove_signal_handler(sig)

    try:
        return asyncio.run(runner())
    except KeyboardInterrupt:  # platforms without loop signal handlers
        return EXIT_INTERRUPTED
