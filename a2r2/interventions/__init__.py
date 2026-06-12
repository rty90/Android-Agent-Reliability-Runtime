"""Recovery interventions: turn A2R2 detections into generic rescues.

Blocking prevents harm but completes nothing; recovery is what raises success
rate. Every intervention here is process-level and task-agnostic:

* retry      -- the proposed action is malformed (e.g. a click with no
                coordinates): re-ask the model instead of executing garbage.
* reflect    -- the agent repeats the same action on the same screen: inject a
                short advisor note into its context.
* verify     -- the agent claims completion suspiciously early: ask it to
                double-check once before accepting the claim.

All interventions are budget-limited per episode so a wrong trigger costs a few
tokens, never the task.
"""

from a2r2.interventions.engine import Intervention, InterventionConfig, InterventionEngine

__all__ = ["Intervention", "InterventionConfig", "InterventionEngine"]
