# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Shared domain kernel for the Joseph market engine.

This package holds immutable value objects, enums and errors that every
bounded context (market_data, screening, indicators, strategy, signals,
alerts, backtest) depends on. It imports only the Python standard library
and must never import frameworks, venue SDKs or infrastructure modules.
"""
