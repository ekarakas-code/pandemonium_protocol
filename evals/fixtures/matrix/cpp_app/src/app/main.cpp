#include "engine/world.hpp"

// Entry point: build a world, seed an entity, and advance the simulation for a fixed number
// of frames by driving the per-frame scheduler.
int main() {
    engine::World world;
    world.spawn(engine::Vec2{0.0, 0.0});
    for (int frame = 0; frame < 100; ++frame) {
        engine::core::runFrame(world);
    }
    return 0;
}
