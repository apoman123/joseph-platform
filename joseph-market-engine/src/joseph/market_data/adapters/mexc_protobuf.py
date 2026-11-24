# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""MEXC futures WebSocket frames in Protocol Buffers and JSON.

MEXC is moving its futures WebSocket market data from JSON to Protocol
Buffers ("MEXC to Migrate Futures WebSocket Market Data from JSON to
Protobuf", 2026-11-12: "Migration Deadline: Dec 1, 2026, 10:00 (UTC)").
The announcement does not list channels and does not say whether JSON
stays available after the deadline:
https://www.mexc.com/announcements/article/mexc-to-migrate-futures-websocket-market-data-from-json-to-protobuf-17827791539697

Sources (read 2026-11-23):

* Update log, https://www.mexc.com/api-docs/futures/update-log:
  2026-10-23 added the Protobuf integration docs and the endpoint
  ``wss://contract.mexc.com/edge?Trans-Protocol=Protobuf`` ("Futures
  WebSocket supports both JSON and Protobuf"); 2026-11-11 made ``push.deal``
  in PB format "reduce" by default (one record per period), with
  ``"reduce": false`` to receive every trade.
* https://www.mexc.com/api-docs/futures/websocket-api/native-ws-endpoint
  lists both addresses: the format is chosen per connection by the
  ``Trans-Protocol`` query parameter. No documented subscription parameter
  selects it. ``compress`` (``sub.deal`` aggregation, on by default since
  2025-08-21) and the ``gzip`` flag in the ``sub.kline`` example are
  unrelated to the format.
* https://www.mexc.com/api-docs/futures/websocket-api/protocol-buffers-integration:
  client requests (subscribe, unsubscribe, ping) stay UTF-8 JSON; server
  market pushes are raw Protobuf binary; ``pong`` is JSON.
* https://www.mexc.com/api-docs/futures/websocket-api/deal (``reduce``).
* Official definitions: https://github.com/mexcdevelop/websocket-future-proto
  (Apache-2.0, commit ``0338258``), vendored unmodified in ``mexc_proto/``
  (see ``mexc_proto/NOTICE``).

Verified live on 2026-11-23 against both endpoints with ``sub.deal`` and
``sub.kline`` on ``BTC_USDT``:

* On the Protobuf endpoint, acks (``rs.sub.*``), errors (``rs.error``) and
  ``pong`` are still JSON text frames. Pushes are binary
  ``ContractWrapper`` messages: ``symbol`` (1), ``channel`` (2), ``ts`` (3)
  and the payload in the ``data`` oneof.
* ``push.deal`` carries one ``Deal`` (field 2005); ``Deals`` (2006) is a
  batch. Protobuf renamed the JSON keys: ``t`` is the taker side (JSON
  ``T``), ``ts`` the trade time (JSON ``t``), and ``o``/``m`` are JSON
  ``O``/``M``. Prices and volumes are decimal strings.
* With the default ``reduce``, PB deals arrive without a trade id ``i``
  (the update log says one record per period); ``"reduce": false``
  restores one deal per trade with its id. The JSON endpoint accepts the
  parameter and still pushes every trade with its id.
* ``push.kline`` carries ``WsKLine`` (2101) with the interval as the
  ``Interval`` enum, decoded here to its JSON name (``Min1`` …).

Frames decode with the ``protobuf`` runtime. The descriptors are built
here from a transcription of the vendored ``.proto`` files, so no generated
code is needed, and ``tests/unit/market_data/test_mexc_protobuf.py`` checks
the transcription against those files. Only the messages the adapter reads
are described. Other ``data`` members are skipped as unknown fields.
Decoded pushes are reshaped to the JSON frame layout, so one parser serves
both formats during the transition.
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool
from google.protobuf import message
from google.protobuf import message_factory

PROTO_DIR = pathlib.Path(__file__).with_name("mexc_proto")
"""Vendored official ``.proto`` files (with ``LICENSE`` and ``NOTICE``)."""

_PACKAGE = "joseph_mexc_futures_ws"
_JSON_START = (b"{", b"[")
_FDP = descriptor_pb2.FieldDescriptorProto
_SCALARS = {
    "string": _FDP.TYPE_STRING,
    "int32": _FDP.TYPE_INT32,
    "int64": _FDP.TYPE_INT64,
    "bool": _FDP.TYPE_BOOL,
}


@dataclasses.dataclass(frozen=True, slots=True)
class Field:
    """One field of a transcribed ``.proto`` message.

    Attributes:
        name: Field name as written in the ``.proto`` file.
        number: Field number.
        kind: Scalar type (``string``, ``int32``, ``int64``, ``bool``) or
            the name of a message or enum in the same schema.
        label: ``optional``, ``repeated`` or empty (proto3 implicit
            presence), as written in the ``.proto`` file.
    """

    name: str
    number: int
    kind: str
    label: str = ""


ENUMS: Mapping[str, Mapping[str, int]] = {
    "Interval": {
        "UNKNOWN": 0,
        "Second1": 1,
        "Min1": 2,
        "Min5": 3,
        "Min15": 4,
        "Min30": 5,
        "Min60": 6,
        "Hour4": 7,
        "Hour8": 8,
        "Day1": 9,
        "Week1": 10,
        "Month1": 11,
    },
}
"""Enums from ``QuotationEnums.proto`` used by the messages below."""

MESSAGES: Mapping[str, Sequence[Field]] = {
    "ContractWrapper": (
        Field("symbol", 1, "string"),
        Field("channel", 2, "string"),
        Field("ts", 3, "int64"),
        Field("deal", 2005, "Deal"),
        Field("deals", 2006, "Deals"),
        Field("wsKline", 2101, "WsKLine"),
    ),
    "Deal": (
        Field("p", 1, "string", "optional"),
        Field("v", 2, "string", "optional"),
        Field("t", 3, "int32", "optional"),
        Field("o", 4, "int32", "optional"),
        Field("m", 5, "int32", "optional"),
        Field("ts", 6, "int64", "optional"),
        Field("i", 7, "int64", "optional"),
        Field("cts", 8, "int64"),
    ),
    "Deals": (Field("deals", 1, "Deal", "repeated"),),
    "WsKLine": (
        Field("symbol", 1, "string"),
        Field("interval", 2, "Interval"),
        Field("t", 3, "int64"),
        Field("o", 4, "string"),
        Field("c", 5, "string"),
        Field("h", 6, "string"),
        Field("l", 7, "string"),
        Field("a", 8, "string"),
        Field("q", 9, "string"),
        Field("ro", 10, "string", "optional"),
        Field("rc", 11, "string", "optional"),
        Field("rh", 12, "string", "optional"),
        Field("rl", 13, "string", "optional"),
    ),
}
"""Transcribed messages (``ContractWrapper`` lists only the members used)."""

_DEAL_KEYS = {
    "p": "p",
    "v": "v",
    "t": "T",
    "o": "O",
    "m": "M",
    "ts": "t",
    "i": "i",
    "cts": "cts",
}
"""Protobuf ``Deal`` field to JSON ``push.deal`` key."""


class Schema:
    """Protobuf message classes built from transcribed ``.proto`` fields.

    The classes are proto3 messages in a private descriptor pool, so parsing
    uses the ``protobuf`` runtime without generated code.
    """

    def __init__(
        self,
        package: str,
        messages: Mapping[str, Sequence[Field]],
        enums: Mapping[str, Mapping[str, int]],
    ) -> None:
        """Builds the descriptor pool.

        Args:
            package: Protobuf package of the synthetic file.
            messages: Fields per message name.
            enums: Values per enum name.
        """
        self._messages = messages
        self._enum_names = {
            name: {number: value for value, number in values.items()}
            for name, values in enums.items()
        }
        proto = descriptor_pb2.FileDescriptorProto(
            name=f"{package}.proto", package=package, syntax="proto3"
        )
        for enum_name, values in enums.items():
            enum = proto.enum_type.add(name=enum_name)
            for value_name, number in values.items():
                enum.value.add(name=value_name, number=number)
        for message_name, fields in messages.items():
            body = proto.message_type.add(name=message_name)
            for spec in fields:
                field = body.field.add(
                    name=spec.name, number=spec.number, json_name=spec.name
                )
                field.label = (
                    _FDP.LABEL_REPEATED
                    if spec.label == "repeated"
                    else _FDP.LABEL_OPTIONAL
                )
                if spec.kind in _SCALARS:
                    field.type = _SCALARS[spec.kind]
                else:
                    field.type = (
                        _FDP.TYPE_ENUM
                        if spec.kind in enums
                        else _FDP.TYPE_MESSAGE
                    )
                    field.type_name = f".{package}.{spec.kind}"
                if spec.label == "optional":
                    field.proto3_optional = True
                    field.oneof_index = len(body.oneof_decl)
                    body.oneof_decl.add(name=f"_{spec.name}")
        pool = descriptor_pool.DescriptorPool()
        pool.AddSerializedFile(proto.SerializeToString())
        self._classes = {
            name: message_factory.GetMessageClass(
                pool.FindMessageTypeByName(f"{package}.{name}")
            )
            for name in messages
        }

    def message_class(self, name: str) -> type[message.Message]:
        """Returns the class of a transcribed message.

        Args:
            name: Message name.

        Returns:
            Message class (encodes as well as decodes).
        """
        return self._classes[name]

    def parse(self, name: str, data: bytes) -> dict[str, Any]:
        """Parses one message into plain values.

        Args:
            name: Message name.
            data: Serialized message.

        Returns:
            Values as ``to_dict`` returns them.

        Raises:
            ValueError: If the bytes are not a valid message.
        """
        try:
            value = self._classes[name].FromString(data)
        except message.DecodeError as err:
            raise ValueError(f"undecodable MEXC {name}: {err}") from err
        return self.to_dict(value)

    def to_dict(self, value: message.Message) -> dict[str, Any]:
        """Converts a message with proto3 presence rules.

        ``optional`` scalars and messages appear only when set;
        implicit-presence scalars always appear, with their default (``0``,
        ``""``, ``False``) when the sender left them out; repeated fields
        are lists; enums are their names (numbers when unknown).

        Args:
            value: Decoded message.

        Returns:
            Field name to value.
        """
        result: dict[str, Any] = {}
        for spec in self._messages[value.DESCRIPTOR.name]:
            current = getattr(value, spec.name)
            if spec.label == "repeated":
                result[spec.name] = [
                    self._plain(spec, item) for item in current
                ]
            elif (
                spec.label != "optional" and spec.kind not in self._messages
            ) or value.HasField(spec.name):
                result[spec.name] = self._plain(spec, current)
        return result

    def _plain(self, spec: Field, value: Any) -> Any:
        """Converts one field value.

        Args:
            spec: Field.
            value: Raw value.

        Returns:
            Dict for messages, enum name (or number when unknown) for
            enums, the value itself otherwise.
        """
        if spec.kind in self._messages:
            return self.to_dict(value)
        if spec.kind in self._enum_names:
            return self._enum_names[spec.kind].get(value, value)
        return value


SCHEMA = Schema(_PACKAGE, MESSAGES, ENUMS)
"""Classes of the transcribed messages."""


def decode(frame: bytes) -> dict[str, Any]:
    """Decodes a binary push into the JSON frame layout.

    Args:
        frame: ``ContractWrapper`` bytes.

    Returns:
        ``{"channel", "symbol", "ts", "data"}`` like the JSON push
        (``symbol``/``ts`` omitted when unset; ``data`` omitted for
        payloads this adapter does not read). ``push.deal`` data is a list
        of deals with the JSON keys; ``push.kline`` data is one kline.

    Raises:
        ValueError: If the bytes are not a ``ContractWrapper``.
    """
    body = SCHEMA.parse("ContractWrapper", frame)
    result: dict[str, Any] = {"channel": body["channel"]}
    if body["symbol"]:
        result["symbol"] = body["symbol"]
    if body["ts"]:
        result["ts"] = body["ts"]
    if "deal" in body:
        result["data"] = [_json_deal(body["deal"])]
    elif "deals" in body:
        result["data"] = [_json_deal(deal) for deal in body["deals"]["deals"]]
    elif "wsKline" in body:
        result["data"] = body["wsKline"]
    return result


def _json_deal(deal: Mapping[str, Any]) -> dict[str, Any]:
    """Renames a decoded ``Deal`` to the JSON ``push.deal`` keys.

    Args:
        deal: Decoded deal.

    Returns:
        Deal keyed like JSON (``T`` side, ``t`` time, ``O``, ``M``).
    """
    return {_DEAL_KEYS[key]: value for key, value in deal.items()}


def loads(raw: str | bytes) -> Any:
    """Parses a frame of either format.

    Text frames (and binary frames that start like JSON) are JSON: requests'
    acks, errors and pongs on both endpoints, and every push on the JSON
    endpoint. Other binary frames are Protobuf pushes.

    Args:
        raw: Frame payload.

    Returns:
        Parsed JSON value or the decoded push in the JSON layout.

    Raises:
        ValueError: For malformed JSON or Protobuf.
    """
    if isinstance(raw, str) or raw[:1] in _JSON_START:
        return json.loads(raw)
    return decode(raw)
