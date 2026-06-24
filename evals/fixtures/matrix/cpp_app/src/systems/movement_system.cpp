#include "engine/system.hpp"
#include "engine/world.hpp"

namespace engine {

// Moves entities each frame by integrating their motion through the shared physics primitive.
// One of the ISystem extension implementations registered with the scheduler.
struct MovementSystem : ISystem {
    void update(World& world) override {
        for (int i = 0; i < world.size(); ++i) {
            engine::physics::integrateMotion(world, i);
        }
    }
};

}  // namespace engine
