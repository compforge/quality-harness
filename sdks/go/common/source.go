package common

// Forge identifies a system hosting source-code repositories.
type Forge struct {
	Name string `json:"name" yaml:"name"`
}

// Repository is identified by its forge and its path within that forge.
// A checkout directory is location information, not repository identity.
//
// +spec=`ForkedFrom is the direct parent path within the same Forge instance and does not participate in repository identity`
// +link=component://docs/kernel.md
type Repository struct {
	Forge Forge  `json:"forge" yaml:"forge"`
	Path  string `json:"path" yaml:"path"`
	// ForkedFrom is the direct parent path within this same Forge instance; empty is unspecified.
	// Cross-Forge import/mirror provenance belongs in separate metadata.
	ForkedFrom string `json:"forked_from,omitempty" yaml:"forked_from,omitempty"`
}

// SameIdentity compares forge and path, excluding fork provenance.
// Use it instead of struct equality when repository metadata may differ.
func (r Repository) SameIdentity(other Repository) bool {
	return r.Forge == other.Forge && r.Path == other.Path
}

// Product is a business identity independent of repository layout. A registry
// owns its many-to-many relationship with Components.
//
// +spec=`Description, concepts and workflows are optional discovery metadata, not identity or runtime evidence`
// +link=component://docs/kernel.md
type Product struct {
	Name string `json:"name" yaml:"name"`
	// Description is an optional short introduction, not part of Product identity.
	Description string `json:"description,omitempty" yaml:"description,omitempty"`
	// Concepts is Markdown describing domain objects, relationships and constraints.
	Concepts string `json:"concepts,omitempty" yaml:"concepts,omitempty"`
	// Workflows is Markdown describing cross-service flows, not executable plans.
	Workflows string `json:"workflows,omitempty" yaml:"workflows,omitempty"`
}

// SameIdentity compares product names, excluding descriptive metadata.
// Use it instead of struct equality when product metadata may differ.
func (p Product) SameIdentity(other Product) bool {
	return p.Name == other.Name
}

// Component is a named, independently buildable or releasable unit in a
// Repository. Consumers may supply language and description metadata; directory
// layout and discovery remain consumer responsibilities.
type Component struct {
	Repository  Repository `json:"repository" yaml:"repository"`
	Name        string     `json:"name" yaml:"name"`
	Description string     `json:"description,omitempty" yaml:"description,omitempty"`
	// Language is optional implementation metadata; empty means unknown.
	Language string `json:"language,omitempty" yaml:"language,omitempty"`
}

// SameIdentity compares stable identity, excluding descriptive metadata.
// Use it instead of struct equality when component metadata may differ.
func (c Component) SameIdentity(other Component) bool {
	return c.Repository.SameIdentity(other.Repository) && c.Name == other.Name
}

// Ecosystem derives the tool ecosystem from Language without storing a second
// source of truth. An empty result means no known, unambiguous mapping.
// Package manager detection and execution belong to consumers.
func (c Component) Ecosystem() string {
	switch c.Language {
	case "python", "go":
		return c.Language
	case "javascript", "typescript":
		return "node"
	default:
		return ""
	}
}
