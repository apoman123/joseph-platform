# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signature test vectors for every live venue.

* Binance: the official HMAC example of the USDⓈ-M "General Info" page
  (``POST /fapi/v1/order`` with the documented apiKey/secretKey).
* Bitget and MEXC publish payload strings but no secret/signature pairs;
  their documented payload strings are signed with a test secret and the
  expected digests were computed independently with OpenSSL
  (``openssl dgst -sha256 -hmac``).
* Hyperliquid: every signature vector of the official SDK's
  ``tests/signing_test.py`` (commit 2fdb18f9), reproduced with
  ``eth-account`` + ``msgpack``; the expiry vector was produced by running
  the SDK's own ``sign_l1_action``.
* Lighter: the official native signer (``lighter-sdk``) signs offline with
  explicit nonces; signatures are randomized, so the transaction fields are
  checked.
"""

from __future__ import annotations

import json

import pytest

from joseph_execution.adapters.venues import bitget
from joseph_execution.adapters.venues import hyperliquid
from joseph_execution.adapters.venues import lighter
from joseph_execution.adapters.venues import signing

_HL_KEY = "0x0123456789012345678901234567890123456789012345678901234567890123"
_SECRET = "joseph-test-secret"


def test_binance_official_hmac_example() -> None:
    """The futures docs' signed order query reproduces exactly."""
    secret = "2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9"
    query = signing.encode_query(
        [
            ("symbol", "BTCUSDT"),
            ("side", "BUY"),
            ("type", "LIMIT"),
            ("quantity", "1"),
            ("price", "9000"),
            ("timeInForce", "GTC"),
            ("recvWindow", "5000"),
            ("timestamp", "1591702613943"),
        ]
    )
    assert query == (
        "symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000"
        "&timeInForce=GTC&recvWindow=5000&timestamp=1591702613943"
    )
    assert signing.hmac_sha256_hex(secret, query) == (
        "3c661234138461fcc7a7d8746c6558c9842d4e10870d2ecbedf7777cad694af9"
    )


def test_bitget_prehash_examples_sign_like_openssl() -> None:
    """Bitget's documented prehash strings sign to OpenSSL's digests."""
    body = (
        '{"productType":"usdt-futures","symbol":"BTCUSDT","size":"8",'
        '"marginMode":"crossed","side":"buy","orderType":"limit",'
        '"clientOid":"channel#123456"}'
    )
    assert (
        bitget.sign(
            _SECRET,
            "16273667805456",
            "POST",
            "/api/v2/mix/order/place-order",
            body,
        )
        == "6cybFnCqZNa2vd2cIlnW3srpHvtv286r3rvixDg/AYs="
    )
    assert (
        bitget.sign(
            _SECRET,
            "16273667805456",
            "GET",
            "/api/v3/account/fee-rate?category=SPOT&symbol=BTCUSDT",
            "",
        )
        == "cf/0ZzKciHwvChd+ubcjryRzk4r7BbLWGzCSzktJat8="
    )
    assert bitget.sign(_SECRET, "1538054050", "GET", "/user/verify", "") == (
        "O/Z9rOsWC4dGc/Os4welQk0fAP0j9TgcUTyxV2IYfHQ="
    )


def test_mexc_signature_strings_sign_like_openssl() -> None:
    """MEXC's documented target string (accessKey + time) signs correctly."""
    target = "mx0aBYs33eIilxBW5C" + "1657186536762"
    assert target == "mx0aBYs33eIilxBW5C1657186536762"
    assert signing.hmac_sha256_hex(_SECRET, target) == (
        "ab3c8b95440a1003601081d7af8ca452f77be323e5144653a6c3e61b206e71c1"
    )
    body = (
        '{"symbol":"BTC_USDT","price":"9000","vol":"1","side":1,"type":1,'
        '"openType":1}'
    )
    assert signing.hmac_sha256_hex(_SECRET, target + body) == (
        "a0b057d038f85ee642a169af22755a057219791953a22a14cc9ac776f672efd7"
    )


def _order_wire(
    order_type: dict[str, object], cloid: str | None = None
) -> dict[str, object]:
    """Builds the SDK test order (ETH, asset 1, buy 100 @ 100).

    Args:
        order_type: Order type object, the wire ``t`` field.
        cloid: Client order id, the wire ``c`` field; omitted when ``None``.

    Returns:
        An ``order`` action holding that one order, without grouping.
    """
    order: dict[str, object] = {
        "a": 1,
        "b": True,
        "p": "100",
        "s": "100",
        "r": False,
        "t": order_type,
    }
    if cloid is not None:
        order["c"] = cloid
    return {"type": "order", "orders": [order], "grouping": "na"}


_HL_VECTORS = [
    (
        {"type": "dummy", "num": 100_000_000_000},
        None,
        True,
        (
            "0x53749d5b30552aeb2fca34b530185976545bb22d0b3ce6f62e31be961a59298",
            "0x755c40ba9bf05223521753995abb2f73ab3229be8ec921f350cb447e384d8ed8",
            27,
        ),
    ),
    (
        {"type": "dummy", "num": 100_000_000_000},
        None,
        False,
        (
            "0x542af61ef1f429707e3c76c5293c80d01f74ef853e34b76efffcb57e574f9510",
            "0x17b8b32f086e8cdede991f1e2c529f5dd5297cbe8128500e00cbaf766204a613",
            28,
        ),
    ),
    (
        _order_wire({"limit": {"tif": "Gtc"}}),
        None,
        True,
        (
            "0xd65369825a9df5d80099e513cce430311d7d26ddf477f5b3a33d2806b100d78e",
            "0x2b54116ff64054968aa237c20ca9ff68000f977c93289157748a3162b6ea940e",
            28,
        ),
    ),
    (
        _order_wire({"limit": {"tif": "Gtc"}}),
        None,
        False,
        (
            "0x82b2ba28e76b3d761093aaded1b1cdad4960b3af30212b343fb2e6cdfa4e3d54",
            "0x6b53878fc99d26047f4d7e8c90eb98955a109f44209163f52d8dc4278cbbd9f5",
            27,
        ),
    ),
    (
        _order_wire(
            {"limit": {"tif": "Gtc"}}, "0x00000000000000000000000000000001"
        ),
        None,
        True,
        (
            "0x41ae18e8239a56cacbc5dad94d45d0b747e5da11ad564077fcac71277a946e3",
            "0x3c61f667e747404fe7eea8f90ab0e76cc12ce60270438b2058324681a00116da",
            27,
        ),
    ),
    (
        _order_wire(
            {"limit": {"tif": "Gtc"}}, "0x00000000000000000000000000000001"
        ),
        None,
        False,
        (
            "0xeba0664bed2676fc4e5a743bf89e5c7501aa6d870bdb9446e122c9466c5cd16d",
            "0x7f3e74825c9114bc59086f1eebea2928c190fdfbfde144827cb02b85bbe90988",
            28,
        ),
    ),
    (
        _order_wire(
            {"trigger": {"isMarket": True, "triggerPx": "103", "tpsl": "sl"}}
        ),
        None,
        True,
        (
            "0x98343f2b5ae8e26bb2587daad3863bc70d8792b09af1841b6fdd530a2065a3f9",
            "0x6b5bb6bb0633b710aa22b721dd9dee6d083646a5f8e581a20b545be6c1feb405",
            27,
        ),
    ),
    (
        _order_wire(
            {"trigger": {"isMarket": True, "triggerPx": "103", "tpsl": "sl"}}
        ),
        None,
        False,
        (
            "0x971c554d917c44e0e1b6cc45d8f9404f32172a9d3b3566262347d0302896a2e4",
            "0x206257b104788f80450f8e786c329daa589aa0b32ba96948201ae556d5637eac",
            28,
        ),
    ),
    (
        {"type": "dummy", "num": 100_000_000_000},
        "0x1719884eb866cb12b2287399b15f7db5e7d775ea",
        True,
        (
            "0x3c548db75e479f8012acf3000ca3a6b05606bc2ec0c29c50c515066a326239",
            "0x4d402be7396ce74fbba3795769cda45aec00dc3125a984f2a9f23177b190da2c",
            28,
        ),
    ),
    (
        {"type": "scheduleCancel", "time": 123456789},
        None,
        True,
        (
            "0x609cb20c737945d070716dcc696ba030e9976fcf5edad87afa7d877493109d55",
            "0x16c685d63b5c7a04512d73f183b3d7a00da5406ff1f8aad33f8ae2163bab758b",
            28,
        ),
    ),
]


@pytest.mark.parametrize(
    ("action", "vault", "mainnet", "expected"), _HL_VECTORS
)
def test_hyperliquid_sdk_vectors(
    action: dict[str, object],
    vault: str | None,
    mainnet: bool,
    expected: tuple[str, str, int],
) -> None:
    """Every official SDK L1 signing vector reproduces (nonce 0)."""
    signature = hyperliquid.sign_l1_action(
        _HL_KEY, action, vault, 0, None, mainnet
    )
    assert (signature["r"], signature["s"], signature["v"]) == expected


def test_hyperliquid_phantom_agent_and_expiry() -> None:
    """The SDK's phantom-agent hash and an expiry signature reproduce."""
    action = {
        "type": "order",
        "orders": [
            {
                "a": 4,
                "b": True,
                "p": "1670.1",
                "s": "0.0147",
                "r": False,
                "t": {"limit": {"tif": "Ioc"}},
            }
        ],
        "grouping": "na",
    }
    digest = hyperliquid.action_hash(action, None, 1677777606040, None)
    assert "0x" + digest.hex() == (
        "0x0fcbeda5ae3c4950a548021552a4fea2226858c4453571bf3f24ba017eac2908"
    )
    order = _order_wire({"limit": {"tif": "Gtc"}})
    assert hyperliquid.action_hash(order, None, 0, None).hex() == (
        "884f2c32bb6dbdd65f6033e32fb28c0cb6f5b345db0f6471fd3366d85c9252c1"
    )
    assert hyperliquid.action_hash(order, None, 0, 1_700_000_000_000).hex() == (
        "bfbb24419b62b190625a57b932968f8edc25d4e27ead21954202b8d6a7f6d5a3"
    )
    signed = hyperliquid.sign_l1_action(
        _HL_KEY, order, None, 0, 1_700_000_000_000, True
    )
    assert signed == {
        "r": "0xb508e3f051d08cbe967711165e57de575bc9ae1b0"
        "de24f0d60608120a56958af",
        "s": "0x533289adbea6d4e7bd98c94181c4ded1811be7f42a"
        "31418fd331466556bd2b4e",
        "v": 28,
    }
    assert hyperliquid.address_of(_HL_KEY) == (
        "0x14791697260e4c9a71f18484c9f997b308e59325"
    )


async def test_lighter_native_signer_signs_offline() -> None:
    """The official signer produces create/cancel transactions offline."""
    sdk = pytest.importorskip("lighter.signer_client")
    private, _, err = sdk.create_api_key()
    assert err is None
    signer = lighter.sdk_signer("http://127.0.0.1:9", 7, 4, private, 300)
    tx_type, tx_info, tx_hash, error = signer.sign_create_order(
        4096,
        12345,
        100,
        950000,
        False,
        0,
        1,
        False,
        0,
        -1,
        nonce=5,
        api_key_index=4,
    )
    assert error is None and tx_type == 14 and tx_hash
    fields = json.loads(tx_info)
    assert fields["MarketIndex"] == 4096
    assert fields["ClientOrderIndex"] == 12345
    assert fields["BaseAmount"] == 100 and fields["Price"] == 950000
    assert fields["Nonce"] == 5 and fields["ApiKeyIndex"] == 4
    assert fields["AccountIndex"] == 7 and fields["Sig"]
    cancel = signer.sign_cancel_order(4096, 12345, nonce=6, api_key_index=4)
    assert cancel[0] == 15 and json.loads(cancel[1])["Index"] == 12345
    token, token_err = signer.create_auth_token_with_expiry(
        600, api_key_index=4
    )
    assert token_err is None and token is not None
    assert token.split(":")[1:3] == ["7", "4"]
    await signer.close()  # type: ignore[attr-defined]  # the Signer protocol omits the SDK client's close()
