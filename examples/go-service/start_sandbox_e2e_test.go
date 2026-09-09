//go:build e2e

package sandbox_e2e

import (
	"context"
	"fmt"
	"strconv"
	"testing"
	"time"

	"github.com/compforge/quality-harness/sdks/go/e2e/caserun"
	"github.com/compforge/quality-harness/sdks/go/e2e/core"
	"github.com/compforge/quality-harness/sdks/go/e2e/judge"
	"github.com/compforge/quality-harness/sdks/go/e2e/runner"
)

var budgets = caserun.Budgets{
	Prepare: 30 * time.Second,
	Execute: 2 * time.Minute,
	Judge:   time.Minute,
	Cleanup: time.Minute,
}

type startState struct {
	conversation string
	request      runner.Request
	first        *runner.Outcome
	second       *runner.Outcome
}

func TestStartSandboxHappyPath(t *testing.T) {
	config, err := core.LoadConfig("config.yaml")
	if err != nil {
		t.Fatal(err)
	}
	core.RequireProfile(t, config, "full", "minimal")
	r := runner.NewJSONRunner(config)
	state := startState{}

	result := caserun.Run(
		context.Background(),
		caserun.Ref("sandbox-runtime", "happy_minimal"),
		nil,
		&state,
		caserun.Definition[startState]{
			Prepare: func(_ context.Context, state *startState) error {
				ttl, err := strconv.ParseInt(config.Custom["ttl_seconds"], 10, 64)
				if err != nil {
					return fmt.Errorf("parse ttl_seconds: %w", err)
				}
				state.conversation = core.UniqueID(config.Custom["conv_prefix"])
				state.request = runner.Request{
					Method: "POST",
					Path:   "/api/v1/sandboxes/start",
					Body: map[string]any{
						"conversation_id": state.conversation,
						"ttl_seconds":     ttl,
					},
				}
				return nil
			},
			Execute: func(ctx context.Context, state *startState) error {
				state.first, err = r.Trigger(ctx, state.request)
				return err
			},
			Judge: func(_ context.Context, state *startState) error {
				if err := judge.Assert(
					state.first,
					judge.Status(200),
					judge.FieldNotEmpty("sandbox_name"),
					judge.FieldEq("conversation_id", state.conversation),
					judge.NoError(),
				); err != nil {
					return caserun.Fail(err.Error())
				}
				return nil
			},
			Cleanup: stopSandbox(r),
			Budgets: budgets,
		},
	)
	sandboxRun.Assert(t, result)
}

func TestStartSandboxReuse(t *testing.T) {
	config, err := core.LoadConfig("config.yaml")
	if err != nil {
		t.Fatal(err)
	}
	r := runner.NewJSONRunner(config)
	state := startState{}
	result := caserun.Run(
		context.Background(),
		caserun.Ref("sandbox-runtime", "reuse"),
		nil,
		&state,
		caserun.Definition[startState]{
			Prepare: func(_ context.Context, state *startState) error {
				state.conversation = core.UniqueID(config.Custom["conv_prefix"])
				state.request = runner.Request{
					Method: "POST",
					Path:   "/api/v1/sandboxes/start",
					Body:   map[string]any{"conversation_id": state.conversation},
				}
				return nil
			},
			Execute: func(ctx context.Context, state *startState) error {
				state.first, err = r.Trigger(ctx, state.request)
				if err != nil {
					return err
				}
				state.second, err = r.Trigger(ctx, state.request)
				return err
			},
			Judge: func(_ context.Context, state *startState) error {
				if err := judge.Assert(
					state.second,
					judge.Status(200),
					judge.FieldEq("sandbox_name", state.first.FieldStr("sandbox_name")),
				); err != nil {
					return caserun.Fail(err.Error())
				}
				return nil
			},
			Cleanup: stopSandbox(r),
			Budgets: budgets,
		},
	)
	sandboxRun.Assert(t, result)
}

func stopSandbox(r *runner.JSONRunner) caserun.Step[startState] {
	return func(ctx context.Context, state *startState) error {
		if state.conversation == "" {
			return nil
		}
		_, err := r.Trigger(ctx, runner.Request{
			Method: "POST",
			Path:   "/api/v1/sandboxes/stop",
			Body:   map[string]any{"conversation_id": state.conversation},
		})
		return err
	}
}
