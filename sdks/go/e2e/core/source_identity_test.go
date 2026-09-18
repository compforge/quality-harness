package core_test

import (
	"testing"

	"github.com/compforge/quality-harness/sdks/go/common"
	"github.com/compforge/quality-harness/sdks/go/e2e/core"
)

func TestServiceAcceptsCommonSourceIdentity(t *testing.T) {
	component := common.Component{
		Repository: common.Repository{Forge: common.Forge{Name: "github"}, Path: "example/repo"},
		Name:       "server",
	}
	service := core.Service{Component: component}
	var legacy core.Component = component
	if service.Component != legacy {
		t.Fatal("E2E changed the common component identity")
	}
}
