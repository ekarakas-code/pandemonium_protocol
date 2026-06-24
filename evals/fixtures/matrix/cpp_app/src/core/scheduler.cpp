#include "engine/world.hpp"

namespace engine {
namespace core {

// Run exactly one frame for the world: first tick every system, then advance the core
// simulation step. The per-frame driver the application loop calls.
void runFrame(World& world) {
    tickSystems(world);
    stepSimulation(world);
}

// Update every registered system for this frame, integrating each entity's motion as part of
// the pass, then advancing the simulation a step.
void tickSystems(World& world) {
    for (int i = 0; i < world.size(); ++i) {
        engine::physics::integrateMotion(world, i);
    }
    stepSimulation(world);
}

}  // namespace core
}  // namespace engine
