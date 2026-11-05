# Component tools and Depot

A Component may declare the development tools it uses and one logical Depot of recoverable
supporting content. These are optional metadata alongside language and description; Component
identity remains Repository plus name. Common describes the contract without inspecting a checkout.

```text
Component: web
  language: typescript
  tools: [{name: pnpm, version: ">=9 <10"}, {name: vite}]
  depot:
    bins:
      - {path: node_modules, kind: dependencies}
      - {path: apps/web/dist, kind: output}
      - {path: apps/web/.vite, kind: cache}
```

## Declarations

- **Tool** names a development tool, including package managers and build tools. Its optional
  version is a declared version or constraint, not the version installed on a machine. It does
  not select a command or executable; discovery evidence and conflict resolution belong to consumers.
- **Depot** groups a Component's recoverable supporting content. Within a selected checkout a
  Component has zero or one logical Depot; the same declaration can be resolved in several checkouts.
  Removing physical contents does not remove the declaration. An empty `bins` list is valid.
- **Bin** describes one content unit, normally a directory, with `path` and `kind`.
  `dependencies`, `output` and `cache` describe purpose, not current existence, readiness or
  cleanup eligibility. One preparation operation may restore multiple bins.

Paths are normalized POSIX paths relative to the repository checkout root, without a leading slash,
trailing slash, empty segment, `.` or `..` segment. They are literal locations, not globs. A workspace
installation may be outside the Component's own directory while remaining within its checkout.
A Bin path is unique within its Depot; distinct Components may declare the same path. Shared and
nested physical locations require consumer-owned deduplication and coordinated usage protection.
Global caches outside the checkout are not represented by this contract.

Omitted `tools` means undeclared; an empty list declares no tools. Omitted `depot` means no Depot
was declared, not proof that the checkout contains nothing recoverable. Consumers report discovery
coverage separately. The declarations are trusted typed data, not a filesystem validation API.
Consumers must validate external input and resolve filesystem containment, including symlinks,
before executing any operation.

## Ownership and lifecycle

Common supplies Tool, Depot and Bin declarations and Component associations. It performs no I/O
and has no installation, build or deletion methods. RepoKit or another consumer owns discovery,
absolute path resolution, restoration recipes, installed versions, size/status observations,
locks, active usage and reclamation. Tool or language names alone do not prove a recovery recipe.

A Bin declaration is not deletion authorization. Consumers check tracked files, recovery evidence,
active use and shared locations before reclamation. The selected checkout disambiguates identical
relative paths in different worktrees. Reclaiming one logical Depot must not invalidate another
active consumer of a shared directory.

Validation consumers select the bins that a check reads. Dependency deletion or relevant content
changes can invalidate reuse, while output/cache writes performed by the check itself must not
create an unconditional invalidation loop. Bin kind alone does not determine input/output roles.

## Language APIs and compatibility

Public names are `Tool`, `Depot` and `Bin`; Component fields are `tools` and `depot` (Go: `Tools`,
`Depot`). TypeScript uses readonly interfaces. Python uses frozen dataclasses, optional tuples
and keyword-only Component metadata, excluded from equality and hashing.

Go uses `Tools *[]Tool` and `Depot *Depot`. A nil Tools pointer means undeclared; a pointer to
an empty non-nil slice serializes as `[]`. Depot's Bins is a required slice; use an empty non-nil
slice when explicitly declaring an empty Depot. The pointers preserve Component comparability,
but `==` and map keys compare all fields, including metadata pointer addresses. Use `SameIdentity`
for identity comparisons; identity indexes must use Repository's forge/path plus Component name.

Legacy callers need not supply the new fields. JSON omits undeclared metadata; the shared
`conformance/common/component-depots.json` fixture covers legacy, Node/Python, shared-location
and empty declarations in all three SDKs. Existing source identity fixtures remain unchanged.
