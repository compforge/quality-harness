package core

import (
	"fmt"
	"os"
	"regexp"
	"strings"
)

var idUnsafe = regexp.MustCompile(`[^a-zA-Z0-9]+`)

// UniqueIDFor builds a collision-free identifier that also encodes the test name,
// so concurrent tests get visibly distinct resources in logs and on the server.
// Format: "<prefix><sanitized-test>-<pid>-<seq>". The seq counter is shared with
// UniqueID, so the two never collide.
//
// Conversation, tenant, and user IDs are just different prefixes — quota and
// isolation cases that must not share state across tests pass a per-test base as
// the prefix (e.g. a fresh tenant per test) rather than reusing the config default.
func UniqueIDFor(prefix, testName string) string {
	clean := strings.Trim(idUnsafe.ReplaceAllString(testName, "-"), "-")
	return fmt.Sprintf("%s%s-%d-%d", prefix, clean, os.Getpid(), seq.Add(1))
}
