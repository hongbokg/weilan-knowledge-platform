//go:build !linux

package chat

func wrapCost(c Chat, remote bool, err error) (Chat, error) { return c, err }
