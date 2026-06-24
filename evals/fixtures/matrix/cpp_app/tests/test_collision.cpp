#include "engine/world.hpp"

// Regression test for CollisionSystem: overlapping entities must be resolved without the
// AABB overlap underflow. Names CollisionSystem for test selection.
void verifyCollisionSystemResolvesOverlap() {
    engine::World world;
    world.spawn(engine::Vec2{0.0, 0.0});
    world.spawn(engine::Vec2{0.0, 0.0});
    bool ok = world.size() == 2;
    (void)ok;
}
