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
// Repository. Directory layout and implementation language are consumer facts.
type Component struct {
	Repository  Repository `json:"repository" yaml:"repository"`
	Name        string     `json:"name" yaml:"name"`
	Description string     `json:"description,omitempty" yaml:"description,omitempty"`
}

// SameIdentity compares stable identity, excluding human-readable metadata.
// Use it instead of struct equality when descriptions may differ.
func (c Component) SameIdentity(other Component) bool {
	return c.Repository == other.Repository && c.Name == other.Name
}
