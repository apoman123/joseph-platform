"""MEXC futures private WebSocket frames in Protocol Buffers and JSON.

MEXC is moving its futures WebSocket *market data* from JSON to Protocol
Buffers ("MEXC to Migrate Futures WebSocket Market Data from JSON to
Protobuf", 2026-11-12, "Migration Deadline: Dec 1, 2026, 10:00 (UTC)"):
https://www.mexc.com/announcements/article/mexc-to-migrate-futures-websocket-market-data-from-json-to-protobuf-17827791539697
Neither the announcement nor the docs say whether the private
``push.personal.*`` channels change. The private-channel pages
(https://www.mexc.com/api-docs/futures/websocket-api/order and its
siblings) document JSON only. Sources (read 2026-11-23):

* Update log, https://www.mexc.com/api-docs/futures/update-log
  (2026-10-23: Protobuf docs and the endpoint
  ``wss://contract.mexc.com/edge?Trans-Protocol=Protobuf``; "Futures
  WebSocket supports both JSON and Protobuf").
* https://www.mexc.com/api-docs/futures/websocket-api/protocol-buffers-integration:
  requests stay UTF-8 JSON; server pushes are raw Protobuf binary.
* Official definitions: https://github.com/mexcdevelop/websocket-future-proto
  (Apache-2.0, commit ``0338258``), vendored unmodified in ``mexc_proto/``
  (see ``mexc_proto/NOTICE``). The ``ContractWrapper`` ``data`` oneof does
  carry private payloads: ``OrderChange`` (1001), ``AssetChange`` (1002),
  ``OrderDealDetail`` (1008), ``ContractPlanOrder`` (1009) and
  ``PositionChange`` (1011).

Verified live on 2026-11-23 without credentials: on both endpoints a
refused ``login`` answers with a JSON text ``rs.error`` frame, like
subscription acks and ``pong`` on the Protobuf endpoint. Private pushes in
Protobuf could not be observed without an account. Their field names
match the JSON pushes, except that ``OrderDealDetail`` names the taker flag
``taker`` and Protobuf strings carry decimals.

The stream therefore parses both formats on any endpoint: text frames as
JSON and binary frames as ``ContractWrapper``. A decoded private payload is
reshaped to the JSON push layout under the JSON channel name of its payload
type, so one parser serves both formats. Decoding uses the ``protobuf``
runtime with descriptors built here from a transcription of the vendored
files, so no generated code is needed. ``tests/unit/venues/
test_mexc_protobuf.py`` checks the transcription against the vendored
files.
"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping
from collections.abc import Sequence
from typing import Any

from google.protobuf import descriptor_pb2
from google.protobuf import descriptor_pool
from google.protobuf import message
from google.protobuf import message_factory

from joseph_execution.adapters.venues import http

PROTO_DIR = pathlib.Path(__file__).with_name("mexc_proto")
"""Vendored official ``.proto`` files (with ``LICENSE`` and ``NOTICE``)."""

_PACKAGE = "joseph_mexc_futures_private_ws"
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


MESSAGES: Mapping[str, Sequence[Field]] = {
    "ContractWrapper": (
        Field("symbol", 1, "string"),
        Field("channel", 2, "string"),
        Field("ts", 3, "int64"),
        Field("orderChange", 1001, "OrderChange"),
        Field("assetChange", 1002, "AssetChange"),
        Field("orderDealDetail", 1008, "OrderDealDetail"),
        Field("contractPlanOrder", 1009, "ContractPlanOrder"),
        Field("positionChange", 1011, "PositionChange"),
    ),
    "OrderChange": (
        Field("orderId", 1, "string"),
        Field("positionId", 2, "int64", "optional"),
        Field("openType", 3, "int32"),
        Field("side", 4, "int32"),
        Field("price", 5, "string", "optional"),
        Field("vol", 6, "string"),
        Field("remainVol", 7, "string"),
        Field("dealAvgPrice", 8, "string", "optional"),
        Field("dealVol", 9, "string"),
        Field("state", 10, "int32"),
        Field("version", 11, "int64", "optional"),
        Field("category", 12, "int32"),
        Field("orderType", 13, "int64"),
        Field("symbol", 14, "string"),
        Field("orderMargin", 15, "string", "optional"),
        Field("takerFee", 16, "string", "optional"),
        Field("makerFee", 17, "string", "optional"),
        Field("profit", 18, "string", "optional"),
        Field("feeCurrency", 19, "string"),
        Field("usedMargin", 20, "string", "optional"),
        Field("leverage", 21, "int32", "optional"),
        Field("errorCode", 22, "int64"),
        Field("externalOid", 23, "string", "optional"),
        Field("marketOrderLevel", 24, "int32", "optional"),
        Field("positionMode", 25, "int32"),
        Field("reduceOnly", 26, "bool", "optional"),
        Field("createTime", 27, "int64", "optional"),
        Field("updateTime", 28, "int64", "optional"),
        Field("bboTypeNum", 29, "int32"),
        Field("makerFeeRate", 30, "string"),
        Field("takerFeeRate", 31, "string"),
        Field("bizSource", 32, "int32", "optional"),
        Field("sideType", 33, "string", "optional"),
        Field("positionType", 34, "string", "optional"),
    ),
    "AssetChange": (
        Field("currency", 1, "string", "optional"),
        Field("availableBalance", 2, "string", "optional"),
        Field("frozenBalance", 3, "string", "optional"),
        Field("positionMargin", 4, "string", "optional"),
        Field("bonus", 5, "string", "optional"),
    ),
    "OrderDealDetail": (
        Field("id", 1, "string", "optional"),
        Field("symbol", 2, "string"),
        Field("side", 3, "int32"),
        Field("vol", 4, "string", "optional"),
        Field("price", 5, "string", "optional"),
        Field("feeCurrency", 6, "string", "optional"),
        Field("fee", 7, "string", "optional"),
        Field("timestamp", 8, "int64", "optional"),
        Field("profit", 9, "string", "optional"),
        Field("taker", 10, "bool"),
        Field("category", 11, "int32"),
        Field("orderId", 12, "string"),
        Field("isSelf", 13, "bool", "optional"),
        Field("externalOid", 14, "string", "optional"),
        Field("positionMode", 15, "int32"),
        Field("reduceOnly", 16, "bool", "optional"),
        Field("opponentUid", 17, "int64", "optional"),
    ),
    "ContractPlanOrder": (
        Field("id", 1, "string", "optional"),
        Field("symbol", 2, "string"),
        Field("leverage", 3, "int32", "optional"),
        Field("side", 4, "int32"),
        Field("triggerPrice", 5, "string", "optional"),
        Field("price", 6, "string", "optional"),
        Field("vol", 7, "string", "optional"),
        Field("openType", 8, "int32", "optional"),
        Field("triggerType", 9, "int32"),
        Field("state", 10, "int32"),
        Field("executeCycle", 11, "int32", "optional"),
        Field("trend", 12, "int32"),
        Field("orderType", 13, "int32"),
        Field("orderId", 14, "string", "optional"),
        Field("errorCode", 15, "int32", "optional"),
        Field("marketOrderLevel", 16, "int32", "optional"),
        Field("createTime", 17, "int64"),
        Field("updateTime", 18, "int64"),
        Field("positionMode", 19, "int32", "optional"),
        Field("lossTrend", 20, "int32", "optional"),
        Field("profitTrend", 21, "int32", "optional"),
        Field("stopLossPrice", 22, "string", "optional"),
        Field("takeProfitPrice", 23, "string", "optional"),
        Field("reduceOnly", 24, "bool"),
        Field("ensureStopLoss", 25, "int32", "optional"),
        Field("extraTakerFeeRate", 26, "string", "optional"),
    ),
    "PositionChange": (
        Field("positionId", 1, "int64"),
        Field("symbol", 2, "string"),
        Field("state", 3, "int32"),
        Field("holdVol", 4, "string", "optional"),
        Field("frozenVol", 5, "string", "optional"),
        Field("closeVol", 6, "string", "optional"),
        Field("holdAvgPrice", 7, "string", "optional"),
        Field("holdAvgPriceFullyScale", 8, "string", "optional"),
        Field("openAvgPrice", 9, "string", "optional"),
        Field("openAvgPriceFullyScale", 10, "string", "optional"),
        Field("closeAvgPrice", 11, "string", "optional"),
        Field("liquidatePrice", 12, "string", "optional"),
        Field("oim", 13, "string", "optional"),
        Field("im", 14, "string", "optional"),
        Field("mm", 15, "string", "optional"),
        Field("holdFee", 16, "string", "optional"),
        Field("realised", 17, "string", "optional"),
        Field("adlLevel", 18, "int32", "optional"),
        Field("positionType", 19, "int32"),
        Field("openType", 20, "int32"),
        Field("leverage", 21, "int32", "optional"),
        Field("autoAddIm", 22, "bool", "optional"),
        Field("pnl", 23, "string"),
        Field("marginRatio", 24, "string", "optional"),
        Field("createTime", 25, "int64", "optional"),
        Field("updateTime", 26, "int64", "optional"),
        Field("version", 27, "int64", "optional"),
        Field("newOpenAvgPrice", 28, "string", "optional"),
        Field("newCloseAvgPrice", 29, "string", "optional"),
        Field("closeProfitLoss", 30, "string", "optional"),
        Field("fee", 31, "string", "optional"),
        Field("deductFeeList", 32, "PositionDeductFee", "repeated"),
        Field("makerFeeRate", 33, "string"),
        Field("takerFeeRate", 34, "string"),
    ),
    "PositionDeductFee": (
        Field("currency", 1, "string"),
        Field("deductFee", 2, "string"),
        Field("convertSettleFee", 3, "string"),
    ),
}
"""Transcribed messages (``ContractWrapper`` lists only the members used)."""

CHANNELS: Mapping[str, str] = {
    "orderChange": "push.personal.order",
    "assetChange": "push.personal.asset",
    "orderDealDetail": "push.personal.order.deal",
    "contractPlanOrder": "push.personal.plan.order",
    "positionChange": "push.personal.position",
}
"""JSON channel of each decoded ``ContractWrapper`` payload member."""


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


SCHEMA = Schema(_PACKAGE, MESSAGES, {})
"""Classes of the transcribed messages."""


def decode(frame: bytes) -> dict[str, Any]:
    """Decodes a binary push into the JSON frame layout.

    Args:
        frame: ``ContractWrapper`` bytes.

    Returns:
        ``{"channel", "symbol", "ts", "data"}`` like the JSON push. A
        private payload sets ``channel`` to its JSON channel (``CHANNELS``)
        and ``data`` to its fields; other payloads keep the wrapper's
        channel and carry no ``data``. ``symbol``/``ts`` are omitted when
        unset.

    Raises:
        ValueError: If the bytes are not a ``ContractWrapper``.
    """
    body = SCHEMA.parse("ContractWrapper", frame)
    result: dict[str, Any] = {"channel": body["channel"]}
    if body["symbol"]:
        result["symbol"] = body["symbol"]
    if body["ts"]:
        result["ts"] = body["ts"]
    for member, channel in CHANNELS.items():
        if member in body:
            result["channel"] = channel
            result["data"] = body[member]
            break
    return result


def loads(raw: str | bytes) -> Any:
    """Parses a frame of either format.

    Text frames (and binary frames that start like JSON) are JSON with exact
    decimals; other binary frames are Protobuf pushes.

    Args:
        raw: Frame payload.

    Returns:
        Parsed JSON value or the decoded push in the JSON layout.

    Raises:
        ValueError: For malformed JSON or Protobuf.
    """
    if isinstance(raw, str):
        return http.parse_json(raw)
    if raw[:1] in _JSON_START:
        return http.parse_json(raw.decode())
    return decode(raw)
