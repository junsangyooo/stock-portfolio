"""Qualitative analysis (news / trend / issue) agent — Phase 2.

Pipeline layers:
  L1 MAPPING   symbol -> themes        (mapping.py, portfolio.db.holding_themes)
  L2 CATALOG   theme master            (themes.json + catalog.py)
  L3 COLLECT   theme-level sources      (collect.py — N1)
  L4 EVALUATE  digest -> state -> score (digest/state/evaluate.py — N2/N3)
"""
