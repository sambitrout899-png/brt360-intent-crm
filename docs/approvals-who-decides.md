# Approvals - who decides

Admin → Approvals.

## Who decides (settings)

Each step of an approval rule is decided by the people in its position, and waits in their **My Work**. The rest is configured under **Who decides**:

| Setting | Options |
|---|---|
| When nobody holds a step's position (or the step names none) it goes to | a **fallback position** (its people get it) · **administrators** · **anyone allowed to approve** |
| Whoever asked for an approval may decide it themselves | on / off |
| Administrators may decide any step | on / off. Either way it is only in their My Work when it is routed to them |
| Segments a money rule can be limited to | the tenant's own list |

Until the settings are saved, the tenant follows **Strict approvals** (Admin → Roles & Access) as before. Off means anyone, and requesters may decide. On means administrators, and requesters may not decide. Once saved, the two stay in step: flipping Strict approvals sets both settings, and saving here sets the switch.

## The queue

My Work lists only approvals its owner can actually decide. An approval whose only possible approver is the person who asked is no longer offered to them with an Approve button that can't work. Approve / Reject shows what happened on the row itself, including why it was refused.

## Approvals nobody can decide

**Waiting now** (Admin → Approvals) lists every approval under way. It flags the ones nobody can decide, with the reason, for example:

- a step called "Yes" (an old wizard answer), which is not an approver;
- nobody holds the position, and the only person who could decide asked for it.

Administrators also see these in My Work under "Nobody can decide these". **Send to…** moves the current step to a real position, whose people then get it.

## Rules

- A step must be a real position. "Yes", "OK" and similar are refused, and existing steps like that are shown in red as *not an approver*.
- Editing a rule can also move the approvals already waiting on it to the new approvers (for steps not decided yet). This is on by default.
- Ready-made rules ask which position decides each step. Positions are no longer created empty behind the scenes.

API (administrators): `GET/PUT /api/admin/approval-policy`, `GET /api/admin/approvals/waiting`, `POST /api/admin/approvals/{id}/route` (`position_id`). `PUT /api/admin/approval-rules/{id}` takes `apply_to_pending`. `POST /api/admin/approval-rules/presets/{key}` takes `position_ids`.
