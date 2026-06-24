#include "engine/world.hpp"

namespace engine {
namespace core {

// Advance the whole simulation by exactly one frame: move every entity by its velocity so the
// world progresses one step in time. This is the single core entry point the scheduler calls
// each tick to advance the simulation.
void stepSimulation(World& world) {
    for (int i = 0; i < world.size(); ++i) {
        world.positions[i] = world.positions[i].add(world.velocities[i]);
    }
}

}  // namespace core
}  // namespace engine
