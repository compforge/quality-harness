package common

// Tool is a development tool declared for a Component, such as pnpm, uv or Vite.
//
// +spec=`A declaration does not prove installation or select an executable`
type Tool struct {
	Name string `json:"name" yaml:"name"`
	// Version is a declared version or constraint, not an observed installed version.
	Version string `json:"version,omitempty" yaml:"version,omitempty"`
}

// Bin is a recoverable content unit within a Depot, usually one directory.
//
// +spec=`Path is a normalized repository-relative POSIX path, never checkout-absolute`
// +rule=`A declaration alone does not authorize deletion or prove exclusive ownership`
// +link=component://docs/component-depot.md
type Bin struct {
	Path string `json:"path" yaml:"path"`
	// Kind is dependencies, output or cache. It does not determine cleanup eligibility.
	Kind string `json:"kind" yaml:"kind"`
}

// Depot is the optional logical container for a Component's recoverable supporting content.
//
// +spec=`A Component has at most one Depot per checkout; missing contents do not remove its declaration`
// +spec=`Consumers own discovery, preparation, usage protection and reclamation`
// +link=component://docs/component-depot.md
type Depot struct {
	Bins []Bin `json:"bins" yaml:"bins"`
}
