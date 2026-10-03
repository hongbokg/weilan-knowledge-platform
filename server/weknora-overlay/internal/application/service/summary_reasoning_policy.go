package service

import (
	"github.com/Tencent/WeKnora/internal/models/chat"
	"github.com/Tencent/WeKnora/internal/types"
	"strings"
)

// GLM-5.3 cannot disable thinking. Its output budget includes reasoning;
// an old 1024-token summary cap can leave no visible answer at all.
// https://docs.bigmodel.cn/cn/guide/models/vlm/glm-5.3-flash
func summaryChatOptions(model string, configured int) *chat.ChatOptions {
	off := false
	opts := &chat.ChatOptions{Temperature: 0.3, MaxTokens: configured, Thinking: &off}
	switch strings.ToLower(strings.TrimSpace(model)) {
	case "glm-5.3", "glm-5.3-flash", "glm-5.3-flashx":
		opts.Thinking = nil
		opts.ReasoningEffort = chat.ReasoningEffort("low")
		if opts.MaxTokens < 4096 {
			opts.MaxTokens = 4096
		}
		if opts.MaxTokens > 8192 {
			opts.MaxTokens = 8192
		}
	}
	return opts
}

func summaryNeedsLargerBudget(response *types.ChatResponse, opts *chat.ChatOptions) bool {
	return response != nil && opts != nil && opts.ReasoningEffort == chat.ReasoningEffort("low") &&
		opts.MaxTokens < 8192 && response.FinishReason == "length" &&
		strings.TrimSpace(response.Content) == "" && strings.TrimSpace(response.ReasoningContent) != ""
}
