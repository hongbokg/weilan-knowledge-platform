package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"sort"
	"strings"
	"time"

	werrors "github.com/Tencent/WeKnora/internal/errors"
	"github.com/Tencent/WeKnora/internal/types"
	"github.com/google/uuid"
)

// commitExternalSummary uses the normal authorized knowledge write path and
// embedding engine. The fingerprint binds the result to enabled source chunks.
// A failed index leaves the knowledge status incomplete, allowing idempotent retry.
func (s *knowledgeService) commitExternalSummary(ctx context.Context, k *types.Knowledge, expected string) error {
	scope, ok := types.TenantAPIKeyScopeFromContext(ctx)
	if !ok || scope.FullAccess || scope.IsPlatform() || len(scope.KnowledgeBaseIDs) != 1 || !scope.AllowsKnowledgeBase(k.KnowledgeBaseID) {
		return werrors.NewBadRequestError("external summary commit requires an explicit single-KB limited API key")
	}
	if k.ParseStatus != types.ParseStatusCompleted || len(strings.TrimSpace(k.Description)) < 30 || len(k.Description) > 12000 {
		return werrors.NewBadRequestError("external summary requires a completed source and 30..12000 bytes")
	}
	chunks, err := s.chunkRepo.ListChunksByKnowledgeIDAndTypes(ctx, k.TenantID, k.ID, []types.ChunkType{types.ChunkTypeText})
	if err != nil {
		return err
	}
	sort.Slice(chunks, func(i, j int) bool { return chunks[i].ID < chunks[j].ID })
	hash := sha256.New()
	var parent string
	maxIndex := 0
	for _, c := range chunks {
		if !c.IsEnabled {
			continue
		}
		hash.Write([]byte(c.ID + "\x00" + c.Content + "\x00"))
		if parent == "" {
			parent = c.ID
		}
		if c.ChunkIndex > maxIndex {
			maxIndex = c.ChunkIndex
		}
	}
	if parent == "" || hex.EncodeToString(hash.Sum(nil)) != expected {
		return werrors.NewBadRequestError("source changed; discard external summary")
	}
	summaries, err := s.chunkRepo.ListChunksByKnowledgeIDAndTypes(ctx, k.TenantID, k.ID, []types.ChunkType{types.ChunkTypeSummary})
	if err != nil {
		return err
	}
	if len(summaries) > 1 {
		return fmt.Errorf("multiple summary chunks require reconciliation")
	}
	if len(summaries) == 0 {
		c := &types.Chunk{ID: uuid.NewString(), TenantID: k.TenantID, KnowledgeID: k.ID, KnowledgeBaseID: k.KnowledgeBaseID, Content: k.Description, SourceContent: k.Description, ChunkIndex: maxIndex + 1, IsEnabled: true, ChunkType: types.ChunkTypeSummary, ParentChunkID: parent, CreatedAt: time.Now(), UpdatedAt: time.Now()}
		if err = s.chunkRepo.CreateChunks(ctx, []*types.Chunk{c}); err != nil {
			return err
		}
		summaries = []*types.Chunk{c}
	} else {
		c := summaries[0]
		c.Content = k.Description
		c.SourceContent = k.Description
		c.IsEnabled = true
		c.UpdatedAt = time.Now()
		if err = s.chunkRepo.UpdateChunk(ctx, c); err != nil {
			return err
		}
	}
	return s.updateChunkVector(ctx, k.KnowledgeBaseID, summaries)
}
