# Copyright 2026 apoman123
# SPDX-License-Identifier: MIT

"""Signal pipeline: closed candles → indicator engine → strategy runtime.

Application-level orchestration that connects the market-data, indicator,
screening and strategy contexts. Contains no trading or indicator logic of
its own; each step is delegated to the owning context.
"""
