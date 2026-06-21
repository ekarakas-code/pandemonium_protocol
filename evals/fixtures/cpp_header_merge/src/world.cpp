// Out-of-line definitions for sim::World. BARE — no doc comments (the docs live in
// include/world.hpp). Nothing here mentions "defers", "destruction", "frame", "iterating"
// or "mid-pass": the target definition's ONLY route to those words is the header->cpp doc
// merge (Step 8). Every method is defined out-of-line (`World::method`), the case the
// nested-namespace fix did not cover.
#include "world.hpp"

namespace sim {

void World::queueDeath(EntityId id) {
    doomed_.push_back(id);
}

void World::completeFrame() {
}

void World::stepFrame() {
}

void World::flushDestructionQueue() {
    doomed_.clear();
}

void World::destroyAll() {
    doomed_.clear();
    alive_ = 0;
}

std::size_t World::livingCount() const {
    return alive_;
}

}  // namespace sim
