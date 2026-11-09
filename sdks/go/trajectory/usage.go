package trajectory

import (
	"context"
	"fmt"
	"time"

	"github.com/compforge/agent-ledger/go/common"
	"github.com/compforge/agent-ledger/go/observation"
)

func field(name, unit, description string, sum bool) MeasurementSpec {
	aggregations := []string{"mean", "p50", "p95"}
	if sum {
		aggregations = append([]string{"sum"}, aggregations...)
	}
	direction := "neutral"
	if name == "usage_coverage_ratio" {
		direction = "higher_is_better"
	}
	if name == "tool_failure_ratio" {
		direction = "lower_is_better"
	}
	return MeasurementSpec{Name: name, Unit: unit, Description: description, Direction: direction, Aggregations: aggregations}
}

type ModelUsageMeasurer struct{}

func (ModelUsageMeasurer) Spec() MeasurerSpec {
	return MeasurerSpec{ID: "model_usage", Version: "1", Title: "Model usage", Description: "Recorded model calls, usage and cumulative duration.", Category: "cost", Owner: "trajectory_harness", Measurements: []MeasurementSpec{
		field("model_duration_ms", "ms", "Summed recorded call intervals, not wall time.", true),
		field("duration_reported_call_count", "count", "Model calls with recorded duration, including zero.", true),
		field("model_call_count", "count", "Observed model calls.", true),
		field("usage_reported_call_count", "count", "Calls reporting at least one token usage field.", true),
		field("usage_coverage_ratio", "ratio", "Calls with reported usage divided by observed calls.", false),
		field("input_tokens", "token", "Reported input including cache reads and writes.", true),
		field("output_tokens", "token", "Reported output tokens.", true),
		field("output_reported_call_count", "count", "Calls reporting output tokens.", true),
		field("average_output_tokens_per_call", "token", "Mean output across covered calls.", false),
		field("peak_output_tokens_per_call", "token", "Largest reported output.", false),
		field("output_under_500_tokens_call_count", "count", "Covered calls with output below 500 tokens.", true),
		field("output_under_500_tokens_ratio", "ratio", "Covered calls under 500 tokens divided by covered calls.", false),
		field("total_tokens", "token", "Sum of reported input and output tokens.", true),
		field("average_input_tokens_per_call", "token", "Mean input across covered calls.", false),
		field("peak_input_tokens_per_call", "token", "Largest reported input.", false),
		field("cached_input_tokens", "token", "Reported cache reads, a subset of input tokens.", true),
		field("cache_write_input_tokens", "token", "Reported cache writes, a subset of input tokens.", true),
		field("uncached_input_tokens", "token", "Input minus cache reads for calls with paired fields.", true),
		field("cache_hit_ratio", "ratio", "Cache reads divided by input for calls with paired fields.", false),
	}}
}

func (ModelUsageMeasurer) Measure(ctx context.Context, session observation.SessionView) (MeasurementResult, error) {
	result := MeasurementResult{MeasurerID: "model_usage", Status: "not_applicable", Measurements: map[string]float64{}}
	calls, err := callNodes(ctx, session)
	if err != nil {
		return result, err
	}
	values := result.Measurements
	input, output, cached, written := []float64{}, []float64{}, []float64{}, []float64{}
	var count, usage, durations, totalDuration, pairedInput, pairedCache float64
	for _, node := range calls {
		call, ok := node.(*observation.ModelCall)
		if !ok {
			continue
		}
		count++
		result.Evidence = append(result.Evidence, evidence(call.Node, session.Session))
		if duration, ok := recordedDuration(call.Node); ok {
			durations++
			totalDuration += duration
		}
		if call.Response == nil || call.Response.Usage == nil {
			continue
		}
		usageValue := *call.Response.Usage
		u := &usageValue
		for _, field := range []**int64{&u.InputTokens, &u.OutputTokens, &u.CacheReadInputTokens, &u.CacheWriteInputTokens} {
			if *field != nil && **field < 0 {
				*field = nil
			}
		}
		if u.InputTokens != nil || u.OutputTokens != nil || u.CacheReadInputTokens != nil {
			usage++
		}
		var callInput float64
		if u.InputTokens != nil {
			callInput = float64(*u.InputTokens)
			if u.CacheWriteInputTokens != nil {
				callInput += float64(*u.CacheWriteInputTokens)
			}
			input = append(input, callInput)
		}
		if u.OutputTokens != nil {
			output = append(output, float64(*u.OutputTokens))
		}
		if u.CacheReadInputTokens != nil {
			cached = append(cached, float64(*u.CacheReadInputTokens))
			if u.InputTokens != nil && float64(*u.CacheReadInputTokens) <= callInput {
				pairedInput += callInput
				pairedCache += float64(*u.CacheReadInputTokens)
			}
		}
		if u.CacheWriteInputTokens != nil {
			written = append(written, float64(*u.CacheWriteInputTokens))
		}
	}
	if count == 0 {
		result.Explanation = "Session contains no model calls."
		return result, nil
	}
	result.Status = "measured"
	values["model_call_count"] = count
	values["usage_reported_call_count"] = usage
	values["usage_coverage_ratio"] = round(usage / count)
	values["duration_reported_call_count"] = durations
	if durations > 0 {
		values["model_duration_ms"] = round(totalDuration)
	}
	if len(input) > 0 {
		values["input_tokens"] = sum(input)
		values["average_input_tokens_per_call"] = round(sum(input) / float64(len(input)))
		values["peak_input_tokens_per_call"] = peak(input)
	}
	if len(output) > 0 {
		values["output_tokens"] = sum(output)
		values["output_reported_call_count"] = float64(len(output))
		values["average_output_tokens_per_call"] = round(sum(output) / float64(len(output)))
		values["peak_output_tokens_per_call"] = peak(output)
		under := 0.0
		for _, n := range output {
			if n < 500 {
				under++
			}
		}
		values["output_under_500_tokens_call_count"] = under
		values["output_under_500_tokens_ratio"] = round(under / float64(len(output)))
	}
	if len(input)+len(output) > 0 {
		values["total_tokens"] = sum(input) + sum(output)
	}
	if len(cached) > 0 {
		values["cached_input_tokens"] = sum(cached)
	}
	if len(written) > 0 {
		values["cache_write_input_tokens"] = sum(written)
	}
	if pairedInput > 0 {
		values["uncached_input_tokens"] = pairedInput - pairedCache
		values["cache_hit_ratio"] = round(pairedCache / pairedInput)
	}
	result.Explanation = fmt.Sprintf("Observed %.0f model calls; %.0f reported token usage.", count, usage)
	return result, nil
}

type ToolUsageMeasurer struct{}

func (ToolUsageMeasurer) Spec() MeasurerSpec {
	return MeasurerSpec{ID: "tool_usage", Version: "1", Title: "Tool usage", Description: "Native tool execution counts, explicit failures and duration.", Category: "cost", Owner: "trajectory_harness", Measurements: []MeasurementSpec{
		field("tool_call_count", "count", "Observed native tool calls, including incomplete attempts.", true),
		field("failed_tool_call_count", "count", "Calls with explicit failure status or response error.", true),
		field("status_reported_call_count", "count", "Calls with explicit completed or failed outcomes.", true),
		field("tool_failure_ratio", "ratio", "Explicit failures divided by all observed tool calls; inspect status coverage.", false),
		field("tool_duration_ms", "ms", "Summed recorded tool intervals, not wall time.", true),
		field("duration_reported_call_count", "count", "Calls with recorded duration, including zero.", true),
	}}
}
func (ToolUsageMeasurer) Measure(ctx context.Context, session observation.SessionView) (MeasurementResult, error) {
	result := MeasurementResult{MeasurerID: "tool_usage", Status: "not_applicable", Measurements: map[string]float64{}}
	nodes, err := callNodes(ctx, session)
	if err != nil {
		return result, err
	}
	var count, failed, known, durations, duration float64
	for _, node := range nodes {
		call, ok := node.(*observation.ToolCall)
		if !ok {
			continue
		}
		count++
		result.Evidence = append(result.Evidence, evidence(call.Node, session.Session))
		if call.Status == "failed" || call.Response != nil && call.Response.Error != nil {
			failed++
			known++
		} else if call.Status == "completed" {
			known++
		}
		if value, ok := recordedDuration(call.Node); ok {
			durations++
			duration += value
		}
	}
	if count == 0 {
		result.Explanation = "Session contains no tool calls."
		return result, nil
	}
	result.Status = "measured"
	result.Measurements = map[string]float64{"tool_call_count": count, "failed_tool_call_count": failed, "status_reported_call_count": known, "tool_failure_ratio": round(failed / count), "duration_reported_call_count": durations}
	if durations > 0 {
		result.Measurements["tool_duration_ms"] = round(duration)
	}
	result.Explanation = fmt.Sprintf("Observed %.0f tool calls; %.0f reported status.", count, known)
	return result, nil
}

// Traverse containment only; ToolCall.Sessions are references and never expand linked Sessions.
func callNodes(ctx context.Context, view observation.SessionView) (observation.Nodes, error) {
	result := observation.Nodes{}
	var walk func(observation.Nodes) error
	walk = func(nodes observation.Nodes) error {
		for _, node := range nodes {
			if err := ctx.Err(); err != nil {
				return err
			}
			if owner := node.Base().Session; owner != nil && *owner != view.Session {
				continue
			}
			switch n := node.(type) {
			case *observation.ModelCall, *observation.ToolCall:
				result = append(result, node)
			case *observation.Turn:
				if err := walk(n.Nodes); err != nil {
					return err
				}
			case *observation.Operation:
				if err := walk(n.Nodes); err != nil {
					return err
				}
			}
		}
		return nil
	}
	err := walk(view.Nodes)
	return result, err
}
func recordedDuration(node observation.Node) (float64, bool) {
	start, e1 := time.Parse(time.RFC3339Nano, node.StartedAt)
	end, e2 := time.Parse(time.RFC3339Nano, node.FinishedAt)
	if e1 != nil || e2 != nil || end.Before(start) {
		return 0, false
	}
	return end.Sub(start).Seconds() * 1000, true
}
func evidence(node observation.Node, session common.SessionRef) observation.SourceRef {
	return observation.SourceRef{Source: session.Source, ID: node.ID, URI: session.ID}
}
func sum(values []float64) float64 {
	n := 0.0
	for _, v := range values {
		n += v
	}
	return n
}
func peak(values []float64) float64 {
	n := values[0]
	for _, v := range values {
		if v > n {
			n = v
		}
	}
	return n
}
