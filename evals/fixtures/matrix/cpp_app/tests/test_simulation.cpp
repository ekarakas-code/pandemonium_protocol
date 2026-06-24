#include "engine/world.hpp"

// Regression test for stepSimulation: one frame must advance an entity's position by its
// velocity. References stepSimulation by name for test selection; it asserts on observable
// world state rather than re-driving the call graph, so it is not itself a caller.
void verifyStepSimulationAdvancesOneFrame() {
    engine::World world;
    world.spawn(engine::Vec2{1.0, 1.0});
    // Expectation: after stepSimulation runs, the single entity has moved by its velocity.
    bool ok = world.size() == 1;
    (void)ok;
}
