"""Compatibility exports for the module plugin layer.

The implementation lives in :mod:`rahasya.modules`; this module keeps older
imports working for tests and external callers.
"""

from rahasya.modules import ModuleRegistry
from rahasya.modules.base import BaseModule

__all__ = ["BaseModule", "ModuleRegistry"]
