// Package burst provides parallel request utilities for e2e tests.
package burst

import (
	"sync"
	"time"
)

// Result holds one parallel invocation outcome.
type Result[T any] struct {
	Index   int
	Value   T
	Err     error
	Elapsed time.Duration
}

// Run fires n goroutines, each calling fn(i), and returns all results in order.
func Run[T any](n int, fn func(i int) (T, error)) []Result[T] {
	results := make([]Result[T], n)
	var wg sync.WaitGroup
	wg.Add(n)
	for i := 0; i < n; i++ {
		go func(idx int) {
			defer wg.Done()
			start := time.Now()
			val, err := fn(idx)
			results[idx] = Result[T]{
				Index:   idx,
				Value:   val,
				Err:     err,
				Elapsed: time.Since(start),
			}
		}(i)
	}
	wg.Wait()
	return results
}
