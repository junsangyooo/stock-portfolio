"""Qualitative analysis — what happened in the world, per field.

  CATALOG    theme master + source registry   themes.json / sources.json / catalog.py
  COLLECT    one sweep over every source      collect_all.py + fetch/ → news.db signals
  ENRICH     summary·importance·novelty·theme enrich.py       → news.db signals + signal_themes
  DAILY      per-theme overview + sentiment   daily.py        → news.db theme_daily
  ROLLUP     weekly / monthly compression     rollup.py       → theme_weekly / theme_monthly
  READ       every query against news.db      read.py
  ASSEMBLE   per-symbol inputs for analyze    inputs.py

cycle_job.py is the cron entry. Collection is independent of holdings — every
field is always on, and the filtering lives on the read side.
"""
