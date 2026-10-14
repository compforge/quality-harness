package common

// Identity exposes a string identifier whose format and uniqueness scope belong to the model.
//
// +spec=`The same business identity yields the same identifier; descriptive metadata is excluded`
type Identity interface {
	ID() string
}
