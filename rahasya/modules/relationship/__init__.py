"""Cross-identity relationship modules (FIXES_NEW.md Workstream G).

Modules in this package are responsible for turning a single ground-truth
identifier into edges connecting it to *other* identities of the same or
related persons: recovery-hint pivots, related-person spawning signals,
etc. The correlation layer (rahasya.correlation.*) already knows how to
consume the PartialEmail/PartialPhone entities emitted here.
"""
