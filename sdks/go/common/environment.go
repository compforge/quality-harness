// Package common owns neutral identities and evidence shared by harnesses.
package common

import (
	"fmt"
)

// Environment identifies a deployment environment independently of the runner.
// Host is optional; nil means operations execute on the current host.
// For Kubernetes, Kubeconfig is interpreted on Host, not on the case runner.
type Environment struct {
	Name       string `json:"name" yaml:"name"`
	Kind       string `json:"kind,omitempty" yaml:"kind,omitempty"`
	Host       *Host  `json:"host,omitempty" yaml:"host,omitempty"`
	Kubeconfig string `json:"kubeconfig,omitempty" yaml:"kubeconfig,omitempty"`
	Context    string `json:"context,omitempty" yaml:"context,omitempty"`
}

func (e Environment) ResolvedKind() string {
	if e.Kind != "" {
		return e.Kind
	}
	if e.Kubeconfig != "" || e.Context != "" {
		return "kubernetes"
	}
	return "generic"
}

func (e Environment) Validate() error {
	if e.Host != nil {
		if err := e.Host.Validate(); err != nil {
			return err
		}
	}
	switch e.ResolvedKind() {
	case "generic", "host":
		if e.Kubeconfig != "" || e.Context != "" {
			return fmt.Errorf("%s environment contains Kubernetes access fields", e.ResolvedKind())
		}
	case "kubernetes":
	default:
		return fmt.Errorf("unknown environment kind %q", e.Kind)
	}
	return nil
}

// EnvironmentFacts are observations. An absent key means unknown, not false.
type EnvironmentFacts struct {
	Source     string            `json:"source"`
	ObservedAt string            `json:"observed_at"`
	Values     map[string]string `json:"values"`
}

// EnvironmentSnapshot records realized conditions without connection credentials.
// Project fixtures populate Target from the target, never from the local runner.
type EnvironmentSnapshot struct {
	Name     string            `json:"name"`
	Kind     string            `json:"kind"`
	Profile  string            `json:"profile"`
	Revision string            `json:"revision"`
	HostName string            `json:"host_name,omitempty"`
	Host     *EnvironmentFacts `json:"host,omitempty"`
	Runner   EnvironmentFacts  `json:"runner"`
	Target   EnvironmentFacts  `json:"target"`
}
