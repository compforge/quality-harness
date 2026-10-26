package common

import "strconv"

// PullRequestState is the provider-neutral lifecycle; the zero value means unknown.
type PullRequestState string

const (
	PullRequestOpen   PullRequestState = "open"
	PullRequestMerged PullRequestState = "merged"
	PullRequestClosed PullRequestState = "closed"
)

// PullRequest is a GitHub PR or GitLab MR owned by its target Repository.
//
// +spec=`Identity is target Repository and repository-local number; source, revisions and state are metadata`
// +spec=`Omitted facts are unknown; a missing State or MergedAt never proves closure or merge`
// +link=component://docs/pull-request.md
type PullRequest struct {
	Repository Repository `json:"repository" yaml:"repository"`
	// Number is the positive repository-local PR number / MR iid, not a global database ID.
	Number           int              `json:"number" yaml:"number"`
	Title            string           `json:"title,omitempty" yaml:"title,omitempty"`
	URL              string           `json:"url,omitempty" yaml:"url,omitempty"`
	State            PullRequestState `json:"state,omitempty" yaml:"state,omitempty"`
	SourceRepository *Repository      `json:"source_repository,omitempty" yaml:"source_repository,omitempty"`
	SourceBranch     string           `json:"source_branch,omitempty" yaml:"source_branch,omitempty"`
	TargetBranch     string           `json:"target_branch,omitempty" yaml:"target_branch,omitempty"`
	HeadSHA          string           `json:"head_sha,omitempty" yaml:"head_sha,omitempty"`
	BaseSHA          string           `json:"base_sha,omitempty" yaml:"base_sha,omitempty"`
	// UpdatedAt and MergedAt are ISO 8601 provider timestamps.
	UpdatedAt string `json:"updated_at,omitempty" yaml:"updated_at,omitempty"`
	MergedAt  string `json:"merged_at,omitempty" yaml:"merged_at,omitempty"`
}

func (p PullRequest) ID() string {
	return p.Repository.ID() + "#" + strconv.Itoa(p.Number)
}

// SameIdentity excludes all mutable facts, including source repository and fork provenance.
func (p PullRequest) SameIdentity(other PullRequest) bool {
	return p.Repository.SameIdentity(other.Repository) && p.Number == other.Number
}
