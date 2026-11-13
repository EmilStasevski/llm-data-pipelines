package main

import (
	"context"
	"time"
)

// contextWithTimeout is a fresh context for shutdown (the signal context is already canceled).
func contextWithTimeout(d time.Duration) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.Background(), d)
}
