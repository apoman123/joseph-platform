"""MEXC futures private Protobuf frames: transcription and decoding.

The transcribed schema is checked against the vendored official ``.proto``
files. MEXC's private pushes in Protobuf could not be captured without an
account, so payloads are encoded here with the same official schema.
"""

from __future__ import annotations

import decimal
import hashlib
import re

import pytest

from joseph_execution.adapters.venues import mexc_protobuf

_BLOCK = re.compile(r"\b(message|enum)\s+(\w+)\s*\{")
_FIELD = re.compile(
    r"(?:\b(optional|repeated)\s+)?([\w.]+)\s+(\w+)\s*=\s*(\d+)\s*;"
)


def _official_blocks() -> dict[str, str]:
    """Extracts message bodies from the vendored ``.proto`` files.

    Returns:
        Body text per message name.
    """
    blocks: dict[str, str] = {}
    for path in sorted(mexc_protobuf.PROTO_DIR.glob("*.proto")):
        text = re.sub(r"//[^\n]*", "", path.read_text(encoding="utf-8"))
        for match in _BLOCK.finditer(text):
            depth, end = 1, match.end()
            while depth:
                depth += {"{": 1, "}": -1}.get(text[end], 0)
                end += 1
            if match.group(1) == "message":
                blocks[match.group(2)] = text[match.end() : end - 1]
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


def _wrap(member: str, channel: str, **fields: object) -> bytes:
    """Encodes a private push like the Protobuf endpoint.

    Args:
        member: ``ContractWrapper`` oneof member (``orderChange`` …).
        channel: Wrapper channel.
        **fields: Payload fields.

    Returns:
        Serialized ``ContractWrapper``.
    """
    schema = mexc_protobuf.SCHEMA
    wrapper = schema.message_class("ContractWrapper")(
        channel=channel, symbol="BTC_USDT", ts=7
    )
    payload = schema.message_class(member[0].upper() + member[1:])(**fields)
    getattr(wrapper, member).CopyFrom(payload)
    return wrapper.SerializeToString()


def test_transcribed_messages_match_the_vendored_proto_files() -> None:
    """Every transcribed field has the official name, number, type, label."""
    blocks = _official_blocks()
    for name, fields in mexc_protobuf.MESSAGES.items():
        mine = {(f.name, f.number, f.kind, f.label) for f in fields}
        official = _official_fields(blocks[name])
        if name == "ContractWrapper":
            assert mine <= official
        else:
            assert mine == official


def test_every_decoded_member_has_a_json_channel() -> None:
    """Each transcribed wrapper payload maps to its JSON channel."""
    members = {
        f.name
        for f in mexc_protobuf.MESSAGES["ContractWrapper"]
        if f.number > 1000
    }
    assert members == set(mexc_protobuf.CHANNELS)


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


def test_order_push_decodes_to_the_json_layout() -> None:
    """``OrderChange`` keeps JSON names; unset optionals are absent."""
    frame = _wrap(
        "orderChange",
        "push.personal.order",
        orderId="7",
        symbol="BTC_USDT",
        dealVol="5",
        state=2,
        externalOid="j1",
    )
    decoded = mexc_protobuf.loads(frame)
    assert decoded["channel"] == "push.personal.order"
    assert decoded["symbol"] == "BTC_USDT" and decoded["ts"] == 7
    data = decoded["data"]
    assert data["orderId"] == "7" and data["dealVol"] == "5"
    assert data["state"] == 2 and data["externalOid"] == "j1"
    assert data["errorCode"] == 0
    assert "price" not in data and "updateTime" not in data


def test_maker_fills_keep_an_explicit_false_taker_flag() -> None:
    """Proto3 omits ``taker = false`` on the wire; decoding restores it."""
    frame = _wrap(
        "orderDealDetail",
        "push.personal.order.deal",
        id="9",
        symbol="BTC_USDT",
        vol="1",
        price="61000.5",
        orderId="7",
        timestamp=1,
    )
    data = mexc_protobuf.loads(frame)["data"]
    assert data["taker"] is False
    assert "isSelf" not in data


def test_payload_type_names_the_channel() -> None:
    """The payload member decides the JSON channel, not the wrapper text."""
    frame = _wrap(
        "positionChange", "push.personal.position.v2", symbol="BTC_USDT"
    )
    decoded = mexc_protobuf.loads(frame)
    assert decoded["channel"] == "push.personal.position"
    assert decoded["data"]["deductFeeList"] == []
    asset = _wrap("assetChange", "", currency="USDT", availableBalance="1")
    assert mexc_protobuf.loads(asset) == {
        "channel": "push.personal.asset",
        "symbol": "BTC_USDT",
        "ts": 7,
        "data": {"currency": "USDT", "availableBalance": "1"},
    }


def test_unknown_payloads_keep_the_wrapper_channel() -> None:
    """Members not transcribed decode to the channel without ``data``."""
    wrapper = mexc_protobuf.SCHEMA.message_class("ContractWrapper")(
        channel="push.personal.leverage.mode"
    )
    leverage_mode = b"\xe2\x3e\x00"  # field 1004, empty message
    decoded = mexc_protobuf.loads(wrapper.SerializeToString() + leverage_mode)
    assert decoded == {"channel": "push.personal.leverage.mode"}


def test_json_frames_keep_exact_decimals() -> None:
    """Text and JSON-like binary frames parse with ``Decimal`` floats."""
    text = '{"channel":"push.personal.asset","data":{"positionMargin":0.1}}'
    for frame in (text, text.encode()):
        data = mexc_protobuf.loads(frame)["data"]
        assert data["positionMargin"] == decimal.Decimal("0.1")


@pytest.mark.parametrize("frame", [b"\xff\xff\xff", b"\x12\x05ab"])
def test_corrupt_binary_frames_raise_value_error(frame: bytes) -> None:
    """Truncated or corrupt Protobuf is reported as ``ValueError``."""
    with pytest.raises(ValueError, match="undecodable"):
        mexc_protobuf.loads(frame)
