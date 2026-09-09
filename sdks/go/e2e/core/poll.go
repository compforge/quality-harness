package core

import (
	"context"
	"fmt"
	"time"
)

// Poll calls check until it reports done, returns an error, or ctx expires.
func Poll(ctx context.Context, interval time.Duration, check func(context.Context) (bool, error)) error {
	if interval <= 0 {
		return fmt.Errorf("poll interval must be positive")
	}
	for {
		done, err := check(ctx)
		if err != nil {
			return err
		}
		if done {
			return nil
		}
		if err := waitInterval(ctx, interval); err != nil {
			return fmt.Errorf("poll: %w", err)
		}
	}
}

// Retry calls fn until it succeeds (err == nil) or ctx expires.
//
// fn returns (retryable, err): a non-retryable error fails the test immediately;
// a retryable error is retried after interval. This is the "poll until stable,
// retry only on known-transient" pattern — e.g. a second create that briefly
// returns a volume-detach-pending code — without baking any specific error code
// into the harness; the caller decides what counts as retryable.
func Retry(ctx context.Context, interval time.Duration, fn func(context.Context) (retryable bool, err error)) error {
	if interval <= 0 {
		return fmt.Errorf("retry interval must be positive")
	}
	for {
		retryable, err := fn(ctx)
		if err == nil {
			return nil
		}
		if !retryable {
			return fmt.Errorf("retry: non-retryable: %w", err)
		}
		if waitErr := waitInterval(ctx, interval); waitErr != nil {
			return fmt.Errorf("retry: %w; last error: %v", waitErr, err)
		}
	}
}

// Consistently verifies check remains true for duration. The observation
// window must fit inside the caller's phase deadline so a successful temporal
// assertion does not consume the phase budget itself.
func Consistently(ctx context.Context, duration, interval time.Duration, check func(context.Context) (bool, error)) error {
	if duration <= 0 {
		return fmt.Errorf("consistent duration must be positive")
	}
	if interval <= 0 {
		return fmt.Errorf("consistent interval must be positive")
	}
	if deadline, ok := ctx.Deadline(); ok && time.Until(deadline) <= duration {
		return fmt.Errorf("consistent duration must fit within context deadline")
	}
	timer := time.NewTimer(duration)
	defer timer.Stop()
	for {
		ok, err := check(ctx)
		if err != nil {
			return err
		}
		if !ok {
			return fmt.Errorf("condition became false")
		}
		wait := time.NewTimer(interval)
		select {
		case <-ctx.Done():
			wait.Stop()
			return ctx.Err()
		case <-timer.C:
			wait.Stop()
			return nil
		case <-wait.C:
		}
	}
}

func waitInterval(ctx context.Context, interval time.Duration) error {
	timer := time.NewTimer(interval)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}
