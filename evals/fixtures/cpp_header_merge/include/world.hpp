// World container INTERFACE. Every member's Doxygen doc lives HERE on the declaration;
// the definitions are out-of-line in src/world.cpp with NO comments at all. This is the
// classic C++ header/cpp split that Step 8 (header->cpp doc merge) targets.
//
// The TARGET (`queueDeath`) describes its behaviour with words that appear in NEITHER its
// name NOR its signature NOR src/world.cpp ("defers", "destruction", "frame", "iterating",
// "mid-pass"). The DECOYS carry the query's words in their NAMES instead, so WITHOUT the
// merge they out-rank the real target (the deliberate "buried target" setup, mirroring
// cpp_grid's `.size()` collapse). The merge moves the target's header doc onto the .cpp
// definition's descriptor, which lifts it back to the top.
//
// This header is git-ignored from the fixture index (.pandemoniumignore) so the ONLY
// indexed carrier of these words is the .cpp definition itself — isolating the merge. The
// indexer still reads it off disk via the src/ <-> include/ sibling lookup.
#pragma once
#include <cstddef>
#include <vector>

namespace sim {

using EntityId = unsigned int;

class World {
public:
    /// Defers an entity's destruction until the current frame has fully completed, so the
    /// systems iterating over the world are never mutated in the middle of a pass.
    void queueDeath(EntityId id);

    /// Finalizes and presents the rendered frame to the display swap chain.
    void completeFrame();

    /// Advances the simulation by exactly one frame and ticks every subsystem.
    void stepFrame();

    /// Releases the buffers held by the renderer's destruction queue.
    void flushDestructionQueue();

    /// Destroys every entity at once and frees all backing storage immediately.
    void destroyAll();

    /// Number of entities currently alive in the world.
    std::size_t livingCount() const;

private:
    std::vector<EntityId> doomed_;
    std::size_t alive_;
};

}  // namespace sim
