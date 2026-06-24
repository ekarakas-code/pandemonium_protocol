#pragma once

#include "engine/entity.hpp"
#include "engine/vec2.hpp"

namespace engine {

// The simulation world: the container of every entity and its kinematic state. Systems read
// and mutate the World each frame, and the core step function advances it by one tick.
struct World {
    static constexpr int kMaxEntities = 1024;
    int entityCount{0};
    Vec2 positions[kMaxEntities];
    Vec2 velocities[kMaxEntities];

    EntityId spawn(const Vec2& pos) {
        EntityId id = static_cast<EntityId>(entityCount);
        positions[entityCount] = pos;
        ++entityCount;
        return id;
    }
    int size() const { return entityCount; }
};

}  // namespace engine
