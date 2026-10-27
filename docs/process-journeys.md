# Process composition and memory

The Intent Command Bar's **Build a process** mode supports ordered journeys:

```
Build a B2C process - customer -> identity verification -> raise order -> installation -> service activation on network -> connectivity testing -> acceptance -> start billing -> installed asset
```

The builder suggests a record to hold progress (customer for B2C). It composes a custom picklist field, page instructions and completion buttons, and a Flow Studio action for each stage. Each action checks the preceding completed stage before updating progress. A journey currently accepts two to ten distinct stages. A request must name a process, workflow, journey, lifecycle or procedure to distinguish configuration from an immediate action chain.

**These journey actions record human confirmation.** They do not perform identity verification, network activation, installation, billing, or asset creation. Existing explicit trigger/action, approval, assignment and page requests still use the existing process compiler. External operations need actual adapters and execution contracts before they can be automated. This is not a universal autonomous process executor.

Build rehearsal rolls configuration back. Execute saves it, then checks each generated flow on a sample record with temporary test progress; those test writes are rolled back. No sample record means execution verification is incomplete, and the recipe is not memorized. Rebuilding the same journey reuses matching active flows and page elements. Page fields are read-only in the generated layout; this does not add a separate access-control boundary around custom fields.

Successfully verified builds save versioned recipes in the tenant's existing audit store, using `PROCESS-RECIPE`. No schema migration or model training is required. Exact repeat requests recall their steps and decisions, then revalidate against the tenant's current configuration. The private model receives up to three recent tenant recipes as reference patterns when planning other requests; generated steps still pass the builder's grounding checks. This is retrieval memory, not neural-model fine-tuning.

Administrators can list recipes with `GET /api/process-builder/recipes` or choose **Reuse a verified process** in the UI. Memory includes the original request, steps and decisions, never another tenant's recipes. Failed builds, rehearsals and incompletely verified builds are excluded. Memory uses the existing audit retention policy and returns the latest fifty recipes.

Changes are local source changes. Restart/redeploy the backend and deploy the rebuilt frontend to make them available in a running installation.
