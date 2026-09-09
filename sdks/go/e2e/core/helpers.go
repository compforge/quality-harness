package core

import (
	"fmt"
	"os"
	"sync/atomic"
)

var seq atomic.Int64

// UniqueID generates a unique identifier for test isolation.
func UniqueID(prefix string) string {
	return fmt.Sprintf("%s%d-%d", prefix, os.Getpid(), seq.Add(1))
}
