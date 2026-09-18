package common

// Forge identifies a system hosting source-code repositories.
type Forge struct {
	Name string `json:"name" yaml:"name"`
}

// Repository is identified by its forge and its path within that forge.
// A checkout directory is location information, not repository identity.
type Repository struct {
	Forge Forge  `json:"forge" yaml:"forge"`
	Path  string `json:"path" yaml:"path"`
}

// Product is a business identity independent of repository layout. A registry
// owns its many-to-many relationship with Components.
type Product struct {
	Name string `json:"name" yaml:"name"`
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
	return c.Repository == other.Repository && c.Name == other.Name
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
