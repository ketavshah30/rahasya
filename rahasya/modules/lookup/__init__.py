"""Reverse-lookup module family (FIXES_NEW.md Workstream E).

Each module in this package takes a *ground-truth identifier* (email, phone,
name, or a proven username) and returns which platforms / accounts /
services attest that identifier. Reverse-lookup modules are what pivot us
from a bare email (which is the common seed) to a real, platform-scoped
handle — a job the previous pipeline mis-assigned to Sherlock/Maigret by
`.split("@")[0]`-ing the email.
"""
