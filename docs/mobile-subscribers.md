# Mobile subscribers - prepaid, postpaid, SIM and wholesale

Everything an operator, an MVNO or an MVNE needs to run mobile lines for consumers, SMEs/SMBs and
mid-size enterprises, on top of the communications layer (accounts, billing, collections, fraud).

Code: `app/models/mobile.py`, `app/crm_core/comm_mobile.py`, `app/intent_engine/mobile_intents.py`,
`app/orchestration/actions_mobile.py`, `app/experience/routes_mobile.py`, page **Operations -> Mobile Subscribers**
(page key `mobile`, part of Telecom CRM). Migration `d7f9b1c3e5a7`.

## Intents

| Code | Intent | Prepaid | Postpaid |
| --- | --- | --- | --- |
| INT-136 | Activate mobile lines - one, or up to 50 for an SME in one go; numbers (golden number if asked), physical SIM / eSIM, plan | first recharge >= plan price | deposit, credit limit |
| INT-129 | Recharge - card, cash, UPI, wallet, bank, **voucher PIN**; extends validity; recovers emergency credit first | yes | - (use INT-85 bill payment) |
| INT-130 | Add or remove a pack - data, voice, SMS, combo, roaming, value-added service (caller tune, voicemail); auto-renew | from the wallet | on the bill |
| INT-131 | Check balance & usage - wallet, validity, spend vs limit, what is left (own + shared plan), barrings, recent usage | yes | yes |
| INT-132 | Transfer balance between prepaid lines (limit, fee) | yes | - |
| INT-133 | Emergency credit - recovered with its fee from the next recharge | yes | - |
| INT-134 | Switch prepaid <-> postpaid, keeping number and SIM; balance carried to the bill / deposit returned to the wallet | yes | yes |
| INT-135 | Spend limit and spend alert (bill-shock control) - outgoing barred at the limit until the next cycle | - | yes |
| INT-148 | Take or refund a security deposit (refund only when the account owes nothing) | - | yes |
| INT-142 | Change a line's plan (postpaid credits the unused part of the month) | yes | yes |
| INT-141 | Shared plan - family / business pool: members draw on the owner's allowance | | shared plans |
| INT-137 | SIM swap - identity verified first; cool-down on international / premium / roaming (SIM-swap fraud) | yes | yes |
| INT-138 | Change the mobile number - the old one is quarantined | yes | yes |
| INT-139 | Bar / unbar - lost or stolen, fraud, non-payment, outgoing, incoming, international, premium, data, roaming | yes | yes |
| INT-140 | Roaming on / off (prepaid minimum balance; postpaid in good standing with a limit or deposit), optional roaming pack | yes | yes |
| INT-143 | Suspend, resume, terminate - prepaid balance refunded or forfeited, postpaid deposit applied to the final bill, number quarantined | yes | yes |
| INT-144 | Charge usage online (what the network's charging system asks): packs first, then wallet / bill; refused with the reason when barred or out of money | yes | yes |
| INT-145 | Generate recharge vouchers (admin) - PINs downloaded **once**, only hashes kept | | |
| INT-146 | Onboard / change a wholesale partner (admin) - MVNO, MVNE, reseller, dealer: wholesale rates, revenue share, number range | | |
| INT-147 | Settle with a partner - its lines' usage at wholesale rates, less commission on recharges it sold | | |

Already in place and part of the same journeys: identity verification (INT-13), MNP port-in / port-out and
asset changes (INT-107), billing profiles (INT-74), bill run / payments / adjustments / arrangements / collections
(INT-84..92), fraud (INT-93/94/76), tickets and diagnosis (INT-10..12, INT-95..99). The function map has the
journeys **Prepaid Mobile**, **Postpaid Mobile**, **SIM & Number Care** and **Wholesale & MVNO**.

Each intent works from the command bar in plain English (e.g. "Recharge 9100000001 with 199 by UPI",
"Activate 20 postpaid lines on BIZ_599 for customer 7 as eSIMs", "Turn on roaming for 9100000001 with ROAM7"),
from its form, from the buttons on a line, and from Flow Studio (inputs and `result.*` outputs are listed).

## How money moves

- **Prepaid** money lives in the line's wallet (`mb_lines.balance`, every movement in `mb_wallet_txns`). Prepaid
  usage is recorded in the usage history at amount 0, so the bill run never bills it again.
- **Postpaid** plan fees, packs, fees and usage post to the account's billing ledger, so the existing bill run bills
  them. The credit limit is checked against the cycle's spend (plan fee included).
- **Validity**: a recharge keeps a prepaid line valid for `prepaid_validity_days` (a voucher carries its own). After
  it ends the line is barred for outgoing use ("validity"); after `grace_days` it is suspended. A recharge restores it.
- **Packs** (incl. the plan's own monthly allowance) renew automatically when set to; a prepaid renewal needs the
  money on the wallet, otherwise the pack lapses and the line's history says why.

## Numbers and SIMs

- Numbers come from the partner's range (MVNO lines), else Inventory's phone-number pool, else the tenant's
  `number_range`. A number given up (termination, number change) is quarantined for `quarantine_days`, then returns.
- SIMs come from Inventory's SIM stock (with their IMSI), or the ICCID given; eSIMs are generated with an
  activation code (`LPA:1$<smdp_address>$<code>`). A physical SIM needs an ICCID when Inventory keeps no SIM stock.
  A swapped-out SIM is retired in Inventory.

## Policy (Mobile -> Plans & packs, admin)

Plans, packs, `number_range`, `imsi_prefix`, `smdp_address`, recharge min/max, low-balance alert, emergency credit
amount / fee / minimum days on the network, transfer limit and fee, SIM swap and number change fees, swap cool-down,
postpaid default deposit and credit limit (and its maximum), roaming minimum balance and multiplier, shared-plan
size, bulk activation and voucher batch limits. Stored in the communications settings under key `mobile`.
**The default plans, packs and prices are placeholders - set your own before going live.**

## Scheduler

"Mobile subscribers" runs every 15 minutes: packs renew or expire, prepaid validity and grace, postpaid cycles
restart (spend back to 0, a credit-limit barring lifts), quarantined numbers return, vouchers past expiry expire.
Admins can run it now with `POST /api/mobile/housekeeping`.

## Security notes

- Voucher PINs: only a salted SHA-256 hash is stored; a batch's PINs are kept until downloaded once from
  Mobile -> Vouchers (admin) and then cleared. Five wrong PINs on a line in 24 hours stop PIN recharges for the day.
- Card numbers are never accepted - only the last 4 digits (same rule as billing).
- SIM swap and lifting a lost / stolen barring require the agent to confirm the identity check.

## Not included yet

No live network / HLR / OCS / SM-DP+ integration (provisioning is recorded, the network adapters are not wired);
no payment gateway for recharges (payments are recorded); usage arrives through INT-144 or the API, not a CDR
mediation feed; no number portability messaging with a clearing house beyond INT-107's port orders.
