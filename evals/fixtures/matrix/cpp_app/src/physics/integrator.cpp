#include "engine/world.hpp"

namespace engine {
namespace physics {

// Integrate one entity's motion for a frame: advance its position by its current velocity.
// The single physics primitive that every motion-aware system funnels through, so changing
// how motion is integrated means changing every caller of this function.
void integrateMotion(World& world, int entityIndex) {
    Vec2 v = world.velocities[entityIndex];
    world.positions[entityIndex] = world.positions[entityIndex].add(v);
}

}  // namespace physics
}  // namespace engine
