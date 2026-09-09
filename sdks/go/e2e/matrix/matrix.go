// Package matrix expands environment-specific execution variants without
// putting those variants into canonical case assets.
package matrix

import (
	"fmt"
	"sort"
	"strings"
)

type Variant map[string]string

func (v Variant) ID() string {
	keys := make([]string, 0, len(v))
	for key := range v {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, key := range keys {
		parts = append(parts, key+"="+v[key])
	}
	return strings.Join(parts, ",")
}

func (v Variant) Facets() map[string]string {
	out := make(map[string]string, len(v))
	for key, value := range v {
		out[key] = value
	}
	return out
}

// Expand returns the deterministic cartesian product of named axes.
func Expand(axes map[string][]string) ([]Variant, error) {
	keys := make([]string, 0, len(axes))
	for key, values := range axes {
		if strings.TrimSpace(key) == "" {
			return nil, fmt.Errorf("matrix axis name must not be empty")
		}
		if len(values) == 0 {
			return nil, fmt.Errorf("matrix axis %q has no values", key)
		}
		seen := map[string]bool{}
		for _, value := range values {
			if strings.TrimSpace(value) == "" {
				return nil, fmt.Errorf("matrix axis %q contains an empty value", key)
			}
			if seen[value] {
				return nil, fmt.Errorf("matrix axis %q contains duplicate value %q", key, value)
			}
			seen[value] = true
		}
		keys = append(keys, key)
	}
	sort.Strings(keys)
	variants := []Variant{{}}
	for _, key := range keys {
		var next []Variant
		for _, variant := range variants {
			for _, value := range axes[key] {
				item := variant.Facets()
				item[key] = value
				next = append(next, item)
			}
		}
		variants = next
	}
	return variants, nil
}
