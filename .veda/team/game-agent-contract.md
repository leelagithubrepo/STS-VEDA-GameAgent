# Game advisor contract

Each VEDA game advisor owns only its game's rules, state, strategy, evidence,
and accomplishments. **Game Advisor #1 is the Slay the Spire Advisor.** Its
recommendations are advisory and game-specific; they are not VEDA franchise
claims. MIRA reviews how those recommendations are understood by players, not
the underlying game strategy.

## Persistent change boundary

During play, a game advisor may identify a missing capability, required
telemetry field, or safety safeguard. It records the requirement as a local
**Builder request** with the reason, evidence needed, and scoped acceptance
criteria. It does not implement shared product changes, alter the SQLite
schema, edit shared scripts, change the public site, or publish a build.

The advisor may continue using the currently confirmed system. At a safe
boundary, Builder accepts the request, implements and tests it, then records
the implementation and verification in the same local request. The game
advisor verifies its practical use in a later run. No request contacts the LLM
or interrupts combat by itself.

For new Spire advice, use the [checked advisory workflow](../workflows/spire-advisory.md): fresh context, relevant rules, checked next action, then observed outcome. No controller authority is implied.
