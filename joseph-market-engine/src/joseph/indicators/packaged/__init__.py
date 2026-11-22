"""Adapters running the external ``smc_indicators`` package as plugins.

The indicator algorithms live in the ``apoman123/PineScripts`` repository
(``python_indicators/``, distribution ``joseph-smc-indicators``), which ports
the Pine Script sources line by line. This package only translates between
that library and the platform's plugin SDK; it contains no indicator logic.
"""
