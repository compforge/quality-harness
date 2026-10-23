# Service-held resources

A Resource describes a collection of resources that a running Service persistently manages or uses.
Its instances, count and scale can change without changing the declaration. Examples include Beds,
sandboxes and resident sessions. It is an inventory concept, not a universal base class for every
business record, Workload or DataSource.

## Ownership and observation

Common owns `Resource`, `ServiceResource`, `ResourceInstanceRef`, `ResourceInstance`,
`ResourceMeasurement`, `ResourceCoverage` and `ResourceSnapshot`. TypeScript and Python expose
matching semantics; `conformance/resources.json` supplies shared identity and coverage examples.

- Resource `id` identifies a collection across managing and consuming Services. `kind`, name and
  description describe it; declarations perform no I/O and prove no live instances.
- ServiceResource associates a Service and Resource with `manages` or `uses`. The association does
  not create a client, establish exclusive ownership or change the underlying resource identity.
- Instance identity is environment + resource collection ID + local instance ID. Providers include
  backend/namespace identity in the collection ID when local IDs can collide. Do not key by display
  label or a temporary access endpoint. Recreated objects need incarnation-aware IDs where available.
- ResourceSnapshot records the environment, observation time, source, explicit filter scope and
  coverage. `totalCount` / `total_count` is independently known for that filter scope; never infer it
  from the current page length. Omit unknown totals. A complete empty result differs from unavailable.
- Measurements have a name, finite numeric value, unit and `observed` or `total` scope. A sample's
  memory footprint is not a whole-collection total. Different units/measurements are not additive.
- Instances retain domain state and typed details. State is not an implicit healthy/unhealthy verdict.
  A partial page can retain instances, a known total, and an opaque continuation cursor. Sources,
  cursors and details must be safe serializable evidence; never put credentials into a snapshot.

Consumers extend the declarations with their native read operations and own authorization,
pagination, cancellation, budgets, schema validation and presentation. Common does not define a
Doctor Extension kind, call a business API, render a table, or supply business lifecycle operations.
Workload describes a runtime carrier; DataSource supplies access to data. Resource does not replace
those models. A consumer can link inventory instances to existing WorkloadInstance identities.

```ts
import type { Resource, ResourceSnapshot, ServiceResource } from "@compforge/harness-common";

const beds: Resource = { id: "sandbox/beds", kind: "bed", name: "Beds" };
// Service identity and environment continue to use Common's existing model.
// const relation: ServiceResource = { service, resource: beds, relationship: "manages" };
const snapshot: ResourceSnapshot<{ pod: string }> = {
  resource: beds, environment: "test-cluster", observedAt: "2026-10-23T00:00:00Z",
  source: "bed-api", scope: { tenant: "example" },
  coverage: { status: "partial", reason: "page budget" }, totalCount: 17,
  instances: [{ ref: { environment: "test-cluster", resource: beds.id, id: "bed-1" },
    state: "retained", details: { pod: "carrier-1" } }], nextCursor: "next-page",
};
```
