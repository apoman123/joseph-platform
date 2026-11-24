# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""MEXC futures Protobuf frames: transcription, decoding and detection.

The transcribed schema is checked against the vendored official ``.proto``
files, and decoding against binary frames captured live from
``wss://contract.mexc.com/edge?Trans-Protocol=Protobuf`` on 2026-11-23.
"""

from __future__ import annotations

import base64
import hashlib
import json
import pathlib
import re
from collections.abc import Mapping
from typing import Any

import pytest

from joseph.market_data.adapters import mexc_protobuf

_FIXTURES = pathlib.Path(__file__).resolve().parents[3] / "fixtures/venues/mexc"
_BLOCK = re.compile(r"\b(message|enum)\s+(\w+)\s*\{")
_FIELD = re.compile(
    r"(?:\b(optional|repeated)\s+)?([\w.]+)\s+(\w+)\s*=\s*(\d+)\s*;"
)
_VALUE = re.compile(r"(\w+)\s*=\s*(-?\d+)\s*;")


def _capture(name: str) -> dict[str, Any]:
    """Reads a captured fixture.

    Args:
        name: File name without extension.

    Returns:
        The fixture's parsed JSON object.
    """
    data: dict[str, Any] = json.loads(
        (_FIXTURES / f"{name}.json").read_text(encoding="utf-8")
    )
    return data


def _raw(frame: Mapping[str, str]) -> str | bytes:
    """Restores a captured frame.

    Args:
        frame: ``{"text": ...}`` or ``{"base64": ...}``.

    Returns:
        Text or binary payload as the socket delivered it.
    """
    if "base64" in frame:
        return base64.b64decode(frame["base64"])
    return frame["text"]


def _official_blocks() -> dict[tuple[str, str], str]:
    """Extracts message and enum bodies from the vendored ``.proto`` files.

    Returns:
        Body text per ``(kind, name)``.
    """
    blocks: dict[tuple[str, str], str] = {}
    for path in sorted(mexc_protobuf.PROTO_DIR.glob("*.proto")):
        text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
        for match in _BLOCK.finditer(text):
            depth, end = 1, match.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
            blocks[(match.group(1), match.group(2))] = text[
                match.end() : end - 1
            ]
    return blocks


def _official_fields(body: str) -> set[tuple[str, int, str, str]]:
    """Lists the fields declared in a message body.

    Args:
        body: Message body text.

    Returns:
        ``(name, number, type, label)`` per field.
    """
    return {
        (name, int(number), kind, label)
        for label, kind, name, number in _FIELD.findall(body)
    }


def _transcribed(name: str) -> set[tuple[str, int, str, str]]:
    """Lists the transcribed fields of a message.

    Args:
        name: Message name.

    Returns:
        ``(name, number, kind, label)`` per field.
    """
    return {
        (f.name, f.number, f.kind, f.label)
        for f in mexc_protobuf.MESSAGES[name]
    }


def test_transcribed_messages_match_the_vendored_proto_files() -> None:
    """Transcribed fields keep the official name, number, type and label."""
    blocks = _official_blocks()
    for name in mexc_protobuf.MESSAGES:
        official = _official_fields(blocks[("message", name)])
        if name == "ContractWrapper":
            assert _transcribed(name) <= official
        else:
            assert _transcribed(name) == official


def test_transcribed_enums_match_the_vendored_proto_files() -> None:
    """Enum values equal the official ``QuotationEnums.proto`` ones."""
    blocks = _official_blocks()
    for name, values in mexc_protobuf.ENUMS.items():
        official = {
            value: int(number)
            for value, number in _VALUE.findall(blocks[("enum", name)])
        }
        assert dict(values) == official


def test_vendored_files_match_the_recorded_hashes() -> None:
    """Each vendored file is the upstream copy recorded in ``NOTICE``."""
    notice = (mexc_protobuf.PROTO_DIR / "NOTICE").read_text(encoding="utf-8")
    recorded = dict(
        re.findall(r"^\s+(\S+\.proto|LICENSE)\s+([0-9a-f]{64})$", notice, re.M)
    )
    present = {
        path.name
        for path in mexc_protobuf.PROTO_DIR.iterdir()
        if path.name != "NOTICE"
    }
    assert set(recorded) == present
    for name, digest in recorded.items():
        data = (mexc_protobuf.PROTO_DIR / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest


def test_captured_deal_pushes_decode_to_the_json_layout() -> None:
    """PB ``t``/``ts``/``o``/``m`` become JSON ``T``/``t``/``O``/``M``."""
    frames = [_raw(f) for f in _capture("ws_deal_protobuf")["frames"]]
    assert isinstance(frames[0], str)
    assert mexc_protobuf.loads(frames[0])["channel"] == "rs.sub.deal"
    pushes = [mexc_protobuf.loads(f) for f in frames[1:]]
    assert all(isinstance(f, bytes) for f in frames[1:])
    first = pushes[0]
    assert first == {
        "channel": "push.deal",
        "symbol": "BTC_USDT",
        "ts": 1795427295241,
        "data": [
            {
                "p": "61450.2",
                "v": "12",
                "T": 1,
                "O": 3,
                "M": 1,
                "t": 1795427295236,
                "i": 17133941459,
                "cts": 1795427295236,
            }
        ],
    }
    ids = [push["data"][0]["i"] for push in pushes]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    assert {push["data"][0]["T"] for push in pushes} == {1, 2}


def test_reduced_deal_pushes_have_no_trade_id() -> None:
    """Without ``reduce: false`` MEXC drops the trade id from PB deals."""
    frames = _capture("ws_deal_protobuf")["reduced_frames"][1:]
    for frame in frames:
        (deal,) = mexc_protobuf.loads(_raw(frame))["data"]
        assert "i" not in deal
        assert {"p", "v", "T", "t"} <= set(deal)


def test_captured_kline_pushes_decode_with_the_interval_name() -> None:
    """``WsKLine`` keeps decimal strings and names its ``Interval``."""
    frames = _capture("ws_kline_1m_protobuf")["frames"]
    push = mexc_protobuf.loads(_raw(frames[1]))
    assert push["channel"] == "push.kline"
    assert push["symbol"] == "BTC_USDT"
    assert push["data"] == {
        "symbol": "BTC_USDT",
        "interval": "Min1",
        "t": 1795427280,
        "o": "61459.8",
        "c": "61443.9",
        "h": "61459.8",
        "l": "61443.9",
        "a": "173418.63077",
        "q": "28219",
        "ro": "61453.4",
        "rc": "61443.9",
        "rh": "61457.3",
        "rl": "61443.9",
    }


def test_deals_batches_decode_every_deal() -> None:
    """A ``deals`` (2006) batch yields one JSON deal per entry."""
    wrapper = mexc_protobuf.SCHEMA.message_class("ContractWrapper")()
    wrapper.channel = "push.deal"  # type: ignore[attr-defined]  # message class is built at runtime from the schema
    for number in (1, 2):
        deal = wrapper.deals.deals.add()  # type: ignore[attr-defined]  # message class is built at runtime from the schema
        deal.p, deal.v, deal.t, deal.ts, deal.i = "1.5", "3", number, 9, number
    decoded = mexc_protobuf.decode(wrapper.SerializeToString())
    assert decoded == {
        "channel": "push.deal",
        "data": [
            {"p": "1.5", "v": "3", "T": 1, "t": 9, "i": 1, "cts": 0},
            {"p": "1.5", "v": "3", "T": 2, "t": 9, "i": 2, "cts": 0},
        ],
    }


def test_presence_follows_proto3_rules() -> None:
    """Unset optionals are absent; implicit scalars carry defaults."""
    kline = mexc_protobuf.SCHEMA.message_class("WsKLine")()
    kline.interval = 99  # type: ignore[attr-defined]  # message class is built at runtime from the schema
    parsed = mexc_protobuf.SCHEMA.parse("WsKLine", kline.SerializeToString())
    assert parsed == {
        "symbol": "",
        "interval": 99,
        "t": 0,
        "o": "",
        "c": "",
        "h": "",
        "l": "",
        "a": "",
        "q": "",
    }
    assert mexc_protobuf.SCHEMA.parse("Deal", b"") == {"cts": 0}


def test_unknown_payloads_are_skipped() -> None:
    """Members the adapter does not read decode without ``data``."""
    wrapper = mexc_protobuf.SCHEMA.message_class("ContractWrapper")(
        channel="push.ticker", symbol="BTC_USDT", ts=5
    )
    ticker_detail = b"\x8a\x7e\x03\x0a\x01x"  # field 2017, length 3
    frame = wrapper.SerializeToString() + ticker_detail
    assert mexc_protobuf.decode(frame) == {
        "channel": "push.ticker",
        "symbol": "BTC_USDT",
        "ts": 5,
    }


def test_text_and_json_bytes_parse_as_json() -> None:
    """Text frames and binary frames starting like JSON are JSON."""
    assert mexc_protobuf.loads('{"channel":"pong","data":1}') == {
        "channel": "pong",
        "data": 1,
    }
    assert mexc_protobuf.loads(b'{"channel":"pong"}') == {"channel": "pong"}
    assert mexc_protobuf.loads(b"[1]") == [1]


@pytest.mark.parametrize("frame", [b"\xff\xff\xff", b"\x0a\x05ab"])
def test_corrupt_binary_frames_raise_value_error(frame: bytes) -> None:
    """Truncated or corrupt Protobuf is reported as ``ValueError``."""
    with pytest.raises(ValueError, match="undecodable"):
        mexc_protobuf.loads(frame)
