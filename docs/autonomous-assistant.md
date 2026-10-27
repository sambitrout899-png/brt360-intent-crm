# Autonomous assistant

In Intent Command Bar → Ask anything, enable **Autonomous mode**, describe the
goal, and select **Go**. The choice applies to that request and is off on a new
page load.

The assistant consults the tenant's configured model, with existing permitted
intents, custom flows, phrases, examples and segment restrictions. It falls back
to the existing rule planner when no model is available. This does not install
or train a different model.

Action plans rehearse before execution. Missing details open the relevant forms.
One unambiguous suggested prerequisite repair can be applied automatically,
with at most two repair rounds; ambiguous or unsuccessful repairs stop for review.
Interactive order configuration pauses instead of claiming an order was created.
Existing permissions, approval gates and transaction rollback remain in effect.

Administrator process requests use the existing process builder. Ready blueprints
with no outstanding questions or engineering requirements are rehearsed and built,
including supported flows, approval rules, assignments and forms. New custom actions
are built automatically only when their draft has no warnings. Unclear business
rules, unsupported operations and warning-bearing drafts require input.

**Start another request** stops further automatic requests from the current panel.
It cannot cancel an execution or build already submitted to the server.

Flows with explicit graph `segment` metadata use that scope. Legacy flows with
exactly one B2B/B2C marker in their name inherit that scope. Automatic triggers
skip records from the other segment (or whose segment cannot be resolved);
direct invocations are rejected. Generated flows retain segment metadata from
the request. Record data takes precedence over user-entered segment values.

Deploy the frontend and backend together with the existing deployment script.
No database migration is required. These changes do not repair previously
modified production records or certify that arbitrary English requests can
always be implemented.
