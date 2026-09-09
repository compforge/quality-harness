package core

// Ptr returns a pointer to v. Test request bodies use *T fields to distinguish
// "unset" (nil) from "explicitly zero" (e.g. ttl_seconds=0); Ptr keeps those call
// sites a one-liner without a per-type helper.
func Ptr[T any](v T) *T {
	return &v
}
