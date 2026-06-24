#pragma once

namespace engine {

struct World;

// Extension point: implement ISystem::update to add a new per-frame behavior to the engine.
// Register an implementation with the scheduler and its update hook runs every tick. This is
// the interface to subclass when adding new simulation behavior.
struct ISystem {
    virtual ~ISystem() = default;
    // Advance this system's effect on the world by one frame.
    virtual void update(World& world) = 0;
};

}  // namespace engine
