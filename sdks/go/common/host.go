package common

import (
	"fmt"
	"strings"
)

// Host is an optional part of an Environment. For Kubernetes it identifies the
// machine providing cluster access, not the node hosting a Pod. Address uses SSH
// configuration (alias or user@host); credentials remain outside the model.
type Host struct {
	Name      string `json:"name" yaml:"name"`
	Transport string `json:"transport,omitempty" yaml:"transport,omitempty"`
	Address   string `json:"address,omitempty" yaml:"address,omitempty"`
}

func (h Host) ResolvedTransport() string {
	if h.Transport == "" {
		return "local"
	}
	return h.Transport
}

func (h Host) Validate() error {
	if h.Name == "" {
		return fmt.Errorf("host requires name")
	}
	switch h.ResolvedTransport() {
	case "local":
		if h.Address != "" {
			return fmt.Errorf("local host cannot specify SSH address")
		}
	case "ssh":
		if h.Address == "" || strings.HasPrefix(h.Address, "-") {
			return fmt.Errorf("SSH host requires a non-option address")
		}
	default:
		return fmt.Errorf("unknown host transport %q", h.Transport)
	}
	return nil
}
