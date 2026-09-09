// Package runner defines the Runner interface and Outcome data structure.
package runner

import (
	"context"
	"encoding/json"
)

// Request represents what to send to the service.
type Request struct {
	Method         string
	Path           string
	Body           any
	Headers        map[string]string
	Query          map[string]string
	ExcludeHeaders []string
}

// Outcome is the standardized runner output — the contract between Runner and Judge.
type Outcome struct {
	StatusCode int
	Body       json.RawMessage
	Headers    map[string]string
	DurationMS int64
	Metadata   map[string]any
	Raw        []byte
}

// Field accesses a dot-path field in the parsed body JSON.
func (o *Outcome) Field(path string) any {
	if o.Body == nil {
		return nil
	}
	var parsed any
	if err := json.Unmarshal(o.Body, &parsed); err != nil {
		return nil
	}
	return dotPath(parsed, path)
}

// FieldStr returns the string representation of a dot-path field.
func (o *Outcome) FieldStr(path string) string {
	v := o.Field(path)
	if v == nil {
		return ""
	}
	switch s := v.(type) {
	case string:
		return s
	default:
		b, _ := json.Marshal(v)
		return string(b)
	}
}

// BodyMap returns the body parsed as map[string]any, or nil on failure.
func (o *Outcome) BodyMap() map[string]any {
	if o.Body == nil {
		return nil
	}
	var m map[string]any
	if err := json.Unmarshal(o.Body, &m); err != nil {
		return nil
	}
	return m
}

// Decode unmarshals the JSON body into v. It lets a black-box runner feed typed,
// service-specific response structs without each caller re-running json.Unmarshal;
// the biz layer keeps its own types and decodes Outcome into them.
func (o *Outcome) Decode(v any) error {
	return json.Unmarshal(o.Body, v)
}

// Runner is the protocol adapter interface.
type Runner interface {
	Trigger(ctx context.Context, req Request) (*Outcome, error)
}

func dotPath(obj any, path string) any {
	parts := splitDotPath(path)
	current := obj
	for _, part := range parts {
		if current == nil {
			return nil
		}
		switch m := current.(type) {
		case map[string]any:
			current = m[part]
		case []any:
			idx := 0
			for i := range part {
				if part[i] < '0' || part[i] > '9' {
					return nil
				}
				idx = idx*10 + int(part[i]-'0')
			}
			if idx >= len(m) {
				return nil
			}
			current = m[idx]
		default:
			return nil
		}
	}
	return current
}

func splitDotPath(path string) []string {
	if path == "" {
		return nil
	}
	var parts []string
	start := 0
	for i := range path {
		if path[i] == '.' {
			parts = append(parts, path[start:i])
			start = i + 1
		}
	}
	parts = append(parts, path[start:])
	return parts
}
