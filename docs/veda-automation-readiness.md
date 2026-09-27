# Automation readiness report

`scripts/check_automation_readiness.py CHECKPOINT --output NEW_REPORT` inventories implementation support for a historical strategy checkpoint. It reads at most 1 MB, binds the report to the exact input bytes, and leaves existing output files untouched. Exit 0 means the report was produced; it does not mean automation is ready. Exit 2 means the input or output could not be processed.

The report preserves exact card variants and duplicate counts. A checked numeric effect, a routine-planner candidate, a follow-up selection, and a completely executable action are separate capabilities. For example, base True Grit has a checked Block effect and a random-exhaust boundary. The exhausted card remains unknown until observed; this does not add the upgraded card's selection flow. Headbutt's routine candidate requires a confirmed empty discard pile; its return selection is not implemented.

Relic allowlisting does not claim a complete interaction simulator. Recorded potion slots do not establish current contents or controller support. Collector's reviewed A2 attack-only case does not cover Spawn, Revive, Buff or Mega Debuff ordering, other Ascension variants, or the next rolled move.

The report lists the outstanding complete-reader, per-field validation, live evidence, bridge, arming, selection, potion, noncombat and runtime-ledger integration requirements. It always returns `runtime_authorized: false`, `controller_authorized: false` and `autonomy_ready: false`. Caller-provided flags and calibration summaries cannot change those results. A recent checkpoint timestamp is still historical metadata, not a fresh game observation.

This command does not capture a screen, follow linked evidence paths, connect to SQLite, invoke a model, contact the bridge or send input. It is an implementation inventory, not a tactical recommendation or an authorization mechanism.
